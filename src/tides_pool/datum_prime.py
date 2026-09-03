"""Minimal DATUM Prime server (Ocean Gateway pool_host side).

Implements handshake + configure (0x99) + coinbaser (0x11) + share ack (0x8F)
enough for lab Gateway → tides-pool share accounting + TIDES coinbase suggestions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from nacl.public import Box, PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError, CryptoError

from tides_pool.addresses import address_to_script, is_valid_payout_address
from tides_pool.config import Settings, finder_credit_bps, miner_reward_bps
from tides_pool.bitcoin_rpc import BitcoinRPC
from tides_pool.store import Store
from tides_pool.tides import Share, coinbase_suggestion, split_reward


log = logging.getLogger("tides_pool.datum_prime")

AcceptShareCb = Callable[[str, int, str | None], Awaitable[None]]

# Good attempt "why" values that count toward rehab / probation streaks.
_GOOD_ATTEMPT_WHY = frozenset({"ok", "rehab-good", "probation-good"})

# DATUM reject reason codes (protocol)
DATUM_REJECT_BAD_COINBASE_ID = 11
DATUM_REJECT_BAD_USERNAME = 14
DATUM_REJECT_BAD_COINBASE_OUTPUTS = 27
DATUM_REJECT_OTHER = 30

DATUM_POW_ACCEPTED = 0x50
DATUM_POW_REJECTED = 0x66

# 0x27 flags
FLAG_IS_BLOCK = 0x01
FLAG_SUBSIDY_ONLY = 0x02


class HandshakeError(RuntimeError):
    """Non-DATUM / bad first packet — common internet probe noise."""


def script_for_address(addr: str, fallback_ops: str) -> bytes:
    try:
        return address_to_script(addr)
    except ValueError:
        log.warning("cannot encode %s — using ops address script", addr)
        return address_to_script(fallback_ops)


@dataclass
class _CachedWindow:
    cutoff_seq: int | None
    shares: list[Share]  # newest-first, window only
    block_diff: int
    finder: str
    finder_credit: int
    computed_at: float
    max_seq: int


class CoinbaserSplitCache:
    """Process-wide payout-window share cache + cheap coinbaser replies.

    - Full DB reload every `coinbaser_cache_seconds` (default 15) or on invalidate
      (new find / finder credit).
    - Accepted shares append into an incremental buffer between reloads.
    - `0x10` handlers rescale the cached window to the request `value` on a worker
      thread — they do not re-scan 50k rows on the asyncio loop.
    """

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._lock = asyncio.Lock()
        self._cached: _CachedWindow | None = None
        self._extras: list[Share] = []
        self._dirty = True
        self._refresh_task: asyncio.Task | None = None
        self._bg_task: asyncio.Task | None = None
        self._refresh_count = 0
        self._last_refresh_ms: float | None = None
        self._last_refresh_error: str | None = None
        # Coinbaser reply health (process lifetime + recent ring)
        self.gateway_sessions = 0
        self._reply_count = 0
        self._last_outs: int | None = None
        self._last_reply_ms: float | None = None
        self._outs_ring: deque[int] = deque(maxlen=100)
        self._latency_ring: deque[float] = deque(maxlen=100)

    def ttl(self) -> float:
        return float(getattr(self.settings, "coinbaser_cache_seconds", 15.0) or 15.0)

    def note_share(self, *, seq: int, address: str, work: int, fee_bps: int = 0) -> None:
        if work < 1 or not address:
            return
        self._extras.append(
            Share(seq=int(seq), address=address, work=int(work), fee_bps=int(fee_bps))
        )
        # Bound extras if a refresh is stuck; background reload will reset.
        if len(self._extras) > 50_000:
            self._extras = self._extras[-10_000:]
            self._dirty = True

    def invalidate(self, reason: str = "") -> None:
        self._dirty = True
        if reason:
            log.info("coinbaser cache invalidate: %s", reason)

    def is_fresh(self) -> bool:
        c = self._cached
        if c is None or self._dirty:
            return False
        return (time.monotonic() - c.computed_at) < self.ttl()

    def _merged_shares(self, c: _CachedWindow) -> list[Share]:
        if not self._extras:
            return list(c.shares)
        seen = {s.seq for s in c.shares}
        extra_new = [s for s in self._extras if s.seq not in seen and s.seq > (c.max_seq or 0)]
        if not extra_new:
            return list(c.shares)
        # extras were appended oldest→newest; window wants newest-first
        return list(reversed(extra_new)) + list(c.shares)

    async def refresh(self, *, force: bool = False) -> None:
        if not force and self.is_fresh():
            return
        async with self._lock:
            if not force and self.is_fresh():
                return
            t0 = time.monotonic()
            cutoff = await self.store.payout_window_cutoff_seq(self.settings.window_blocks)
            shares = await self.store.list_shares_after_cutoff(cutoff)
            diff_meta = await self.store.get_meta("block_difficulty", "1") or "1"
            try:
                block_diff = max(int(float(diff_meta)), 1)
            except ValueError:
                block_diff = 1
            finder, credit = await self.store.pending_finder_credit()
            max_seq = max((s.seq for s in shares), default=0)
            # Keep only extras newer than this snapshot
            self._extras = [s for s in self._extras if s.seq > max_seq]
            self._cached = _CachedWindow(
                cutoff_seq=cutoff,
                shares=shares,
                block_diff=block_diff,
                finder=finder or "",
                finder_credit=int(credit or 0),
                computed_at=time.monotonic(),
                max_seq=max_seq,
            )
            self._dirty = False
            self._refresh_count += 1
            self._last_refresh_ms = (time.monotonic() - t0) * 1000.0
            self._last_refresh_error = None
            log.info(
                "coinbaser cache refresh #%s shares=%s cutoff=%s max_seq=%s finder=%s credit=%s in %.3fs",
                self._refresh_count,
                len(shares),
                cutoff,
                max_seq,
                (finder or "")[:12],
                credit,
                time.monotonic() - t0,
            )

    def _kick_refresh(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            return

        async def _run() -> None:
            try:
                await self.refresh(force=True)
            except Exception as exc:  # noqa: BLE001
                self._last_refresh_error = str(exc)[:200]
                log.exception("coinbaser cache refresh failed")

        self._refresh_task = asyncio.create_task(_run())

    def _compute_outs_sync(
        self,
        shares: list[Share],
        value: int,
        *,
        block_diff: int,
        finder: str,
        finder_credit: int,
    ) -> list[dict]:
        tides = split_reward(
            shares,
            reward_sats=value,
            block_difficulty=block_diff,
            window_blocks=self.settings.window_blocks,
            miner_bps=miner_reward_bps(self.settings),
            min_output_sats=self.settings.min_output_sats,
            pool_ops_address=self.settings.pool_ops_address,
            cutoff_seq=None,  # shares already window-trimmed
            window_mode="pool_finds",
        )
        outs = coinbase_suggestion(
            tides,
            pool_ops_address=self.settings.pool_ops_address or "ops",
            finder_address=finder or "",
            finder_credit_sats=finder_credit,
            min_output_sats=self.settings.min_output_sats,
        )
        if not outs:
            outs = [{"address": self.settings.pool_ops_address, "sats": value, "kind": "ops"}]
        return outs

    async def build_outs(self, value: int) -> list[dict]:
        t0 = time.monotonic()
        if self._cached is None:
            await self.refresh(force=True)
        elif not self.is_fresh():
            self._kick_refresh()
        c = self._cached
        if c is None:
            # refresh failed — ops-only fallback
            outs = [{"address": self.settings.pool_ops_address, "sats": value, "kind": "ops"}]
            self.note_reply(n_outs=1, latency_ms=(time.monotonic() - t0) * 1000.0)
            return outs
        shares = self._merged_shares(c)
        outs = await asyncio.to_thread(
            self._compute_outs_sync,
            shares,
            int(value),
            block_diff=c.block_diff,
            finder=c.finder,
            finder_credit=c.finder_credit,
        )
        self.note_reply(
            n_outs=len(outs),
            latency_ms=(time.monotonic() - t0) * 1000.0,
        )
        return outs

    def note_reply(self, *, n_outs: int, latency_ms: float) -> None:
        self._reply_count += 1
        self._last_outs = int(n_outs)
        self._last_reply_ms = float(latency_ms)
        self._outs_ring.append(int(n_outs))
        self._latency_ring.append(float(latency_ms))

    def health_snapshot(self) -> dict:
        c = self._cached
        age = None if c is None else max(0.0, time.monotonic() - c.computed_at)
        lat = sorted(self._latency_ring)
        p99 = lat[int(round((len(lat) - 1) * 0.99))] if lat else None
        outs1 = sum(1 for n in self._outs_ring if n <= 1)
        return {
            "cache_fresh": self.is_fresh(),
            "cache_age_s": None if age is None else round(age, 2),
            "cache_ttl_s": self.ttl(),
            "cache_shares": 0 if c is None else len(c.shares),
            "cache_max_seq": None if c is None else c.max_seq,
            "refresh_count": self._refresh_count,
            "last_refresh_ms": None
            if self._last_refresh_ms is None
            else round(self._last_refresh_ms, 1),
            "last_refresh_error": self._last_refresh_error,
            "gateway_sessions": int(self.gateway_sessions),
            "replies": self._reply_count,
            "last_outs": self._last_outs,
            "last_reply_ms": None
            if self._last_reply_ms is None
            else round(self._last_reply_ms, 1),
            "outs1_recent": outs1,
            "outs_recent_n": len(self._outs_ring),
            "p99_reply_ms": None if p99 is None else round(p99, 1),
        }

    async def run_background(self) -> None:
        # Prime once, then periodic refresh
        try:
            await self.refresh(force=True)
        except Exception:  # noqa: BLE001
            log.exception("coinbaser cache initial refresh failed")
        while True:
            try:
                await asyncio.sleep(max(self.ttl(), 1.0))
                await self.refresh(force=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("coinbaser cache background refresh failed")

    def start_background(self) -> None:
        if self._bg_task and not self._bg_task.done():
            return
        self._bg_task = asyncio.create_task(self.run_background())


class QuarantineGuard:
    """In-memory attempt rings + throttled auto-Q checks.

    Clean miners: auto-Q evaluated every N attempts from the ring (no SQL stats).
    Hot miners (reject-27 in ring): evaluated every reject path.
    Quarantine / probation flags cached after first DB read.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        win = int(getattr(settings, "quarantine_reject27_window", 20) or 20)
        self._rings: dict[str, deque] = defaultdict(lambda: deque(maxlen=max(win, 20)))
        self._since_check: dict[str, int] = defaultdict(int)
        self._hot: set[str] = set()
        self._q: dict[str, dict | None] = {}
        self._probation_cleared: set[str] = set()
        self._probation_known: set[str] = set()

    def note_attempt(
        self,
        address: str,
        *,
        accepted: bool,
        reason_code: int = 0,
        why: str = "",
    ) -> None:
        addr = (address or "").strip()
        if not addr:
            return
        why_s = (why or "").strip() or "ok"
        self._rings[addr].appendleft((bool(accepted), int(reason_code), why_s))
        self._since_check[addr] += 1
        if int(reason_code) == DATUM_REJECT_BAD_COINBASE_OUTPUTS:
            self._hot.add(addr)

    def should_check_auto_q(self, address: str) -> bool:
        addr = (address or "").strip()
        if not addr:
            return False
        if addr in self._hot:
            return True
        every = int(getattr(self.settings, "quarantine_check_every_n", 10) or 10)
        if self._since_check[addr] >= every:
            self._since_check[addr] = 0
            return True
        return False

    def ring_stats(self, address: str, limit: int = 20) -> tuple[int, int]:
        items = list(self._rings.get(address, ()))[:limit]
        rej = sum(1 for acc, rc, _ in items if (not acc) and int(rc) == DATUM_REJECT_BAD_COINBASE_OUTPUTS)
        return rej, len(items)

    def consecutive_good(self, address: str, limit: int = 20) -> int:
        n = 0
        for acc, rc, why in list(self._rings.get(address, ()))[:limit]:
            if acc and int(rc) == 0 and why in _GOOD_ATTEMPT_WHY:
                n += 1
            else:
                break
        return n

    def cache_quarantine(self, address: str, q: dict | None) -> None:
        self._q[address] = q

    def get_cached_quarantine(self, address: str) -> tuple[bool, dict | None]:
        """Return (known, value). known=False means must hit DB."""
        if address in self._q:
            return True, self._q[address]
        return False, None

    def mark_probation_cleared(self, address: str) -> None:
        self._probation_known.add(address)
        self._probation_cleared.add(address)

    def get_cached_probation_cleared(self, address: str) -> tuple[bool, bool]:
        if address in self._probation_known:
            return True, address in self._probation_cleared
        return False, False

    def clear_hot_if_clean(self, address: str) -> None:
        rej, total = self.ring_stats(address, limit=20)
        if total >= 5 and rej == 0:
            self._hot.discard(address)


def header_xor_feedback(i: int) -> int:
    i &= 0xFFFFFFFF
    h = 0xB10CFEED
    k = i
    k = (k * 0xCC9E2D51) & 0xFFFFFFFF
    k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
    k = (k * 0x1B873593) & 0xFFFFFFFF
    h ^= k
    h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
    h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    h ^= 4
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h & 0xFFFFFFFF


def pack_header(
    cmd_len: int,
    *,
    proto_cmd: int,
    is_signed: bool = False,
    is_encrypted_pubkey: bool = False,
    is_encrypted_channel: bool = False,
) -> bytes:
    word = (
        (cmd_len & 0x3FFFFF)
        | ((1 if is_signed else 0) << 24)
        | ((1 if is_encrypted_pubkey else 0) << 25)
        | ((1 if is_encrypted_channel else 0) << 26)
        | ((proto_cmd & 0x1F) << 27)
    )
    return struct.pack("<I", word)


def unpack_header(raw: bytes) -> dict:
    word = struct.unpack("<I", raw[:4])[0]
    return {
        "cmd_len": word & 0x3FFFFF,
        "is_signed": bool((word >> 24) & 1),
        "is_encrypted_pubkey": bool((word >> 25) & 1),
        "is_encrypted_channel": bool((word >> 26) & 1),
        "proto_cmd": (word >> 27) & 0x1F,
        "raw": word,
    }


def xor_header(hdr: bytes, key: int) -> bytes:
    word = struct.unpack("<I", hdr[:4])[0] ^ (key & 0xFFFFFFFF)
    return struct.pack("<I", word)


def incr_nonce(nonce: bytearray) -> None:
    for i in range(0, 24, 4):
        limb = struct.unpack_from("<I", nonce, i)[0]
        limb = (limb + 1) & 0xFFFFFFFF
        struct.pack_into("<I", nonce, i, limb)
        if limb != 0:
            return


def derive_nonces(nk: int, client_session_ed_pk: bytes) -> tuple[bytearray, bytearray]:
    """Return (client_recv/server_send, client_send/server_recv) nonces."""
    x = (nk - 42) & 0xFFFFFFFF
    x ^= struct.unpack_from("<I", client_session_ed_pk, 7)[0]
    recv = bytearray(24)
    send = bytearray(24)
    for j in range(0, 24, 4):
        w = header_xor_feedback((x - 42) & 0xFFFFFFFF)
        struct.pack_into("<I", recv, j, w)
        struct.pack_into("<I", send, j, w ^ 0x57575757)
        x = (~w) & 0xFFFFFFFF
    return recv, send


@dataclass
class PoolKeys:
    sign_sk: SigningKey
    box_sk: PrivateKey

    @property
    def pubkey_hex(self) -> str:
        return (self.sign_sk.verify_key.encode() + self.box_sk.public_key.encode()).hex()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "ed25519_sk": self.sign_sk.encode().hex(),
                    "x25519_sk": self.box_sk.encode().hex(),
                    "pool_pubkey": self.pubkey_hex,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def load_or_create(cls, path: Path) -> "PoolKeys":
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                sign_sk=SigningKey(bytes.fromhex(data["ed25519_sk"])),
                box_sk=PrivateKey(bytes.fromhex(data["x25519_sk"])),
            )
        keys = cls(sign_sk=SigningKey.generate(), box_sk=PrivateKey.generate())
        keys.save(path)
        log.info("generated DATUM Prime keys → %s pubkey=%s", path, keys.pubkey_hex)
        return keys


class DatumPrimeSession:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        pool_keys: PoolKeys,
        settings: Settings,
        store: Store,
        on_share: AcceptShareCb | None = None,
        *,
        coinbaser_cache: CoinbaserSplitCache | None = None,
        quarantine_guard: QuarantineGuard | None = None,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.pool_keys = pool_keys
        self.settings = settings
        self.store = store
        self.on_share = on_share
        self.coinbaser_cache = coinbaser_cache or CoinbaserSplitCache(store, settings)
        self.qguard = quarantine_guard or QuarantineGuard(settings)
        self.send_hdr_key = 0
        self.recv_hdr_key = 0
        self.send_nonce = bytearray(24)
        self.recv_nonce = bytearray(24)
        self.box: Box | None = None
        self.session_sign: SigningKey | None = None
        self.configured = False
        self.coinbaser_id = 1
        # coinbaser_id → {"n_value_outs": int} for recent assignments (lag-tolerant)
        self.recent_coinbasers: dict[int, dict] = {}

    def _remember_coinbaser(self, cid: int, n_value_outs: int) -> None:
        self.recent_coinbasers[cid & 0xFF] = {"n_value_outs": int(n_value_outs)}
        # keep a small ring so lagged Gateways still validate
        while len(self.recent_coinbasers) > 12:
            oldest = next(iter(self.recent_coinbasers))
            self.recent_coinbasers.pop(oldest, None)

    def _assigned_multi_out(self) -> bool:
        """True if any recent coinbaser had ≥2 value payouts (fair split, not ops-only)."""
        return any(int(v.get("n_value_outs") or 0) >= 2 for v in self.recent_coinbasers.values())

    async def _get_quarantine_cached(self, address: str) -> dict | None:
        known, q = self.qguard.get_cached_quarantine(address)
        if known:
            return q
        q = await self.store.get_quarantine(address)
        self.qguard.cache_quarantine(address, q)
        return q

    async def _is_probation_cleared_cached(self, address: str) -> bool:
        known, cleared = self.qguard.get_cached_probation_cleared(address)
        if known:
            return cleared
        cleared = await self.store.is_probation_cleared(address)
        if cleared:
            self.qguard.mark_probation_cleared(address)
        else:
            self.qguard._probation_known.add(address)
        return cleared

    async def _consecutive_good_cached(self, address: str, *, need: int) -> int:
        """Prefer in-memory ring; seed from DB once if ring too short."""
        limit = max(need + 5, 20)
        ring_n = len(self.qguard._rings.get(address, ()))
        if ring_n >= need:
            return self.qguard.consecutive_good(address, limit=limit)
        # Seed from DB then recompute
        db_n = await self.store.consecutive_good_attempts(address, limit=limit)
        return db_n

    async def _maybe_quarantine(self, address: str, *, is_block: bool) -> bool:
        """Return True if address is (or becomes) quarantined — freeze new share credit."""
        if self.settings.quarantine_allowlisted(address):
            q = await self._get_quarantine_cached(address)
            if q:
                await self.store.clear_quarantine(address)
                self.qguard.cache_quarantine(address, None)
                log.warning(
                    "QUARANTINE CLEARED address=%s (allowlisted)", address
                )
            return False
        q = await self._get_quarantine_cached(address)
        if q:
            return True
        # Throttle: clean miners every N attempts; hot (r27) every time.
        if not self.qguard.should_check_auto_q(address):
            return False
        win = int(getattr(self.settings, "quarantine_reject27_window", 20) or 20)
        ratio = float(getattr(self.settings, "quarantine_reject27_ratio", 0.5) or 0.5)
        min_n = int(getattr(self.settings, "quarantine_reject27_min_samples", 3) or 3)
        rej, total = self.qguard.ring_stats(address, limit=win)
        # If ring is thin, fall back to SQL once to avoid under-quarantining.
        if total < min_n:
            rej, total = await self.store.recent_attempt_stats(address, limit=win)
        reason = None
        # Do NOT quarantine on a single bad block — only on sustained reject-27 rate.
        if total >= min_n and (rej / float(total)) >= ratio:
            reason = f"reject-27 rate {rej}/{total} over last {win} attempts"
        if reason:
            await self.store.set_quarantine(address, reason)
            self.qguard.cache_quarantine(address, {"reason": reason, "at": "now"})
            log.warning("QUARANTINE address=%s reason=%s", address, reason)
            return True
        self.qguard.clear_hot_if_clean(address)
        return False


    async def _read_exact(self, n: int) -> bytes:
        return await self.reader.readexactly(n)

    async def _send_raw(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def send_sealed(
        self,
        plaintext: bytes,
        *,
        proto_cmd: int,
        seal_to: PublicKey,
        sign_sk: SigningKey,
        hdr_key: int,
    ) -> int:
        sig = sign_sk.sign(plaintext).signature
        body = plaintext + sig
        ct = SealedBox(seal_to).encrypt(body)
        # PyNaCl SealedBox.encrypt returns ciphertext only (includes seal overhead)
        hdr = pack_header(
            len(ct),
            proto_cmd=proto_cmd,
            is_signed=True,
            is_encrypted_pubkey=True,
            is_encrypted_channel=False,
        )
        hdr = xor_header(hdr, hdr_key)
        new_key = header_xor_feedback(hdr_key)
        await self._send_raw(hdr + ct)
        return new_key

    async def send_channel(self, plaintext: bytes, *, signed: bool = False) -> None:
        assert self.box is not None and self.session_sign is not None
        body = plaintext
        if signed:
            body = plaintext + self.session_sign.sign(plaintext).signature
        # Box.encrypt(plaintext, nonce) → ciphertext including 16-byte MAC prefix in PyNaCl
        ct = self.box.encrypt(body, bytes(self.send_nonce)).ciphertext
        hdr = pack_header(
            len(ct),
            proto_cmd=5,
            is_signed=signed,
            is_encrypted_pubkey=False,
            is_encrypted_channel=True,
        )
        hdr = xor_header(hdr, self.send_hdr_key)
        self.send_hdr_key = header_xor_feedback(self.send_hdr_key)
        incr_nonce(self.send_nonce)
        await self._send_raw(hdr + ct)

    async def handshake(self) -> None:
        # First header XOR'd with initial client key
        hdr_raw = await self._read_exact(4)
        hdr_plain = xor_header(hdr_raw, 0xDC871829)
        h = unpack_header(hdr_plain)
        if h["proto_cmd"] != 1 or not h["is_encrypted_pubkey"]:
            raise HandshakeError(
                f"expected hello cmd1 sealed, got cmd={h.get('proto_cmd')} enc_pub={h.get('is_encrypted_pubkey')}"
            )
        ct = await self._read_exact(h["cmd_len"])
        try:
            opened = SealedBox(self.pool_keys.box_sk).decrypt(ct)
        except CryptoError as e:
            raise HandshakeError(f"hello seal open failed: {e}") from e
        if len(opened) < 64 + 128:
            raise HandshakeError("hello too short")
        msg, sig = opened[:-64], opened[-64:]
        client_lt_ed = msg[0:32]
        client_lt_x = msg[32:64]
        client_sess_ed = msg[64:96]
        client_sess_x = msg[96:128]
        # find 0xFE then nk
        try:
            fe = msg.index(0xFE, 128)
        except ValueError as e:
            raise HandshakeError("hello missing 0xFE") from e
        if fe + 5 > len(msg):
            raise HandshakeError("hello missing nk")
        nk = struct.unpack_from("<I", msg, fe + 1)[0]
        try:
            VerifyKey(client_lt_ed).verify(msg, sig)
        except BadSignatureError as e:
            raise HandshakeError("hello signature bad") from e

        # keys / nonces
        self.recv_hdr_key = header_xor_feedback(nk)  # client→server
        self.send_hdr_key = header_xor_feedback((~nk) & 0xFFFFFFFF)  # server→client
        client_recv, client_send = derive_nonces(nk, client_sess_ed)
        # server encrypts with client_recv; decrypts with client_send
        self.send_nonce = client_recv
        self.recv_nonce = client_send

        self.session_sign = SigningKey.generate()
        session_box = PrivateKey.generate()
        self.box = Box(session_box, PublicKey(client_sess_x))

        # handshake response plaintext
        motd = b"TIDES lab DATUM Prime\x00"
        pt = (
            client_lt_ed
            + client_lt_x
            + client_sess_ed
            + client_sess_x
            + self.session_sign.verify_key.encode()
            + session_box.public_key.encode()
            + motd
        )
        self.send_hdr_key = await self.send_sealed(
            pt,
            proto_cmd=2,
            seal_to=PublicKey(client_sess_x),
            sign_sk=self.pool_keys.sign_sk,
            hdr_key=self.send_hdr_key,
        )
        log.info("handshake OK nk=%08x client_ua=%r", nk, msg[128:fe])

        # 0x99 configure — pool ops / fee script
        script = script_for_address(self.settings.pool_ops_address, self.settings.pool_ops_address)
        tag = self.settings.coinbase_tag_primary.encode()[:32]
        cfg = bytearray()
        cfg.append(0x99)
        cfg.append(1)  # version
        cfg.append(len(script))
        cfg.extend(script)
        cfg.extend(struct.pack("<I", 0x71DE5001))  # prime_id
        cfg.append(len(tag))
        cfg.extend(tag)
        cfg.extend(struct.pack("<Q", max(int(self.settings.min_share_difficulty), 4)))
        cfg.extend(b"\x00\xfe")
        await self.send_channel(bytes(cfg), signed=True)
        self.configured = True
        log.info("sent 0x99 configure tag=%s vardiff_min=%s", tag.decode(), max(int(self.settings.min_share_difficulty), 4))

    async def handle_channel(self, plaintext: bytes) -> None:
        if not plaintext:
            return
        cmd = plaintext[0]
        if cmd == 0x10:
            await self._coinbaser(plaintext)
        elif cmd == 0x27:
            await self._pow(plaintext)
        else:
            log.debug("ignore mining subcmd 0x%02x (%d bytes)", cmd, len(plaintext))

    async def _coinbaser(self, msg: bytes) -> None:
        if len(msg) < 42:
            return
        value = struct.unpack_from("<Q", msg, 1)[0]
        # Cached window shares (7 confirmed finds + current); rescale to this value
        # on a worker thread. No LIMIT 50_000 scan on the event loop.
        outs = await self.coinbaser_cache.build_outs(value)

        blob = bytearray()
        sent_id = self.coinbaser_id & 0xFF
        blob.append(sent_id)
        self.coinbaser_id = (self.coinbaser_id % 250) + 1
        assigned = 0
        n_value_outs = 0
        detail: list[str] = []
        ops = self.settings.pool_ops_address
        for o in outs:
            sats = int(o["sats"])
            if sats <= 0:
                continue
            if assigned + sats > value:
                sats = value - assigned
            if sats <= 0:
                break
            addr = str(o.get("address") or ops)
            # Never emit an unencodable scriptPubKey. Invalid share usernames
            # (e.g. 'box2') are rejected at _pow; this is defense-in-depth so
            # any leftover junk folds into the ops output instead of a bad script.
            if not is_valid_payout_address(addr):
                log.warning(
                    "coinbaser: invalid payout %r → ops (%s sats)",
                    addr,
                    sats,
                )
                addr = ops
            script = script_for_address(addr, ops)
            blob.extend(struct.pack("<Q", sats))
            blob.append(len(script))
            blob.extend(script)
            assigned += sats
            n_value_outs += 1
            detail.append(f"{addr[:12]}…:{sats}")
            if assigned >= value:
                break
        if assigned == 0:
            script = script_for_address(ops, ops)
            blob.extend(struct.pack("<Q", value))
            blob.append(len(script))
            blob.extend(script)
            detail.append(f"{ops[:12]}…:{value}")
            n_value_outs = 1

        self._remember_coinbaser(sent_id, n_value_outs)

        resp = bytearray()
        resp.append(0x11)
        resp.extend(struct.pack("<Q", value))
        resp.extend(struct.pack("<I", len(blob)))
        resp.extend(blob)
        await self.send_channel(bytes(resp), signed=False)
        log.info(
            "coinbaser value=%s outs=%d assigned=%s id=%s [%s]",
            value,
            n_value_outs,
            assigned,
            sent_id,
            "; ".join(detail),
        )

    def _parse_pow_job_meta(self, msg: bytes, after_user: int) -> tuple[int | None, int | None]:
        """Best-effort height / coinbase_value from optional 0x01 TLV after username."""
        i = after_user
        height = None
        value = None
        while i < len(msg):
            tag = msg[i]
            i += 1
            if tag == 0xFE:
                break
            if tag == 0x01 and i + 68 <= len(msg):
                # prevhash32 + u16 + nbits4 + coinbaser_id + height u32 + value u64 + ...
                height = struct.unpack_from("<I", msg, i + 32 + 2 + 4 + 1)[0]
                value = struct.unpack_from("<Q", msg, i + 32 + 2 + 4 + 1 + 4)[0]
                break
            if tag == 0x02 and i + 5 <= len(msg):
                # coinbase blob — skip by declared lengths
                _cid = msg[i]
                c1 = struct.unpack_from("<H", msg, i + 1)[0]
                c2 = struct.unpack_from("<H", msg, i + 3)[0]
                i += 5 + c1 + c2
                continue
            break
        return height, value

    async def _note_block_found(
        self,
        *,
        finder: str,
        worker: str | None,
        height: int,
        reward_sats: int,
        difficulty: float,
        nonce: int,
    ) -> None:
        """Record a pending pool find; confirm/orphan later via chain_sync.

        Order matters: finder_credits.from_height / paid_in_height FK → blocks(height),
        so record the block row *before* marking prior credits paid or opening a new credit.
        Hash resolve requires the tip coinbase to look like ours (TIDES tag + ops,
        multi-out *or* ops-only manual), not merely getblockhash(height).
        """
        from tides_pool.block_confirm import (
            build_intended_payout_snapshot,
            coinbase_looks_like_ours,
            coinbase_value_sats,
            pool_coinbase_payout_mode,
            resolve_tides_block_near_height,
        )

        block_hash = f"pool-{height}-{finder[:8]}-{nonce:08x}"
        resolved_height = int(height)
        resolved_blk: dict | None = None
        try:
            rpc = BitcoinRPC(self.settings)
            for _ in range(20):
                found = resolve_tides_block_near_height(
                    rpc,
                    height=resolved_height,
                    tag_primary=self.settings.coinbase_tag_primary,
                    ops_address=self.settings.pool_ops_address,
                    scan=1,
                )
                if found:
                    resolved_height, block_hash = found
                    try:
                        resolved_blk = rpc.call("getblock", [block_hash, 2])
                    except Exception:
                        resolved_blk = None
                    break
                # Also accept exact height if coinbase already ours
                try:
                    hx = rpc.call("getblockhash", [int(resolved_height)])
                    if isinstance(hx, str) and len(hx) == 64:
                        blk = rpc.call("getblock", [hx, 2])
                        if coinbase_looks_like_ours(
                            blk,
                            tag_primary=self.settings.coinbase_tag_primary,
                            ops_address=self.settings.pool_ops_address,
                        ):
                            block_hash = hx
                            resolved_blk = blk
                            break
                except Exception:
                    pass
                await asyncio.sleep(0.5)
            else:
                log.warning(
                    "BLOCK FOUND height=%s no TIDES coinbase yet; keeping synthetic %s",
                    height,
                    block_hash,
                )
        except Exception as exc:  # noqa: BLE001
            log.warning("BLOCK FOUND hash resolve failed: %s", exc)

        # Prefer on-chain coinbase total (subsidy + fees) over TLV / subsidy-only estimate
        if resolved_blk is not None:
            actual = coinbase_value_sats(resolved_blk)
            if actual and actual > 0:
                if actual != int(reward_sats):
                    log.info(
                        "BLOCK FOUND reward from chain %s (was estimate %s)",
                        actual,
                        reward_sats,
                    )
                reward_sats = actual

        head_seq = await self.store.max_share_seq()
        payout_mode = "onchain_split"
        intended_json = None
        manual_note = None
        if resolved_blk is not None:
            mode = pool_coinbase_payout_mode(
                resolved_blk,
                tag_primary=self.settings.coinbase_tag_primary,
                ops_address=self.settings.pool_ops_address,
            )
            if mode:
                payout_mode = mode
            if mode == "ops_manual":
                manual_note = "Coinbase was ops-only; ops will pay miners manually"
                try:
                    intended_json = await build_intended_payout_snapshot(
                        self.store,
                        self.settings,
                        reward_sats=int(reward_sats),
                        share_head_seq=head_seq,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("intended payout snapshot failed: %s", exc)
        await self.store.record_block(
            height=resolved_height,
            block_hash=block_hash,
            difficulty=difficulty,
            reward_sats=reward_sats,
            finder_address=finder,
            status="pending",
            share_head_seq=head_seq,
            payout_mode=payout_mode,
            intended_payout_json=intended_json,
            manual_payout_note=manual_note,
        )
        await self.store.set_meta("last_height", str(resolved_height))
        # Mark only the oldest unpaid bonus (the one currently in coinbasers)
        paid_n = await self.store.mark_finder_credits_paid(resolved_height)
        bonus = reward_sats * finder_credit_bps(self.settings) // 10_000
        await self.store.open_finder_credit(resolved_height, finder, bonus)
        # Finder credit + window cutoff may change — force coinbaser reload.
        self.coinbaser_cache.invalidate(f"block found height={resolved_height}")
        self.coinbaser_cache._kick_refresh()
        log.info(
            "BLOCK FOUND finder=%s worker=%s height=%s hash=%s reward=%s mode=%s bonus_next=%s (marked_paid=%s, pending confirm)",
            finder,
            worker,
            resolved_height,
            block_hash[:16] if block_hash else "",
            reward_sats,
            payout_mode,
            bonus,
            paid_n,
        )

    async def _pow(self, msg: bytes) -> None:
        if len(msg) < 31:
            return
        # 0x27 job_id coinbase_id flags target_byte ntime nonce version en_len en[12] username…
        job_id = msg[1]
        coinbase_id = msg[2]
        flags = msg[3]
        is_block = bool(flags & FLAG_IS_BLOCK)
        subsidy_only = bool(flags & FLAG_SUBSIDY_ONLY) or coinbase_id == 0xFF
        target_byte = msg[4]
        nonce = struct.unpack_from("<I", msg, 9)[0]
        # username C-string at offset 30
        rest = msg[30:]
        nul = rest.find(b"\x00")
        username = (rest[:nul] if nul >= 0 else rest).decode("utf-8", errors="replace")
        address = username.split(".", 1)[0]
        worker = username.split(".", 1)[1] if "." in username else None

        def _reject(reason: int, why: str) -> None:
            log.warning(
                "share REJECT %s user=%r coinbase_id=%s flags=%02x nonce=%08x block=%s",
                why,
                username,
                coinbase_id,
                flags,
                nonce,
                is_block,
            )

        # DATUM/Ocean convention: stratum username must be a payout address.
        if not is_valid_payout_address(address):
            _reject(DATUM_REJECT_BAD_USERNAME, "bad payout address")
            resp = bytearray()
            resp.append(0x8F)
            resp.append(DATUM_POW_REJECTED)
            resp.extend(struct.pack("<H", DATUM_REJECT_BAD_USERNAME))
            resp.extend(struct.pack("<I", nonce))
            resp.append(target_byte & 0xFF)
            resp.append(job_id & 0xFF)
            await self.send_channel(bytes(resp), signed=False)
            return

        # When we assigned a multi-out split, refuse empty/type-0/subsidy-only work for credit.
        # Blake Gateways that mine coinbase[0] send coinbase_id=0 → zero shares until fixed.
        if self._assigned_multi_out() and (subsidy_only or coinbase_id == 0):
            _reject(DATUM_REJECT_BAD_COINBASE_OUTPUTS, "coinbase not multi-out")
            resp = bytearray()
            resp.append(0x8F)
            resp.append(DATUM_POW_REJECTED)
            resp.extend(struct.pack("<H", DATUM_REJECT_BAD_COINBASE_OUTPUTS))
            resp.extend(struct.pack("<I", nonce))
            resp.append(target_byte & 0xFF)
            resp.append(job_id & 0xFF)
            await self.send_channel(bytes(resp), signed=False)
            # Explicitly no finder credit even if is_block
            await self.store.record_share_attempt(
                address,
                accepted=False,
                reason_code=DATUM_REJECT_BAD_COINBASE_OUTPUTS,
                why="coinbase not multi-out",
                worker=worker,
                is_block=is_block,
            )
            self.qguard.note_attempt(
                address,
                accepted=False,
                reason_code=DATUM_REJECT_BAD_COINBASE_OUTPUTS,
                why="coinbase not multi-out",
            )
            await self._maybe_quarantine(address, is_block=is_block)
            return


        # Quarantine rehab: good multi-out shares can lift the freeze after N in a row.
        # Bad coinbase already returned above (reject 27, no finder).
        # Ops-sticky reasons (prefix "ops ") never auto-clear.
        q = await self._get_quarantine_cached(address)
        if q and str((q or {}).get("reason") or "").startswith("ops "):
            await self.store.record_share_attempt(
                address,
                accepted=False,
                reason_code=DATUM_REJECT_OTHER,
                why="ops quarantine hold (no rehab)",
                worker=worker,
                is_block=is_block,
            )
            self.qguard.note_attempt(
                address,
                accepted=False,
                reason_code=DATUM_REJECT_OTHER,
                why="ops quarantine hold (no rehab)",
            )
            _reject(DATUM_REJECT_OTHER, "ops quarantine hold")
            resp = bytearray()
            resp.append(0x8F)
            resp.append(DATUM_POW_REJECTED)
            resp.extend(struct.pack("<H", DATUM_REJECT_OTHER))
            resp.extend(struct.pack("<I", nonce))
            resp.append(target_byte & 0xFF)
            resp.append(job_id & 0xFF)
            await self.send_channel(bytes(resp), signed=False)
            return
        if q and self.settings.quarantine_allowlisted(address):
            await self.store.clear_quarantine(address)
            self.qguard.cache_quarantine(address, None)
            log.warning("QUARANTINE CLEARED address=%s (allowlisted)", address)
            q = None
        rehab_need = int(getattr(self.settings, "quarantine_rehab_shares", 5) or 5)
        if q:
            if not self._assigned_multi_out():
                await self.store.record_share_attempt(
                    address,
                    accepted=False,
                    reason_code=DATUM_REJECT_OTHER,
                    why="rehab-wait-multiout",
                    worker=worker,
                    is_block=is_block,
                )
                self.qguard.note_attempt(
                    address,
                    accepted=False,
                    reason_code=DATUM_REJECT_OTHER,
                    why="rehab-wait-multiout",
                )
                _reject(DATUM_REJECT_OTHER, "quarantine rehab waiting for multi-out job")
                resp = bytearray()
                resp.append(0x8F)
                resp.append(DATUM_POW_REJECTED)
                resp.extend(struct.pack("<H", DATUM_REJECT_OTHER))
                resp.extend(struct.pack("<I", nonce))
                resp.append(target_byte & 0xFF)
                resp.append(job_id & 0xFF)
                await self.send_channel(bytes(resp), signed=False)
                return
            await self.store.record_share_attempt(
                address,
                accepted=True,
                reason_code=0,
                why="rehab-good",
                worker=worker,
                is_block=is_block,
            )
            self.qguard.note_attempt(
                address, accepted=True, reason_code=0, why="rehab-good"
            )
            streak = await self._consecutive_good_cached(address, need=rehab_need)
            if streak < rehab_need:
                _reject(
                    DATUM_REJECT_OTHER,
                    f"quarantine rehab {streak}/{rehab_need} (good split; no credit yet)",
                )
                resp = bytearray()
                resp.append(0x8F)
                resp.append(DATUM_POW_REJECTED)
                resp.extend(struct.pack("<H", DATUM_REJECT_OTHER))
                resp.extend(struct.pack("<I", nonce))
                resp.append(target_byte & 0xFF)
                resp.append(job_id & 0xFF)
                await self.send_channel(bytes(resp), signed=False)
                return
            await self.store.clear_quarantine(address)
            self.qguard.cache_quarantine(address, None)
            log.warning(
                "QUARANTINE CLEARED address=%s after %s good multi-out shares",
                address,
                streak,
            )
            # fall through — credit this share

        # New-miner probation: do not assume good at first connect. No window credit
        # until N consecutive good multi-out shares (same N as rehab by default).
        probation_need = int(getattr(self.settings, "probation_good_shares", 5) or 5)
        if not await self._is_probation_cleared_cached(address):
            if not self._assigned_multi_out():
                await self.store.record_share_attempt(
                    address,
                    accepted=False,
                    reason_code=DATUM_REJECT_OTHER,
                    why="probation-wait-multiout",
                    worker=worker,
                    is_block=is_block,
                )
                self.qguard.note_attempt(
                    address,
                    accepted=False,
                    reason_code=DATUM_REJECT_OTHER,
                    why="probation-wait-multiout",
                )
                _reject(
                    DATUM_REJECT_OTHER,
                    "new-miner probation: waiting for multi-out job",
                )
                resp = bytearray()
                resp.append(0x8F)
                resp.append(DATUM_POW_REJECTED)
                resp.extend(struct.pack("<H", DATUM_REJECT_OTHER))
                resp.extend(struct.pack("<I", nonce))
                resp.append(target_byte & 0xFF)
                resp.append(job_id & 0xFF)
                await self.send_channel(bytes(resp), signed=False)
                return
            await self.store.record_share_attempt(
                address,
                accepted=True,
                reason_code=0,
                why="probation-good",
                worker=worker,
                is_block=is_block,
            )
            self.qguard.note_attempt(
                address, accepted=True, reason_code=0, why="probation-good"
            )
            streak = await self._consecutive_good_cached(address, need=probation_need)
            if streak < probation_need:
                _reject(
                    DATUM_REJECT_OTHER,
                    f"new-miner probation {streak}/{probation_need} (no credit yet)",
                )
                resp = bytearray()
                resp.append(0x8F)
                resp.append(DATUM_POW_REJECTED)
                resp.extend(struct.pack("<H", DATUM_REJECT_OTHER))
                resp.extend(struct.pack("<I", nonce))
                resp.append(target_byte & 0xFF)
                resp.append(job_id & 0xFF)
                await self.send_channel(bytes(resp), signed=False)
                return
            await self.store.clear_probation(address)
            self.qguard.mark_probation_cleared(address)
            log.warning(
                "PROBATION CLEARED address=%s after %s good multi-out shares",
                address,
                streak,
            )
            # fall through — credit this share


        after_user = 30 + (nul + 1 if nul >= 0 else len(rest)) + 4  # username + NUL + 4 reserved
        tlv_height, tlv_value = self._parse_pow_job_meta(msg, after_user)

        if target_byte == 0xFF:
            work = max(int(self.settings.min_share_difficulty), 4)
        else:
            work = 1 << int(target_byte)

        try:
            # Optional per-address work cap. multiplier<=0 → normal pool (full credit).
            cap = self.settings.address_work_cap()
            window = self.settings.address_work_cap_window_sec
            if cap > 0:
                used = await self.store.work_for_address_since(address, window)
                remaining = max(cap - used, 0)
                credit = min(work, remaining)
            else:
                used = 0
                credit = work
            if credit > 0:
                if self.on_share:
                    await self.on_share(address, credit, worker)
                    # Callback may not return seq; 15s background refresh catches up.
                else:
                    row = await self.store.append_share(
                        address, credit, worker=worker, fee_bps=0
                    )
                    self.coinbaser_cache.note_share(
                        seq=row.seq,
                        address=row.address,
                        work=row.work,
                        fee_bps=row.fee_bps,
                    )
            await self.store.record_share_attempt(
                address,
                accepted=True,
                reason_code=0,
                why="ok",
                worker=worker,
                is_block=is_block,
            )
            self.qguard.note_attempt(address, accepted=True, reason_code=0, why="ok")
            self.qguard.clear_hot_if_clean(address)
            status = DATUM_POW_ACCEPTED
            reason = 0
            if cap > 0 and credit < work:
                log.info(
                    "share OK (CAPPED) user=%s work=%s credited=%s used=%s/%s/%ss nonce=%08x cb_id=%s",
                    username,
                    work,
                    credit,
                    used,
                    cap,
                    window,
                    nonce,
                    coinbase_id,
                )
            else:
                log.info(
                    "share OK user=%s work=%s nonce=%08x cb_id=%s%s",
                    username,
                    work,
                    nonce,
                    coinbase_id,
                    " BLOCK" if is_block else "",
                )

            if is_block and address:
                # Prefer TLV job meta; else chain tip estimate
                raw_h = await self.store.get_meta("chain_height")
                height = tlv_height
                if height is None:
                    try:
                        height = int(raw_h) + 1 if raw_h else 0
                    except ValueError:
                        height = 0
                raw_r = await self.store.get_meta("reward_estimate")
                try:
                    reward = int(tlv_value) if tlv_value else int(raw_r or 0)
                except ValueError:
                    reward = int(tlv_value or 0)
                if reward <= 0:
                    reward = 50 * 100_000_000
                raw_d = await self.store.get_meta("block_difficulty", "1") or "1"
                try:
                    diff = float(raw_d)
                except ValueError:
                    diff = 1.0
                await self._note_block_found(
                    finder=address,
                    worker=worker,
                    height=height,
                    reward_sats=reward,
                    difficulty=diff,
                    nonce=nonce,
                )
        except Exception as exc:  # noqa: BLE001
            status = DATUM_POW_REJECTED
            reason = DATUM_REJECT_OTHER
            log.warning("share reject: %s", exc)

        resp = bytearray()
        resp.append(0x8F)
        resp.append(status)
        resp.extend(struct.pack("<H", reason))
        resp.extend(struct.pack("<I", nonce))
        resp.append(target_byte & 0xFF)
        resp.append(job_id & 0xFF)
        await self.send_channel(bytes(resp), signed=False)

    async def run(self) -> None:
        await self.handshake()
        peer = self.writer.get_extra_info("peername")
        log.info("DATUM Gateway connected from %s", peer)
        while True:
            hdr_x = await self._read_exact(4)
            hdr_p = xor_header(hdr_x, self.recv_hdr_key)
            self.recv_hdr_key = header_xor_feedback(self.recv_hdr_key)
            h = unpack_header(hdr_p)
            payload = await self._read_exact(h["cmd_len"])
            if h["is_encrypted_channel"]:
                assert self.box is not None
                try:
                    # ciphertext includes MAC; decrypt with current recv nonce
                    pt = self.box.decrypt(payload, bytes(self.recv_nonce))
                except CryptoError:
                    log.error("channel decrypt failed cmd=%s len=%s", h["proto_cmd"], h["cmd_len"])
                    return
                incr_nonce(self.recv_nonce)
                if h["is_signed"]:
                    if len(pt) < 64:
                        return
                    # ignore sig for server-bound? client signs rarely toward server
                    body, sig = pt[:-64], pt[-64:]
                    # not verifying client session sig for lab
                    pt = body
                if h["proto_cmd"] == 5:
                    await self.handle_channel(pt)
            elif h["proto_cmd"] == 1:
                log.debug("ping")
            else:
                log.debug("unhandled proto_cmd=%s sealed=%s", h["proto_cmd"], h["is_encrypted_pubkey"])


async def handle_client(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    pool_keys: PoolKeys,
    settings: Settings,
    store: Store,
    coinbaser_cache: CoinbaserSplitCache,
    quarantine_guard: QuarantineGuard,
) -> None:
    peer = writer.get_extra_info("peername")
    coinbaser_cache.gateway_sessions += 1
    sess = DatumPrimeSession(
        reader,
        writer,
        pool_keys,
        settings,
        store,
        coinbaser_cache=coinbaser_cache,
        quarantine_guard=quarantine_guard,
    )
    try:
        await sess.run()
    except HandshakeError as exc:
        # Internet probes / wrong protocol — no traceback spam
        log.debug("ignored non-DATUM probe from %s (%s)", peer, exc)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        log.info("DATUM Gateway disconnected %s", peer)
    except Exception:
        log.exception("DATUM Prime session error from %s", peer)
    finally:
        coinbaser_cache.gateway_sessions = max(0, coinbaser_cache.gateway_sessions - 1)
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def start_datum_prime(
    settings: Settings,
    store: Store,
    keys_path: Path,
) -> tuple[asyncio.AbstractServer, PoolKeys, CoinbaserSplitCache]:
    keys = PoolKeys.load_or_create(keys_path)
    coinbaser_cache = CoinbaserSplitCache(store, settings)
    quarantine_guard = QuarantineGuard(settings)
    coinbaser_cache.start_background()
    server = await asyncio.start_server(
        lambda r, w: handle_client(
            r, w, keys, settings, store, coinbaser_cache, quarantine_guard
        ),
        host=settings.host,
        port=settings.datum_prime_port,
    )
    socks = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    log.info(
        "DATUM Prime listening on %s pubkey=%s coinbaser_cache=%ss q_check_every=%s",
        socks,
        keys.pubkey_hex,
        coinbaser_cache.ttl(),
        int(getattr(settings, "quarantine_check_every_n", 10) or 10),
    )
    return server, keys, coinbaser_cache
