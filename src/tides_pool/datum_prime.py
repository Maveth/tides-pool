"""Minimal DATUM Prime server (Ocean Gateway pool_host side).

Implements handshake + configure (0x99) + coinbaser (0x11) + share ack (0x8F)
enough for lab Gateway → tides-pool share accounting + TIDES coinbase suggestions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import struct
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from nacl.public import Box, PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError, CryptoError

from tides_pool.addresses import address_to_script, is_valid_payout_address
from tides_pool.abw import (
    DATUM_CONFIG_FLAG_ABW_DISABLED,
    new_xor_key,
    pack_assignment_notice,
    pack_candidate_receipt,
    pack_candidate_release,
    pack_reveal,
    pack_share_response_abw,
    xor_key_hash,
)
from tides_pool.config import Settings, finder_credit_bps, miner_reward_bps
from tides_pool.bitcoin_rpc import BitcoinRPC
from tides_pool.store import Store
from tides_pool.tides import RewardLine, Share, TidesSplit, coinbase_suggestion, split_reward


log = logging.getLogger("tides_pool.datum_prime")

AcceptShareCb = Callable[[str, int, str | None], Awaitable[None]]

# Good attempt "why" values that count toward rehab / probation streaks.
_GOOD_ATTEMPT_WHY = frozenset({"ok", "rehab-good", "probation-good"})

# CONVOY / Luke tip Gateways need configure v3 + ABW disabled.
# Everyone else (Leo / InnerHat / MaVeTh packages) stays on classic v1.
# Marker sources (merged, first-seen wins):
#   1) env TIDES_CONFIGURE_V3_UA_SUBSTR (or built-in defaults)
#   2) hot-reload file (default /app/data/configure_v3_ua_markers.json) —
#      edit to add known githashes without restarting Prime (mtime-checked)
#   3) learned githashes persisted in meta `configure_v3_ua_markers`
_DEFAULT_V3_UA_MARKERS = ("b9ea7dc", "convoy", "3e252be", "7491a509")  # SV1 cbreuse + Leo PR10 packages
_V3_GIT_HASH_RE = re.compile(r"(?i)(?:^|/)([0-9a-f]{7,40})\+?$")
_META_V3_MARKERS = "configure_v3_ua_markers"
_DEFAULT_V3_MARKERS_FILE = "/app/data/configure_v3_ua_markers.json"
# Quick-fail window: CONVOY StartOS drops right after bad configure version.
# Ignore sub-0.5s drops (Prime restart / decrypt races) so we don't false-sticky.
_DIALECT_QUICK_FAIL_MIN_SEC = 0.5
_DIALECT_QUICK_FAIL_SEC = 12.0
# Ambiguous UA (UNKNOWN): allow a couple of sticky flips, then lock preference.
# Override: TIDES_DIALECT_MAX_FLIPS (default 2).
def _dialect_max_flips() -> int:
    try:
        return max(0, int(os.environ.get("TIDES_DIALECT_MAX_FLIPS", "2") or 2))
    except ValueError:
        return 2


def _env_v3_markers() -> tuple[str, ...]:
    raw = (os.environ.get("TIDES_CONFIGURE_V3_UA_SUBSTR") or "").strip()
    if not raw:
        return _DEFAULT_V3_UA_MARKERS
    return tuple(m.strip().lower() for m in raw.split(",") if m.strip())


def _v3_markers_file_path() -> Path:
    raw = (os.environ.get("TIDES_CONFIGURE_V3_UA_MARKERS_PATH") or "").strip()
    return Path(raw or _DEFAULT_V3_MARKERS_FILE)


def _parse_v3_markers_payload(data: object) -> tuple[str, ...]:
    """Accept JSON list or {\"markers\": [...]} / {\"githashes\": [...]}."""
    if isinstance(data, dict):
        raw_list = data.get("markers")
        if raw_list is None:
            raw_list = data.get("githashes")
        if raw_list is None:
            return ()
        data = raw_list
    if not isinstance(data, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in data:
        s = str(item).strip().lower()
        if len(s) < 4 or "unknown" in s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return tuple(out)


def _extract_ua_githash(ua: str) -> str | None:
    """Return git hash from UA path if present and not UNKNOWN_GIT_HASH."""
    u = (ua or "").strip()
    if not u:
        return None
    low = u.lower()
    if "unknown" in low:
        return None
    # common shapes: v0.4.1-beta/<hash>  or  .../<hash>+
    tail = low.rsplit("/", 1)[-1].rstrip("+")
    if re.fullmatch(r"[0-9a-f]{7,40}", tail):
        return tail
    m = _V3_GIT_HASH_RE.search(low)
    return m.group(1).lower() if m else None


class ConfigureDialectBook:
    """Choose configure v1 vs v3; learn CONVOY githashes; sticky probe for UNKNOWN UAs.

    - Known markers (defaults + env + file + learned githashes) → immediate v3.
    - File markers hot-reload via mtime (no Prime restart); path override:
      TIDES_CONFIGURE_V3_UA_MARKERS_PATH (default /app/data/configure_v3_ua_markers.json).
    - Ambiguous UA (e.g. UNKNOWN_GIT_HASH): default **v1** first (don't break Leo),
      then if peer quick-fails after configure, flip sticky IP preference to v3
      (and the reverse if v3 quick-fails) — **at most TIDES_DIALECT_MAX_FLIPS**
      (default 2), then settle/lock so reconnect flaps stop thrashing.
    - Success (coinbaser/share) settles immediately on that dialect.
    - When a sticky-v3 session actually works and UA has a real githash, add that
      hash to the known-v3 list (persisted) so future peers with that build skip probe.
    """

    def __init__(self) -> None:
        self._learned: set[str] = set()
        self._peer_pref: dict[str, str] = {}  # ip -> "v1" | "v3"
        self._peer_flips: dict[str, int] = {}  # ip -> sticky flip count
        self._peer_settled: set[str] = set()  # ips locked after success or max flips
        self._store: Store | None = None
        self._persist_task: asyncio.Task | None = None
        self._file_markers: tuple[str, ...] = ()
        self._file_mtime_ns: int | None = None
        self._file_missing_logged = False

    def bind_store(self, store: Store) -> None:
        self._store = store

    def _reload_file_markers_if_needed(self) -> None:
        """Cheap mtime check; re-read JSON only when the file changes."""
        path = _v3_markers_file_path()
        try:
            mtime_ns = path.stat().st_mtime_ns
        except FileNotFoundError:
            if self._file_mtime_ns is not None or self._file_markers:
                self._file_markers = ()
                self._file_mtime_ns = None
                log.info(
                    "configure dialect: markers file %s gone; cleared file markers",
                    path,
                )
            elif not self._file_missing_logged:
                self._file_missing_logged = True
                log.info(
                    "configure dialect: no markers file at %s (env/learned only)",
                    path,
                )
            return
        except OSError:
            return
        if self._file_mtime_ns == mtime_ns:
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            markers = _parse_v3_markers_payload(data)
        except Exception:  # noqa: BLE001
            log.exception("configure dialect: failed reading markers file %s", path)
            return
        prev = self._file_markers
        self._file_markers = markers
        self._file_mtime_ns = mtime_ns
        self._file_missing_logged = False
        if markers != prev:
            log.info(
                "configure dialect: loaded %d file v3 UA markers from %s %s",
                len(markers),
                path,
                list(markers)[:12],
            )

    async def load(self) -> None:
        self._reload_file_markers_if_needed()
        if self._store is None:
            return
        try:
            raw = await self._store.get_meta(_META_V3_MARKERS, "[]")
            data = json.loads(raw or "[]")
            if isinstance(data, list):
                for m in data:
                    s = str(m).strip().lower()
                    if len(s) >= 7 and "unknown" not in s:
                        self._learned.add(s)
            if self._learned:
                log.info(
                    "configure dialect: loaded %d learned v3 UA markers %s",
                    len(self._learned),
                    sorted(self._learned)[:12],
                )
        except Exception:  # noqa: BLE001
            log.exception("configure dialect: failed loading learned markers")

    def _schedule_persist(self) -> None:
        if self._store is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _run() -> None:
            try:
                assert self._store is not None
                await self._store.set_meta(
                    _META_V3_MARKERS, json.dumps(sorted(self._learned))
                )
            except Exception:  # noqa: BLE001
                log.exception("configure dialect: persist learned markers failed")

        if self._persist_task and not self._persist_task.done():
            return
        self._persist_task = loop.create_task(_run())

    def markers(self) -> tuple[str, ...]:
        # env/defaults + file (hot) + learned githashes (unique, stable order)
        self._reload_file_markers_if_needed()
        out: list[str] = []
        seen: set[str] = set()
        for m in (
            list(_env_v3_markers())
            + list(self._file_markers)
            + sorted(self._learned)
        ):
            if m and m not in seen:
                seen.add(m)
                out.append(m)
        return tuple(out)

    def ua_forces_v3(self, ua: str) -> bool:
        u = (ua or "").lower()
        if not u:
            return False
        return any(m in u for m in self.markers())

    def choose_v3(self, *, ip: str, ua: str) -> bool:
        if self.ua_forces_v3(ua):
            return True
        pref = self._peer_pref.get(ip or "")
        if pref == "v3":
            return True
        if pref == "v1":
            return False
        # Ambiguous (UNKNOWN etc.): start with v1 so existing Leo StartOS keeps working.
        return False

    def learn_v3_marker(self, marker: str, *, reason: str = "") -> None:
        m = (marker or "").strip().lower()
        if len(m) < 7 or "unknown" in m:
            return
        self._reload_file_markers_if_needed()
        if m in self._learned or m in _env_v3_markers() or m in self._file_markers:
            return
        self._learned.add(m)
        log.info(
            "configure dialect: learned known-v3 githash/marker %r%s",
            m,
            f" ({reason})" if reason else "",
        )
        self._schedule_persist()

    def note_success(self, *, ip: str, ua: str, configure_ver: str) -> None:
        """Session proved dialect (coinbaser/share). Reinforce + learn githash if v3."""
        if configure_ver == "v3":
            if ip:
                self._peer_pref[ip] = "v3"
                self._peer_settled.add(ip)
            gh = _extract_ua_githash(ua)
            if gh:
                self.learn_v3_marker(gh, reason="successful v3 session")
        elif configure_ver == "v1" and ip:
            # Succeeded on v1 → settle v1 (Leo StartOS / classic).
            self._peer_pref[ip] = "v1"
            self._peer_settled.add(ip)

    def note_disconnect(
        self,
        *,
        ip: str,
        ua: str,
        configured: bool,
        configure_ver: str,
        configure_at: float,
        dialect_ok: bool,
    ) -> None:
        if dialect_ok or not configured or not ip:
            return
        if not configure_ver:
            return
        age = time.monotonic() - float(configure_at or 0)
        if age < _DIALECT_QUICK_FAIL_MIN_SEC or age > _DIALECT_QUICK_FAIL_SEC:
            return
        # Already forced by marker — don't thrash
        if self.ua_forces_v3(ua) and configure_ver.startswith("v3"):
            return
        # Settled (success or max probes) — stop flipping on further reconnect flaps.
        if ip in self._peer_settled:
            return
        max_flips = _dialect_max_flips()
        flips = int(self._peer_flips.get(ip, 0) or 0)
        if flips >= max_flips:
            pref = self._peer_pref.get(ip) or (
                "v3" if configure_ver == "v1" else "v1"
            )
            self._peer_pref[ip] = pref
            self._peer_settled.add(ip)
            log.info(
                "configure dialect: peer %s settled on %s after %d probes (ua=%r)",
                ip,
                pref,
                flips,
                ua or "",
            )
            return
        if configure_ver == "v1":
            self._peer_pref[ip] = "v3"
            self._peer_flips[ip] = flips + 1
            settled_now = self._peer_flips[ip] >= max_flips
            if settled_now:
                self._peer_settled.add(ip)
            log.warning(
                "configure dialect: peer %s quick-fail after v1 (%.1fs, ua=%r) → sticky v3 next%s",
                ip,
                age,
                ua or "",
                " (settled)" if settled_now else f" (flip {self._peer_flips[ip]}/{max_flips})",
            )
        elif configure_ver == "v3":
            self._peer_pref[ip] = "v1"
            self._peer_flips[ip] = flips + 1
            settled_now = self._peer_flips[ip] >= max_flips
            if settled_now:
                self._peer_settled.add(ip)
            log.warning(
                "configure dialect: peer %s quick-fail after v3 (%.1fs, ua=%r) → sticky v1 next%s",
                ip,
                age,
                ua or "",
                " (settled)" if settled_now else f" (flip {self._peer_flips[ip]}/{max_flips})",
            )


def _ua_wants_configure_v3(ua: str, dialect: ConfigureDialectBook | None = None) -> bool:
    """Marker-only check (no sticky). Prefer ConfigureDialectBook.choose_v3 for sessions."""
    if dialect is not None:
        return dialect.ua_forces_v3(ua)
    u = (ua or "").lower()
    return any(m in u for m in _env_v3_markers())


def share_work_from_target_byte(
    target_byte: int,
    *,
    min_share_difficulty: float,
    work_ceiling: int,
) -> int:
    """Diff1 work units from PoT byte, clamped to ceiling."""
    if target_byte == 0xFF:
        work = max(int(min_share_difficulty), 4)
    elif 0 <= int(target_byte) <= 62:
        work = 1 << int(target_byte)
    else:
        work = 0
    return min(int(work), int(work_ceiling)) if work_ceiling > 0 else int(work)


def target_byte_allowed(
    target_byte: int,
    *,
    is_block: bool,
    min_tb: int,
    max_tb_share: int,
    max_tb_block: int,
) -> bool:
    """True if Gateway-claimed PoT is in the allowed band."""
    if target_byte == 0xFF:
        return True
    tb = int(target_byte)
    if tb < 0 or tb > 62:
        return False
    if tb < int(min_tb):
        return False
    lim = int(max_tb_block) if is_block else int(max_tb_share)
    return tb <= lim


def ntime_skew_ok(ntime: int, *, now: float, max_skew_sec: int) -> bool:
    """Reject absurd clocks; generous window so we don't false-reject ASICs."""
    try:
        nt = int(ntime)
    except (TypeError, ValueError):
        return False
    skew = int(max_skew_sec)
    # too far in the future
    if nt > int(now) + skew:
        return False
    # too old (2× skew back)
    if nt < int(now) - (skew * 2):
        return False
    return True


# DATUM reject reason codes (protocol)
DATUM_REJECT_BAD_COINBASE_ID = 11
DATUM_REJECT_BAD_TARGET = 13
DATUM_REJECT_BAD_USERNAME = 14
# Cohort nick for Pool SV1 / local-GW fee-peer miners (Contributors folder).
_STRATUM_ENDPOINT_NICK = "Stratum Endpoint"
DATUM_REJECT_BAD_NTIME = 23
DATUM_REJECT_BAD_COINBASE_OUTPUTS = 27
DATUM_REJECT_OTHER = 30

DATUM_POW_ACCEPTED = 0x50
DATUM_POW_REJECTED = 0x66

# 0x27 flags
FLAG_IS_BLOCK = 0x01
FLAG_SUBSIDY_ONLY = 0x02

# DATUM v3 session resume (CONVOY datum_protocol.c)
DATUM_PRIME_ID = 0x71DE5001
DATUM_RESUME_TOKEN_SIZE = 40
_DRS_MAGIC = b"DRS\x01"


def make_resume_token(prime_id: int = DATUM_PRIME_ID, opaque: bytes | None = None) -> bytes:
    """40-byte resume token: prime_id u64 LE + 32 opaque bytes."""
    tok = bytearray(DATUM_RESUME_TOKEN_SIZE)
    struct.pack_into("<Q", tok, 0, int(prime_id) & 0xFFFFFFFFFFFFFFFF)
    if opaque is None:
        tok[8:] = os.urandom(32)
    else:
        if len(opaque) != 32:
            raise ValueError("resume opaque must be 32 bytes")
        tok[8:] = opaque
    return bytes(tok)


def parse_hello_resume_token(msg: bytes, fe: int) -> bytes | None:
    """Extract resume token from decrypted hello after 0xFE marker.

    Layout after fe: nk(u32 LE) | DRS\\x01 | flag | [token 40 if flag==1] | pad
    Returns None if absent / flag 0 / truncated / unknown magic.
    """
    off = int(fe) + 1 + 4  # skip 0xFE + nk
    if off + 5 > len(msg):
        return None
    if msg[off : off + 4] != _DRS_MAGIC:
        return None
    flag = msg[off + 4]
    if flag == 0:
        return None
    if flag != 1:
        return None
    start = off + 5
    end = start + DATUM_RESUME_TOKEN_SIZE
    if end > len(msg):
        return None
    return bytes(msg[start:end])


def build_configure_v3_resume_fields(token: bytes) -> tuple[bytes, bytes]:
    """Return (prime_id_u64_le, token40) for v3 configure wire."""
    if len(token) != DATUM_RESUME_TOKEN_SIZE:
        raise ValueError("resume token must be 40 bytes")
    prime_id = struct.unpack_from("<Q", token, 0)[0]
    return struct.pack("<Q", prime_id), bytes(token)


class ResumeTokenBook:
    """Process-global issued resume tokens (survive session reconnect)."""

    def __init__(self, ttl_sec: float = 86_400.0) -> None:
        self._ttl = max(0.0, float(ttl_sec))
        self._by_token: dict[bytes, float] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        dead = [t for t, ts in self._by_token.items() if (now - ts) > self._ttl]
        for t in dead:
            self._by_token.pop(t, None)

    def resolve(
        self,
        peer_ip: str,
        requested: bytes | None,
        *,
        prime_id: int = DATUM_PRIME_ID,
    ) -> tuple[bytes, bool]:
        """Return (token_for_configure, resumed).

        If ``requested`` matches a still-valid issued token → echo it (resumed).
        Otherwise issue a fresh token (declined / first connect).
        ``peer_ip`` is reserved for logging/future binding; GW identity is the token.
        """
        _ = peer_ip
        now = time.monotonic()
        with self._lock:
            self._purge(now)
            req = bytes(requested) if requested else None
            if req and len(req) == DATUM_RESUME_TOKEN_SIZE and req in self._by_token:
                self._by_token[req] = now
                return req, True
            tok = make_resume_token(prime_id)
            self._by_token[tok] = now
            return tok, False


class HandshakeError(RuntimeError):
    """Non-DATUM / bad first packet — common internet probe noise."""


def script_for_address(addr: str, fallback_ops: str) -> bytes:
    try:
        return address_to_script(addr)
    except ValueError:
        log.warning("cannot encode %s — using ops address script", addr)
        return address_to_script(fallback_ops)


@dataclass
class _CachedSplit:
    """Frozen server-wide coinbaser result. 0x10 serves this until next calc."""

    cutoff_seq: int | None
    outs: list[dict]
    value: int
    window_work: int
    n_shares: int
    n_payees: int
    block_diff: int
    finder: str
    finder_credit: int
    computed_at: float
    max_seq: int
    mode: str  # full | incr+N | rotate | cold


class CoinbaserSplitCache:
    """One payout calc for the whole Prime — timer owned, frozen serve.

    - Every ``coinbaser_cache_seconds`` (~5s): fold new shares into the *current*
      era, recompute outs from (last ≤7 era totals + current), publish.
    - DATUM ``0x10``: return the frozen outs. No per-request split, no rescale.
    - Shares not yet in the cache are simply missing until the next tick (OK).
    - On pool find: snapshot cache for website/intended payout, rotate current
      into the era ring (drop oldest beyond 7), start a fresh current bucket.
      New work after the find is next-block work.
    """

    _DEFAULT_VALUE = 312_500_000  # subsidy fallback before first 0x10

    def __init__(self, store: Store, settings: Settings) -> None:
        self.store = store
        self.settings = settings
        self._pub_lock = asyncio.Lock()
        self._cached: _CachedSplit | None = None
        # Last N published coinbaser snaps (~5s each). Find/confirm match chain
        # against current or prior 1–2 so late cache ticks don't false needs_review.
        self._snap_ring: deque[dict] = deque(maxlen=8)
        # Era ring: completed block-periods in the payout window (oldest→newest).
        # Len ≤ window_blocks-1 (7 when window_blocks=8). Current is separate.
        self._eras: list[dict[str, int]] = []
        self._current: dict[str, int] = {}
        self._cutoff_seq: int | None = None
        self._current_since_seq: int = 0
        self._max_seq: int = 0
        self._n_shares: int = 0
        self._block_diff: int = 1
        self._finder: str = ""
        self._finder_credit: int = 0
        self._refresh_task: asyncio.Task | None = None
        self._bg_task: asyncio.Task | None = None
        self._refresh_count = 0
        self._last_refresh_ms: float | None = None
        self._last_refresh_error: str | None = None
        self._last_fetch_mode: str = "?"
        self._pending_value: int | None = None
        # Dedicated refresh lane (Postgres only).
        self._io_loop: asyncio.AbstractEventLoop | None = None
        self._io_thread: threading.Thread | None = None
        self._io_store: Store | None = None
        self._io_ready = threading.Event()
        self._io_failed = False
        self.dialect = ConfigureDialectBook()
        self.dialect.bind_store(store)
        # Hot-reloadable local GW skim (meta.runtime_fees); no Prime restart to change.
        from tides_pool.runtime_fees import RuntimeFees

        self.runtime_fees = RuntimeFees(settings, store)
        self.gateway_sessions = 0
        self._reply_count = 0
        self._last_outs: int | None = None
        self._last_reply_ms: float | None = None
        self._last_value: int | None = None
        self._last_outs_list: list[dict] = []
        self._outs_ring: deque[int] = deque(maxlen=100)
        self._latency_ring: deque[float] = deque(maxlen=100)
        self._bad_payout_total = 0
        self._bad_payout_ring: deque[tuple[float, str]] = deque(maxlen=200)
        self._bad_payout_names: dict[str, int] = defaultdict(int)
        self._gateway_sessions: dict[str, dict] = {}
        self._ua_handshakes: dict[str, int] = defaultdict(int)
        self._ua_r27: dict[str, int] = defaultdict(int)
        self._ua_bad_payout: dict[str, int] = defaultdict(int)
        # Local-GW skim community half: accrue on accept, flush on coinbaser tick.
        self._community_fee_accrued: int = 0
        self._community_fee_lock = threading.Lock()
        self._community_fee_flushes = 0
        self._community_fee_flushed_work = 0
        # Addresses already stamped Stratum Endpoint this process (SV1 fee-peer).
        self._sv1_stratum_nick_stamped: set[str] = set()
        # DATUM v3 resume tokens — shared across sessions so reconnect can echo.
        self.resume_tokens = ResumeTokenBook()
        # After a real tip change, GW rebuilds templates (new prevhash) and often
        # empty-blasts (cid=0) until multi-out coinbaser arrives. Short grace
        # accepts those non-block empties so ASICs keep hashing on the *new* tip.
        self._chain_height_seen: int = 0
        self._bestblockhash_seen: str = ""
        self._r27_tip_grace_until: float = 0.0
        self._r27_tip_grace_sec: float = float(
            getattr(settings, "r27_new_tip_grace_sec", 45) or 45
        )
        self._tip_task: asyncio.Task | None = None
        self._tip_last_error: str | None = None
        self._tip_polls: int = 0
        self._tip_changes: int = 0

    def ttl(self) -> float:
        return float(getattr(self.settings, "coinbaser_cache_seconds", 5.0) or 5.0)

    def note_chain_height(self, height: int, *, bestblockhash: str | None = None) -> bool:
        """Arm reject-27 empty-job grace when chain tip advances.

        Returns True when height and/or best-block hash changed (callers should
        invalidate coinbaser — templates need the new prevhash on every tip).
        """
        h = int(height or 0)
        bh = (bestblockhash or "").strip()
        changed = False
        if h > 0 and (self._chain_height_seen <= 0 or h > self._chain_height_seen):
            if self._chain_height_seen > 0 and h > self._chain_height_seen:
                grace = max(5.0, float(self._r27_tip_grace_sec))
                self._r27_tip_grace_until = time.monotonic() + grace
                log.info("new tip height=%s — r27 empty-job grace %.0fs", h, grace)
            self._chain_height_seen = h
            changed = True
        if bh and bh != self._bestblockhash_seen:
            if self._bestblockhash_seen:
                # Hash move without height bump (reorg) — still a new tip for prevhash.
                if not changed:
                    grace = max(5.0, float(self._r27_tip_grace_sec))
                    self._r27_tip_grace_until = time.monotonic() + grace
                    log.info(
                        "new tip hash=%s… (height=%s) — r27 empty-job grace %.0fs",
                        bh[:16],
                        h or self._chain_height_seen,
                        grace,
                    )
                changed = True
            self._bestblockhash_seen = bh
        return changed

    def in_new_tip_r27_grace(self) -> bool:
        return time.monotonic() < float(self._r27_tip_grace_until or 0.0)

    def invalidate_coinbaser_for_tip(self) -> None:
        """Drop frozen outs so the next 0x10 / refresh rebuilds for the new tip."""
        self._cached = None
        self._last_refresh_error = None

    async def run_tip_watcher(self) -> None:
        """Poll Knots for tip hash; on change arm grace + force coinbaser refresh.

        GW already sees tips via its node (prevhash). Prime must not lag on the
        15s chain_sync loop or we keep serving stale coinbaser across tips.
        """
        interval = float(getattr(self.settings, "tip_poll_seconds", 1.0) or 0.0)
        if interval <= 0:
            log.info("tip watcher disabled (tip_poll_seconds=%s)", interval)
            return
        interval = max(0.5, min(interval, 30.0))
        rpc = BitcoinRPC(self.settings)
        log.info("tip watcher started poll=%.1fs", interval)
        while True:
            try:
                await asyncio.sleep(interval)

                def _pull() -> tuple[int, str]:
                    info = rpc.getblockchaininfo()
                    return int(info.get("blocks") or 0), str(info.get("bestblockhash") or "")

                h, bh = await asyncio.to_thread(_pull)
                self._tip_polls += 1
                if not bh and h <= 0:
                    continue
                # First observation: seed only (no invalidate storm on boot).
                if not self._bestblockhash_seen and self._chain_height_seen <= 0:
                    self.note_chain_height(h, bestblockhash=bh)
                    continue
                if not self.note_chain_height(h, bestblockhash=bh):
                    continue
                self._tip_changes += 1
                self.invalidate_coinbaser_for_tip()
                try:
                    await self.store.set_meta("chain_height", str(int(h)))
                    if bh:
                        await self.store.set_meta("bestblockhash", bh)
                except Exception:  # noqa: BLE001
                    log.exception("tip watcher meta write failed")
                await self.refresh(force=True)
                self._tip_last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._tip_last_error = str(exc)[:200]
                log.warning("tip watcher poll failed: %s", exc)

    def note_community_fee(self, work: int) -> None:
        """Accrue community half of local-GW skim (flushed on coinbaser tick)."""
        w = int(work or 0)
        if w < 1:
            return
        with self._community_fee_lock:
            self._community_fee_accrued += w

    def community_fee_pending(self) -> int:
        with self._community_fee_lock:
            return int(self._community_fee_accrued)

    async def _flush_community_fee(self, store: Store) -> int:
        """Write accrued community skim to *live* miners with this-block work.

        Anti hit-and-run: only addresses with a recent *real* share (green/live,
        ~10m — same idea as the site activity dots) and work in the current
        unfinished block receive STRATUM FEE, proportional to that this-block
        work. Fee-worker rows (STRATUM FEE / OPERATION FEE) do not count as live.
        Hot path stays O(1); this runs on the coinbaser timer only.
        """
        with self._community_fee_lock:
            accrued = int(self._community_fee_accrued)
            if accrued < 1:
                return 0
            self._community_fee_accrued = 0
        ops = (self.settings.pool_ops_address or "").strip()
        worker = (
            getattr(self.settings, "local_work_fee_community_worker", None)
            or "STRATUM FEE"
        )
        ops_worker = (
            getattr(self.settings, "local_work_fee_worker", None) or "OPERATION FEE"
        )
        fee_workers = {
            str(worker).strip(),
            str(ops_worker).strip(),
            "STRATUM FEE",
            "OPERATION FEE",
        }
        # Prefer sync read — never await RuntimeFees.refresh() from a foreign loop.
        try:
            live_sec = int(self.runtime_fees.local_work_fee_community_live_sec_sync())
        except Exception:  # noqa: BLE001
            live_sec = int(
                getattr(self.settings, "local_work_fee_community_live_sec", 600) or 600
            )
        live_sec = max(60, min(live_sec, 3600))
        live_addrs: set[str] = set()
        try:
            recent = await store.list_share_rows_since(live_sec, limit=50_000)
            for r in recent:
                a = (getattr(r, "address", None) or "").strip()
                if not a or a == ops:
                    continue
                wn = (getattr(r, "worker", None) or "").strip()
                if wn in fee_workers:
                    continue
                live_addrs.add(a)
        except Exception:  # noqa: BLE001
            log.exception("community fee live-set query failed")
            with self._community_fee_lock:
                self._community_fee_accrued += accrued
            return 0
        # Weight by this unfinished block only (not full window eras).
        recip = {
            a: int(w)
            for a, w in self._current.items()
            if int(w) > 0 and a and a != ops and a in live_addrs
        }
        total_w = int(sum(recip.values()))
        if total_w < 1 or not recip:
            # Nobody green-with-this-block-work yet — hold until next tick.
            with self._community_fee_lock:
                self._community_fee_accrued += accrued
            return 0
        # Floor + largest remainder.
        parts: list[list] = []
        assigned = 0
        for addr, w in sorted(recip.items(), key=lambda kv: (-kv[1], kv[0])):
            base = (accrued * int(w)) // total_w
            frac = (accrued * int(w)) / total_w - base
            parts.append([addr, base, frac])
            assigned += base
        rem = accrued - assigned
        parts.sort(key=lambda x: (-x[2], x[0]))
        for i in range(max(rem, 0)):
            parts[i % len(parts)][1] += 1
        flushed = 0
        n_pay = 0
        for addr, amt, _frac in parts:
            amt_i = int(amt)
            if amt_i < 1:
                continue
            row = await store.append_share(addr, amt_i, worker=worker, fee_bps=0)
            self._current[addr] = self._current.get(addr, 0) + amt_i
            self._max_seq = max(int(self._max_seq or 0), int(row.seq))
            self._n_shares += 1
            flushed += amt_i
            n_pay += 1
        leftover = accrued - flushed
        if leftover > 0:
            with self._community_fee_lock:
                self._community_fee_accrued += leftover
        if flushed > 0:
            self._community_fee_flushes += 1
            self._community_fee_flushed_work += flushed
            log.info(
                "stratum community fee flush work=%s payees=%s live=%s pending=%s "
                "total_flushed=%s",
                flushed,
                n_pay,
                len(live_addrs),
                self.community_fee_pending(),
                self._community_fee_flushed_work,
            )
        return flushed

    def _era_cap(self) -> int:
        # window_blocks=8 → 7 completed finds of work + current
        return max(int(self.settings.window_blocks) - 1, 1)

    def note_share(self, *, seq: int, address: str, work: int, fee_bps: int = 0) -> None:
        """Shares do not trigger recalc — the timer owns the next calc."""
        return

    def invalidate(self, reason: str = "") -> None:
        """Pool find: keep frozen cache for website; rotate eras for next work."""
        if reason:
            log.info("coinbaser cache invalidate: %s", reason)
        self.rotate_after_find(share_head_seq=self._max_seq, reason=reason)

    @staticmethod
    def _outs_to_map(outs: list[dict]) -> dict[str, int]:
        m: dict[str, int] = {}
        for o in outs or []:
            a = str(o.get("address") or "").strip()
            v = int(o.get("sats") or o.get("value") or 0)
            if a and v > 0:
                m[a] = m.get(a, 0) + v
        return m

    def _entry_from_cached(self, c: _CachedSplit) -> dict:
        return {
            "cutoff_seq": c.cutoff_seq,
            "share_head_seq": int(c.max_seq or 0),
            "finder_address": c.finder or "",
            "finder_credit_sats": int(c.finder_credit or 0),
            "outputs": [dict(o) for o in c.outs],
            "window_work": int(c.window_work),
            "cache_value": int(c.value),
            "max_seq": int(c.max_seq or 0),
            "mode": c.mode,
            "refresh_n": int(self._refresh_count),
            "computed_at": float(c.computed_at),
        }

    def _push_snap_ring(self, c: _CachedSplit) -> None:
        if c is None or not c.outs:
            return
        self._snap_ring.append(self._entry_from_cached(c))

    def recent_snap_entries(self) -> list[dict]:
        """Oldest→newest copies of recent published coinbaser snaps."""
        return [dict(e) for e in self._snap_ring]

    def match_snap_to_chain(
        self,
        chain: dict[str, int],
        *,
        dust_ignore: int = 1000,
        entries: list[dict] | None = None,
    ) -> dict | None:
        """Return best recent snap whose outs match chain within dust (after rescale)."""
        from tides_pool.payment_verify import (
            classify_intended_vs_chain,
            rescale_payment_map,
        )

        chain_map = {a: int(v) for a, v in (chain or {}).items() if int(v) > 0}
        if not chain_map:
            return None
        target = sum(chain_map.values())
        ops = (self.settings.pool_ops_address or "").strip() or None
        ring = list(reversed(entries if entries is not None else list(self._snap_ring)))
        # Prefer newest first (current, then prior ticks).
        for e in ring:
            raw = self._outs_to_map(e.get("outputs") or [])
            if len(raw) < 2:
                continue  # not a full multi-out snap
            scaled = rescale_payment_map(raw, target, dust_addr=ops)
            kind, _d = classify_intended_vs_chain(
                scaled, chain_map, dust_ignore=dust_ignore
            )
            if kind == "ok":
                out = dict(e)
                out["outputs"] = [
                    {
                        "address": a,
                        "sats": int(s),
                        "value": int(s),
                        "kind": "ops" if ops and a == ops else "tides",
                    }
                    for a, s in sorted(scaled.items(), key=lambda kv: -kv[1])
                ]
                out["matched"] = True
                return out
        return None

    def snapshot_for_block(
        self,
        reward_sats: int,
        *,
        chain: dict[str, int] | None = None,
    ) -> dict | None:
        """Website / intended_payout: prefer recent cache snap matching chain.

        Coinbaser publishes ~every 5s from cache; the winning job may use the
        current or previous tick. Match chain (when known) against the ring,
        rescale outs to the real reward, and keep a couple candidates for confirm.
        """
        from tides_pool.payment_verify import rescale_payment_map

        reward = int(reward_sats or 0)
        ops = (self.settings.pool_ops_address or "").strip() or None
        matched = None
        source = "coinbaser_cache"
        if chain:
            matched = self.match_snap_to_chain(chain, dust_ignore=1000)
            if matched is not None:
                source = "coinbaser_cache_match"
        c = self._cached
        if matched is None:
            if c is None or not c.outs:
                return None
            matched = self._entry_from_cached(c)
            # Rescale current cache outs to chain/reward total
            raw = self._outs_to_map(matched.get("outputs") or [])
            if reward > 0 and raw:
                scaled = rescale_payment_map(raw, reward, dust_addr=ops)
                matched["outputs"] = [
                    {
                        "address": a,
                        "sats": int(s),
                        "value": int(s),
                        "kind": "ops" if ops and a == ops else "tides",
                    }
                    for a, s in sorted(scaled.items(), key=lambda kv: -kv[1])
                ]

        # Keep last few ring entries (compact) for confirm-time re-match
        recent = []
        for e in list(self._snap_ring)[-3:]:
            recent.append(
                {
                    "max_seq": e.get("max_seq"),
                    "cache_value": e.get("cache_value"),
                    "refresh_n": e.get("refresh_n"),
                    "outputs": [dict(o) for o in (e.get("outputs") or [])],
                }
            )
        return {
            "reward_sats": reward,
            "cutoff_seq": matched.get("cutoff_seq"),
            "share_head_seq": matched.get("share_head_seq") or matched.get("max_seq"),
            "finder_address": matched.get("finder_address") or "",
            "finder_credit_sats": int(matched.get("finder_credit_sats") or 0),
            "outputs": [dict(o) for o in (matched.get("outputs") or [])],
            "window_work": int(matched.get("window_work") or 0),
            "source": source,
            "cache_value": int(matched.get("cache_value") or 0),
            "recent_candidates": recent,
            "captured_at": time.time(),
        }

    def rotate_after_find(self, *, share_head_seq: int, reason: str = "") -> None:
        """Close current era into the ring; new shares become next-block work."""
        head = int(share_head_seq or self._max_seq or 0)
        if self._current:
            self._eras.append(dict(self._current))
        cap = self._era_cap()
        while len(self._eras) > cap:
            dropped = self._eras.pop(0)
            self._n_shares = max(0, self._n_shares)  # share count drifts; full rebuild fixes
            del dropped
        self._current = {}
        self._current_since_seq = head
        self._max_seq = max(self._max_seq, head)
        self._cutoff_seq = None  # force cutoff re-read on next refresh
        # Cheap in-memory outs for the *new* window (7 eras + empty current).
        # Frozen pre-rotate outs stay in self._cached until this publishes.
        try:
            outs = self._outs_from_eras(self._calc_value())
            snap = _CachedSplit(
                cutoff_seq=self._cutoff_seq,
                outs=outs,
                value=self._calc_value(),
                window_work=self._total_work(),
                n_shares=self._n_shares,
                n_payees=len(outs),
                block_diff=self._block_diff,
                finder=self._finder,
                finder_credit=0,  # prior credit marked paid; new credit opens after
                computed_at=time.monotonic(),
                max_seq=self._max_seq,
                mode="rotate",
            )
            self._cached = snap
            self._last_outs_list = [dict(o) for o in outs]
            self._last_fetch_mode = "rotate"
            try:
                self._push_snap_ring(snap)
            except Exception:  # noqa: BLE001
                pass
            log.info(
                "coinbaser era rotate head=%s eras=%s work=%s outs=%s reason=%s",
                head,
                len(self._eras),
                snap.window_work,
                len(outs),
                (reason or "")[:80],
            )
        except Exception:  # noqa: BLE001
            log.exception("coinbaser era rotate outs failed")
        self._kick_refresh()

    def is_fresh(self) -> bool:
        c = self._cached
        if c is None:
            return False
        return (time.monotonic() - c.computed_at) < self.ttl()

    def _calc_value(self) -> int:
        if self._pending_value and self._pending_value > 0:
            return int(self._pending_value)
        if self._last_value and self._last_value > 0:
            return int(self._last_value)
        if self._cached and self._cached.value > 0:
            return int(self._cached.value)
        return self._DEFAULT_VALUE

    def _total_work(self) -> int:
        total = 0
        for era in self._eras:
            total += sum(era.values())
        total += sum(self._current.values())
        return int(total)

    def _merged_weights(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for era in self._eras:
            for addr, w in era.items():
                if w:
                    out[addr] = out.get(addr, 0) + int(w)
        for addr, w in self._current.items():
            if w:
                out[addr] = out.get(addr, 0) + int(w)
        return out

    def _outs_from_eras(self, value: int) -> list[dict]:
        """O(payees) — never walks the share log."""
        weights = self._merged_weights()
        weight_sum = int(sum(weights.values()))
        window_work = weight_sum
        miner_bps = miner_reward_bps(self.settings)
        min_out = int(self.settings.min_output_sats or 0)
        ops_addr = self.settings.pool_ops_address or "ops"
        if weight_sum <= 0 or value <= 0:
            return [{"address": ops_addr, "sats": int(value), "kind": "ops"}]
        miner_budget = int(value) * int(miner_bps) // 10_000
        lines: list[RewardLine] = []
        assigned = 0
        for address, w in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
            if w <= 0:
                continue
            sats = (miner_budget * int(w)) // weight_sum
            if sats < min_out:
                continue
            lines.append(RewardLine(address=address, sats=sats, work=int(w)))
            assigned += sats
        dust = miner_budget - assigned
        ops = int(value) - miner_budget + max(dust, 0)
        tides = TidesSplit(
            window_work=window_work,
            miner_budget_sats=miner_budget,
            ops_sats=ops,
            dust_sats=max(dust, 0),
            lines=tuple(lines),
        )
        outs = coinbase_suggestion(
            tides,
            pool_ops_address=ops_addr,
            finder_address=self._finder or "",
            finder_credit_sats=int(self._finder_credit or 0),
            min_output_sats=min_out,
        )
        if not outs:
            outs = [{"address": ops_addr, "sats": int(value), "kind": "ops"}]
        return outs

    @staticmethod
    def _add_shares_to(bucket: dict[str, int], shares: list[Share]) -> tuple[int, int]:
        """Fold shares into an addr→work map. Returns (added_work, n)."""
        added = 0
        n = 0
        for s in shares:
            w = int(s.work or 0)
            if w < 1 or not s.address:
                continue
            bucket[s.address] = bucket.get(s.address, 0) + w
            added += w
            n += 1
        return added, n

    def _ensure_io_thread(self) -> None:
        if self._io_failed:
            return
        if self._io_thread is not None and self._io_thread.is_alive():
            return
        dsn = str(getattr(self.settings, "database_url", "") or "")
        if not dsn.startswith("postgresql"):
            return

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._io_loop = loop

            async def _setup() -> None:
                try:
                    from tides_pool.store import PostgresStore

                    st = PostgresStore(dsn)
                    await st.ensure_ready()
                    self._io_store = st
                    self._io_ready.set()
                    log.info("coinbaser refresh IO thread ready (separate Postgres pool)")
                except Exception:  # noqa: BLE001
                    self._io_failed = True
                    log.exception("coinbaser refresh IO thread setup failed — using main-loop refresh")
                    self._io_ready.set()

            loop.create_task(_setup())
            try:
                loop.run_forever()
            finally:
                try:
                    if self._io_store is not None:
                        loop.run_until_complete(self._io_store.close())
                except Exception:  # noqa: BLE001
                    pass
                loop.close()

        self._io_thread = threading.Thread(target=_run, name="coinbaser-io", daemon=True)
        self._io_thread.start()
        self._io_ready.wait(timeout=60)

    async def _load_meta(self, store: Store) -> tuple[int | None, int, str, int]:
        cutoff = await store.payout_window_cutoff_seq(self.settings.window_blocks)
        diff_meta = await store.get_meta("block_difficulty", "1") or "1"
        try:
            block_diff = max(int(float(diff_meta)), 1)
        except ValueError:
            block_diff = 1
        finder, credit = await store.pending_finder_credit()
        try:
            raw_h = await store.get_meta("chain_height")
            if raw_h is not None and str(raw_h).strip() != "":
                self.note_chain_height(int(raw_h))
        except Exception:  # noqa: BLE001
            pass
        return cutoff, block_diff, finder or "", int(credit or 0)

    async def _full_rebuild(self, store: Store) -> _CachedSplit:
        """Cold start / drift: one window load, bucket into ≤7 eras + current."""
        cutoff, block_diff, finder, credit = await self._load_meta(store)
        shares = await store.list_shares_after_cutoff(cutoff)
        conf = await store.list_confirmed_blocks(limit=max(int(self.settings.window_blocks), 1))
        # newest-first heads within window
        heads: list[int] = []
        for b in conf:
            h = b.share_head_seq
            if h is None:
                continue
            hi = int(h)
            if cutoff is not None and hi <= int(cutoff):
                break
            heads.append(hi)
        # Build era boundaries oldest→newest among the up-to-7 completed periods,
        # then current after newest head (or all shares if no heads).
        eras: list[dict[str, int]] = []
        current: dict[str, int] = {}
        n_shares = 0
        if not heads:
            _, n_shares = self._add_shares_to(current, shares)
            current_since = int(cutoff or 0)
        else:
            # heads newest-first → boundaries for completed eras between cutoff and newest
            # completed eras: (cutoff → heads[-1]], (heads[-1] → heads[-2]], ... (heads[1] → heads[0]]
            # current: seq > heads[0]
            ordered = list(reversed(heads))  # oldest head … newest head
            bounds = [int(cutoff or 0)] + ordered  # len = n_heads+1
            era_maps = [dict() for _ in range(len(ordered))]  # one per completed head gap
            for s in shares:
                seq = int(s.seq)
                w = int(s.work or 0)
                if w < 1 or not s.address:
                    continue
                n_shares += 1
                if seq > ordered[-1]:
                    current[s.address] = current.get(s.address, 0) + w
                    continue
                # find completed era: first bound i where bounds[i] < seq <= bounds[i+1]
                placed = False
                for i in range(len(ordered)):
                    lo = bounds[i]
                    hi = bounds[i + 1]
                    if seq > lo and seq <= hi:
                        era_maps[i][s.address] = era_maps[i].get(s.address, 0) + w
                        placed = True
                        break
                if not placed:
                    current[s.address] = current.get(s.address, 0) + w
            eras = [m for m in era_maps if m]
            # Cap to era_cap (keep newest completed eras)
            cap = self._era_cap()
            if len(eras) > cap:
                eras = eras[-cap:]
            current_since = int(ordered[-1])

        max_seq = max((int(s.seq) for s in shares), default=int(cutoff or 0))
        self._eras = eras
        self._current = current
        self._cutoff_seq = cutoff
        self._current_since_seq = current_since
        self._max_seq = max_seq
        self._n_shares = n_shares
        self._block_diff = block_diff
        self._finder = finder
        self._finder_credit = credit
        value = self._calc_value()
        outs = self._outs_from_eras(value)
        return _CachedSplit(
            cutoff_seq=cutoff,
            outs=outs,
            value=value,
            window_work=self._total_work(),
            n_shares=n_shares,
            n_payees=len(outs),
            block_diff=block_diff,
            finder=finder,
            finder_credit=credit,
            computed_at=time.monotonic(),
            max_seq=max_seq,
            mode="full",
        )

    async def _incr_refresh(self, store: Store) -> _CachedSplit:
        """Timer path: only new shares since max_seq → current; recompute outs."""
        cutoff, block_diff, finder, credit = await self._load_meta(store)
        # Cutoff moved (confirm advanced window) without a rotate — full rebuild.
        if self._cutoff_seq is not None and cutoff != self._cutoff_seq:
            return await self._full_rebuild(store)
        tip = int(self._max_seq or 0)
        newer = await store.list_shares_after_cutoff(tip) if tip > 0 else []
        added_work, n_new = self._add_shares_to(self._current, newer)
        if newer:
            self._max_seq = max(int(newer[0].seq), tip)
            self._n_shares += n_new
        self._cutoff_seq = cutoff
        self._block_diff = block_diff
        self._finder = finder
        self._finder_credit = credit
        value = self._calc_value()
        outs = self._outs_from_eras(value)
        mode = f"incr+{n_new}" if n_new else "incr+0"
        return _CachedSplit(
            cutoff_seq=cutoff,
            outs=outs,
            value=value,
            window_work=self._total_work(),
            n_shares=self._n_shares,
            n_payees=len(outs),
            block_diff=block_diff,
            finder=finder,
            finder_credit=credit,
            computed_at=time.monotonic(),
            max_seq=self._max_seq,
            mode=mode,
        )

    async def _fetch_snapshot(self, store: Store) -> _CachedSplit:
        # Full every 60th tick as drift guard, or when empty.
        do_full = self._cached is None or (
            self._refresh_count > 0 and self._refresh_count % 60 == 0
        )
        if do_full or not self._eras and not self._current and self._cached is None:
            snap = await self._full_rebuild(store)
        elif not self._eras and not self._current and self._max_seq <= 0:
            snap = await self._full_rebuild(store)
        else:
            snap = await self._incr_refresh(store)
        # Community fee flush MUST NOT run on the IO thread: RuntimeFees.refresh()
        # and the main Postgres pool are bound to the Prime asyncio loop. Doing it
        # here via run_coroutine_threadsafe → "Future attached to a different loop"
        # and aborts the refresh (coinbaser timeouts → Gateway 0-out / r27).
        # Flush happens on the main loop in refresh() after the IO snapshot returns.
        return snap

    def _publish_snapshot(self, snap: _CachedSplit, *, elapsed_ms: float) -> None:
        self._cached = snap
        self._refresh_count += 1
        self._last_refresh_ms = elapsed_ms
        self._last_refresh_error = None
        self._last_fetch_mode = snap.mode
        self._last_outs_list = [dict(o) for o in snap.outs]
        if snap.value > 0:
            self._last_value = int(snap.value)
        try:
            self._push_snap_ring(snap)
        except Exception:  # noqa: BLE001
            log.exception("coinbaser snap ring push failed")
        log.info(
            "coinbaser cache refresh #%s shares=%s payees=%s cutoff=%s max_seq=%s "
            "finder=%s credit=%s work=%s in %.3fs io=%s mode=%s ring=%s",
            self._refresh_count,
            snap.n_shares,
            snap.n_payees,
            snap.cutoff_seq,
            snap.max_seq,
            (snap.finder or "")[:12],
            snap.finder_credit,
            snap.window_work,
            elapsed_ms / 1000.0,
            "thread" if self._io_store is not None else "main",
            snap.mode,
            len(self._snap_ring),
        )

    async def refresh(self, *, force: bool = False) -> None:
        if not force and self.is_fresh():
            return
        t0 = time.monotonic()
        try:
            if (
                self._io_loop is not None
                and self._io_store is not None
                and not self._io_failed
                and self._io_loop.is_running()
            ):
                fut = asyncio.run_coroutine_threadsafe(
                    self._fetch_snapshot(self._io_store),
                    self._io_loop,
                )
                snap = await asyncio.wrap_future(fut)
            else:
                snap = await self._fetch_snapshot(self.store)
        except Exception as exc:  # noqa: BLE001
            self._last_refresh_error = str(exc)[:200]
            self._last_refresh_ms = (time.monotonic() - t0) * 1000.0
            log.exception("coinbaser cache refresh failed")
            return
        # Community fee flush is intentionally NOT on this hot path.
        # It was dominating refresh (7–30s via list_share_rows_since + N append_share)
        # while coinbaser replies must stay < Gateway timeout (~5s). Fee flush runs
        # on a slower background timer in run_background().
        async with self._pub_lock:
            if not force and self.is_fresh():
                return
            self._publish_snapshot(snap, elapsed_ms=(time.monotonic() - t0) * 1000.0)

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

    async def build_outs(self, value: int) -> list[dict]:
        """Serve frozen cache. Never recompute split on the request path."""
        t0 = time.monotonic()
        if value and int(value) > 0:
            self._pending_value = int(value)
        if self._cached is None:
            self._kick_refresh()
            deadline = time.monotonic() + 12.0
            while self._cached is None and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
            if self._cached is None:
                await self.refresh(force=True)
        # Stale is fine — timer refreshes; do not kick on every 0x10.
        c = self._cached
        if c is None or not c.outs:
            outs = [{"address": self.settings.pool_ops_address, "sats": int(value), "kind": "ops"}]
            self.note_reply(
                n_outs=1,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                value=int(value),
                outs_list=outs,
            )
            return outs
        outs = [dict(o) for o in c.outs]
        self.note_reply(
            n_outs=len(outs),
            latency_ms=(time.monotonic() - t0) * 1000.0,
            value=int(value),
            outs_list=outs,
        )
        try:
            asyncio.create_task(
                self._persist_web_snapshots(
                    value=int(c.value),
                    outs=outs,
                    window_work=int(c.window_work),
                    max_seq=int(c.max_seq) if c.max_seq is not None else None,
                )
            )
        except Exception:  # noqa: BLE001
            pass
        return outs

    async def _persist_web_snapshots(
        self,
        *,
        value: int,
        outs: list[dict],
        window_work: int,
        max_seq: int | None,
    ) -> None:
        try:
            payload = {
                "value": int(value),
                "outputs": [
                    {
                        "address": str(o.get("address") or ""),
                        "sats": int(o.get("sats") or 0),
                        "kind": str(o.get("kind") or "tides"),
                    }
                    for o in outs
                ],
                "window_work": int(window_work),
                "share_log_head_seq": max_seq,
                "updated_at": time.time(),
            }
            await self.store.set_meta("coinbaser_last_json", json.dumps(payload))
            await self.store.set_meta(
                "prime_health_json", json.dumps(self.health_snapshot())
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("persist web snapshots failed: %s", exc)

    def window_work(self) -> int:
        c = self._cached
        if c is not None:
            return int(c.window_work)
        return self._total_work()

    def last_prime_value(self) -> int | None:
        return self._last_value

    def last_outs_snapshot(self) -> list[dict]:
        return [dict(o) for o in self._last_outs_list]

    def note_reply(
        self,
        *,
        n_outs: int,
        latency_ms: float,
        value: int | None = None,
        outs_list: list[dict] | None = None,
    ) -> None:
        self._reply_count += 1
        self._last_outs = int(n_outs)
        self._last_reply_ms = float(latency_ms)
        self._outs_ring.append(int(n_outs))
        self._latency_ring.append(float(latency_ms))
        if value is not None and int(value) > 0:
            self._last_value = int(value)
            self._pending_value = int(value)
        if outs_list is not None:
            self._last_outs_list = [dict(o) for o in outs_list]

    def note_bad_payout_username(self, username: str) -> None:
        u = (username or "").strip()[:120] or "(empty)"
        now = time.time()
        self._bad_payout_total += 1
        self._bad_payout_ring.append((now, u))
        self._bad_payout_names[u] += 1
        if self._bad_payout_total == 1 or self._bad_payout_total % 50 == 0:
            top = sorted(self._bad_payout_names.items(), key=lambda kv: -kv[1])[:5]
            log.warning(
                "bad_payout_username tally=%s distinct=%s top=%s "
                "(likely Pool Pass Full Users + miner username without bc1…)",
                self._bad_payout_total,
                len(self._bad_payout_names),
                top,
            )

    def health_snapshot(self) -> dict:
        c = self._cached
        age = None if c is None else max(0.0, time.monotonic() - c.computed_at)
        lat = sorted(self._latency_ring)
        p99 = lat[int(round((len(lat) - 1) * 0.99))] if lat else None
        outs1 = sum(1 for n in self._outs_ring if n <= 1)
        now = time.time()
        recent_1h = [(t, u) for t, u in self._bad_payout_ring if now - t <= 3600]
        top = sorted(self._bad_payout_names.items(), key=lambda kv: -kv[1])[:8]
        return {
            "cache_fresh": self.is_fresh(),
            "cache_age_s": None if age is None else round(age, 2),
            "cache_ttl_s": self.ttl(),
            "cache_shares": 0 if c is None else int(c.n_shares),
            "cache_max_seq": None if c is None else c.max_seq,
            "cache_payees": 0 if c is None else int(c.n_payees),
            "cache_eras": len(self._eras),
            "refresh_count": self._refresh_count,
            "last_refresh_ms": None
            if self._last_refresh_ms is None
            else round(self._last_refresh_ms, 1),
            "last_refresh_error": self._last_refresh_error,
            "last_fetch_mode": self._last_fetch_mode,
            "gateway_sessions": int(self.gateway_sessions),
            "replies": self._reply_count,
            "last_outs": self._last_outs,
            "last_value": self._last_value,
            "last_reply_ms": None
            if self._last_reply_ms is None
            else round(self._last_reply_ms, 1),
            "outs1_recent": outs1,
            "outs_recent_n": len(self._outs_ring),
            "p99_reply_ms": None if p99 is None else round(p99, 1),
            "bad_payout_rejects_total": int(self._bad_payout_total),
            "bad_payout_rejects_1h": len(recent_1h),
            "bad_payout_distinct": len(self._bad_payout_names),
            "bad_payout_top": [{"user": u, "n": n} for u, n in top],
            "gateway_uas": self._gateway_ua_snapshot(),
            "ua_handshakes_top": [
                {"ua": u, "n": n}
                for u, n in sorted(self._ua_handshakes.items(), key=lambda kv: -kv[1])[:12]
            ],
            "ua_reject27_top": [
                {"ua": u, "n": n}
                for u, n in sorted(self._ua_r27.items(), key=lambda kv: -kv[1])[:8]
            ],
            "ua_bad_payout_top": [
                {"ua": u, "n": n}
                for u, n in sorted(self._ua_bad_payout.items(), key=lambda kv: -kv[1])[:8]
            ],
        }

    @staticmethod
    def _normalize_ua(raw: bytes | str | None) -> str:
        if raw is None:
            return ""
        if isinstance(raw, (bytes, bytearray)):
            s = bytes(raw).split(b"\x00", 1)[0].decode("utf-8", errors="replace")
        else:
            s = str(raw).split("\x00", 1)[0]
        return s.strip()[:96]

    def note_gateway_session(self, peer_key: str, *, ip: str, ua: str) -> None:
        ua_n = self._normalize_ua(ua) or "(empty)"
        self._gateway_sessions[peer_key] = {
            "ip": ip,
            "ua": ua_n,
            "connected_at": time.time(),
        }
        self._ua_handshakes[ua_n] += 1

    def drop_gateway_session(self, peer_key: str) -> None:
        self._gateway_sessions.pop(peer_key, None)

    def gateway_ua_for_peer(self, peer_key: str) -> str:
        row = self._gateway_sessions.get(peer_key) or {}
        return str(row.get("ua") or "")

    def note_reject_ua(self, ua: str, *, kind: str) -> None:
        ua_n = self._normalize_ua(ua) or "(unknown)"
        if kind == "r27":
            self._ua_r27[ua_n] += 1
        elif kind == "bad_payout":
            self._ua_bad_payout[ua_n] += 1

    def _gateway_ua_snapshot(self) -> list[dict]:
        rows = sorted(
            self._gateway_sessions.values(),
            key=lambda r: float(r.get("connected_at") or 0),
            reverse=True,
        )
        out = []
        for r in rows[:24]:
            out.append(
                {
                    "ip": r.get("ip"),
                    "ua": r.get("ua"),
                    "age_s": round(max(0.0, time.time() - float(r.get("connected_at") or 0)), 1),
                }
            )
        return out

    async def run_background(self) -> None:
        try:
            await self.refresh(force=True)
        except Exception:  # noqa: BLE001
            log.exception("coinbaser cache initial refresh failed")
        # Fee flush cadence: slower than coinbaser TTL. Accrual still happens on
        # every accepted skim share; we just don't block template serving on it.
        fee_every = float(
            getattr(self.settings, "local_work_fee_flush_seconds", 30) or 30
        )
        fee_every = max(15.0, fee_every)
        next_fee = time.monotonic() + fee_every
        while True:
            try:
                await asyncio.sleep(max(self.ttl(), 1.0))
                await self.refresh(force=True)
                if time.monotonic() >= next_fee:
                    next_fee = time.monotonic() + fee_every
                    try:
                        flushed = await self._flush_community_fee(self.store)
                        if flushed > 0:
                            # Publish updated outs so next 0x10 includes STRATUM FEE credits.
                            value = self._calc_value()
                            outs = self._outs_from_eras(value)
                            c = self._cached
                            if c is not None:
                                snap = _CachedSplit(
                                    cutoff_seq=c.cutoff_seq,
                                    outs=outs,
                                    value=value,
                                    window_work=self._total_work(),
                                    n_shares=self._n_shares,
                                    n_payees=len(outs),
                                    block_diff=c.block_diff,
                                    finder=c.finder,
                                    finder_credit=c.finder_credit,
                                    computed_at=time.monotonic(),
                                    max_seq=self._max_seq,
                                    mode=f"fee+cf{flushed}",
                                )
                                async with self._pub_lock:
                                    self._publish_snapshot(
                                        snap,
                                        elapsed_ms=0.0,
                                    )
                    except Exception:  # noqa: BLE001
                        log.exception("community fee background flush failed")
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("coinbaser cache background refresh failed")

    def start_background(self) -> None:
        if self._bg_task and not self._bg_task.done():
            return
        self._ensure_io_thread()

        async def _boot() -> None:
            try:
                await self.dialect.load()
            except Exception:  # noqa: BLE001
                log.exception("configure dialect load failed")
            # Tip watcher runs alongside coinbaser refresh (own task).
            if self._tip_task is None or self._tip_task.done():
                self._tip_task = asyncio.create_task(
                    self.run_tip_watcher(), name="tip-watcher"
                )
            await self.run_background()

        self._bg_task = asyncio.create_task(_boot())


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
        # address → True if last_nickname known non-empty (skip POW nick probes)
        self._nick_has: dict[str, bool] = {}
        # address → monotonic time of last blank-nick POW probe log (rate limit)
        self._nick_pow_probe_log_at: dict[str, float] = {}

    def should_log_nick_pow_probe(
        self, address: str, *, interval_sec: float = 1800.0
    ) -> bool:
        """True at most once per address per interval (blank-nick POW ascii dump)."""
        addr = (address or "").strip()
        if not addr:
            return False
        now = time.monotonic()
        last = float(self._nick_pow_probe_log_at.get(addr) or 0.0)
        if now - last < max(60.0, float(interval_sec)):
            return False
        self._nick_pow_probe_log_at[addr] = now
        return True

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

    def nick_status(self, address: str) -> tuple[bool, bool]:
        """Return (known, has_nickname). known=False → must ask DB."""
        addr = (address or "").strip()
        if addr in self._nick_has:
            return True, bool(self._nick_has[addr])
        return False, False

    def set_nick_status(self, address: str, has_nick: bool) -> None:
        addr = (address or "").strip()
        if addr:
            self._nick_has[addr] = bool(has_nick)

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


def cmd_len_allowed(cmd_len: int, max_len: int) -> bool:
    """True if header cmd_len is safe to buffer (0 = empty ping-style ok)."""
    try:
        n = int(cmd_len)
    except (TypeError, ValueError):
        return False
    return 0 <= n <= int(max_len)


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
        peer = writer.get_extra_info("peername")
        if isinstance(peer, tuple) and peer:
            self.peer_ip = str(peer[0])
            self.peer_port = int(peer[1]) if len(peer) > 1 else 0
        else:
            self.peer_ip = str(peer or "")
            self.peer_port = 0
        self.peer_key = f"{self.peer_ip}:{self.peer_port}"
        # Local listen port decides ABW-on (28926) vs unchanged public path (28916).
        sock = writer.get_extra_info("sockname")
        if isinstance(sock, tuple) and len(sock) > 1:
            self.listen_port = int(sock[1])
        else:
            self.listen_port = int(getattr(settings, "datum_prime_port", 0) or 0)
        self.client_ua = ""
        # Per-session share counter for sampled pow_check_every audits.
        self._pow_share_seq = 0
        # Configure dialect tracking (UNKNOWN sticky probe / learn githash)
        self._configure_at = 0.0
        self._configure_ver = ""  # "v1" | "v3"
        self._dialect_ok = False
        # Per-GW ABW state — only armed when this session's listen port is abw_prime_port
        # (or lab global abw_enable with no dual-port).
        self.abw_required = False
        self.abw_wire_slot = 0
        self.abw_xor_key = b""
        self.abw_key_hash = b""
        self._abw_rotate_task: asyncio.Task | None = None

    def _remember_coinbaser(self, cid: int, n_value_outs: int) -> None:
        self.recent_coinbasers[cid & 0xFF] = {"n_value_outs": int(n_value_outs)}
        # keep a small ring so lagged Gateways still validate
        while len(self.recent_coinbasers) > 24:
            oldest = next(iter(self.recent_coinbasers))
            self.recent_coinbasers.pop(oldest, None)

    def _assigned_multi_out(self) -> bool:
        """True if any recent coinbaser had ≥2 value payouts (fair split, not ops-only)."""
        return any(int(v.get("n_value_outs") or 0) >= 2 for v in self.recent_coinbasers.values())

    def _coinbase_id_ok(
        self,
        coinbase_id: int,
        *,
        subsidy_only: bool,
        is_block: bool = False,
    ) -> tuple[bool, str]:
        """When multi-out was assigned, refuse empty / subsidy-only / type-0xFF shares.

        Gateways may empty-blast (``coinbase_id=0``) or stick on reject-path jobs
        after tip / while waiting on coinbaser. Once this session has delivered a
        real multi-out split, keep rejecting those shapes so the GW asks for a
        fresh template — do **not** credit empty-job hashrate for minutes.

        Block finds on empty/subsidy-only are still accepted (``ops_manual``);
        reject is a nudge, not a lost find. Before any multi-out assignment,
        empty jobs remain valid share work. Returns (ok, why); why empty when ok.
        """
        if not self._assigned_multi_out():
            return True, ""
        cid = int(coinbase_id) & 0xFF
        # Empty / subsidy-only / type-0xFF after we assigned multi-out:
        # ACCEPT block finds; REJECT non-block so GW refreshes the template.
        # Exception: brief post-tip grace for cid=0 non-blocks — GW is on the
        # *new* prevhash already and waiting for multi-out coinbaser.
        if cid == 0 or subsidy_only or cid == 0xFF:
            if is_block:
                return True, ""
            if cid == 0:
                try:
                    cache = getattr(self, "coinbaser_cache", None)
                    if cache is not None and cache.in_new_tip_r27_grace():
                        return True, ""
                except Exception:  # noqa: BLE001
                    pass
                return False, "coinbase empty (multi-out assigned)"
            return False, "coinbase not multi-out"
        return True, ""

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

    async def _maybe_quarantine(
        self,
        address: str,
        *,
        is_block: bool,
        msg: bytes | None = None,
        after_user: int | None = None,
    ) -> bool:
        """Return True if address is (or becomes) quarantined — freeze new share credit.

        On the throttled check tick, also try learning a blank nickname from POW
        coinbase section 0x02 when present (never rejects a share for bad/missing nick).
        """
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
        if msg is not None and after_user is not None:
            await self._try_learn_nickname_from_pow(address, msg, after_user)
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
            # When Prime coinbaser is healthy (multi-out), r27 is almost always a
            # Gateway job race (coinbase_id=0), not a bad miner — skip auto-Q.
            if self._coinbaser_looks_healthy_for_auto_q():
                log.info(
                    "skip auto-Q address=%s reason=%s (coinbaser healthy outs=%s)",
                    address,
                    reason,
                    getattr(self.coinbaser_cache, "_last_outs", None),
                )
                self.qguard.clear_hot_if_clean(address)
                return False
            await self.store.set_quarantine(address, reason)
            self.qguard.cache_quarantine(address, {"reason": reason, "at": "now"})
            log.warning("QUARANTINE address=%s reason=%s", address, reason)
            return True
        self.qguard.clear_hot_if_clean(address)
        return False

    def _coinbaser_looks_healthy_for_auto_q(self) -> bool:
        """True when multi-out coinbaser is serving — don't auto-Q on job-race r27."""
        cb = self.coinbaser_cache
        if getattr(cb, "_last_refresh_error", None):
            return False
        outs = getattr(cb, "_last_outs", None)
        if outs is None:
            return False
        try:
            return int(outs) >= 2
        except (TypeError, ValueError):
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
        max_len = int(self.settings.datum_max_cmd_len)
        if not cmd_len_allowed(h["cmd_len"], max_len):
            raise HandshakeError(f"hello cmd_len {h['cmd_len']} exceeds max {max_len}")
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
        ua_raw = msg[128:fe]
        self.client_ua = CoinbaserSplitCache._normalize_ua(ua_raw)
        try:
            self.coinbaser_cache.note_gateway_session(
                self.peer_key, ip=self.peer_ip, ua=self.client_ua
            )
        except Exception:  # noqa: BLE001
            pass
        log.info(
            "handshake OK nk=%08x peer=%s client_ua=%r",
            nk,
            self.peer_key,
            self.client_ua or ua_raw,
        )
        requested_resume = parse_hello_resume_token(msg, fe)
        resume_token, resumed = self.coinbaser_cache.resume_tokens.resolve(
            self.peer_ip, requested_resume, prime_id=DATUM_PRIME_ID
        )
        if requested_resume is not None:
            log.info(
                "DATUM resume %s peer=%s token=%s…",
                "accepted" if resumed else "declined",
                self.peer_key,
                resume_token[:8].hex(),
            )

        # 0x99 configure — dual-speak + optional quiet ABW port:
        #   known CONVOY markers / learned githashes → v3
        #   UNKNOWN UA: sticky probe (v1 first, flip to v3 after quick-fail)
        #   CONVOY on abw_prime_port (e.g. 28926) → v3-abw-on + ACTIVE 0xA8
        #   CONVOY on public datum_prime_port (28916) → v3-abw-off
        script = script_for_address(self.settings.pool_ops_address, self.settings.pool_ops_address)
        tag = self.settings.coinbase_tag_primary.encode()[:32]
        prime_id = DATUM_PRIME_ID
        vardiff = max(int(self.settings.min_share_difficulty), 4)
        use_v3 = self.coinbaser_cache.dialect.choose_v3(
            ip=self.peer_ip, ua=self.client_ua or ""
        )
        abw_on = use_v3 and self._session_wants_abw()
        cfg = bytearray()
        cfg.append(0x99)
        if use_v3:
            if len(script) > 83:
                raise RuntimeError(
                    f"ops scriptPubKey too long for CONVOY cap ({len(script)} > 83)"
                )
            prime_id_bytes, resume = build_configure_v3_resume_fields(resume_token)
            cfg.append(3)
            cfg.append(len(script))
            cfg.extend(script)
            cfg.extend(prime_id_bytes)
            cfg.extend(resume)
            cfg.append(len(tag))
            cfg.extend(tag)
            cfg.extend(struct.pack("<Q", vardiff))
            if abw_on:
                cfg.append(0x00)  # ABW required
                ver_label = "v3-abw-on"
            else:
                cfg.append(DATUM_CONFIG_FLAG_ABW_DISABLED)
                ver_label = "v3-abw-off"
            cfg.append(0xFE)
        else:
            cfg.append(1)
            cfg.append(len(script))
            cfg.extend(script)
            cfg.extend(struct.pack("<I", prime_id & 0xFFFFFFFF))
            cfg.append(len(tag))
            cfg.extend(tag)
            cfg.extend(struct.pack("<Q", vardiff))
            cfg.extend(b"\x00\xfe")
            ver_label = "v1"
        await self.send_channel(bytes(cfg), signed=True)
        self.configured = True
        self._configure_at = time.monotonic()
        self._configure_ver = "v3" if use_v3 else "v1"
        self._dialect_ok = False
        log.info(
            "sent 0x99 configure %s tag=%s vardiff_min=%s listen=%s resume=%s ua=%r",
            ver_label,
            tag.decode(),
            vardiff,
            self.listen_port,
            ("yes" if resumed else "no") if use_v3 else "n/a",
            self.client_ua,
        )
        if abw_on:
            await self._send_abw_assignment_notice()
            self._start_abw_rotator()

    def _note_dialect_ok(self) -> None:
        if self._dialect_ok:
            return
        self._dialect_ok = True
        try:
            self.coinbaser_cache.dialect.note_success(
                ip=self.peer_ip,
                ua=self.client_ua or "",
                configure_ver=self._configure_ver,
            )
        except Exception:  # noqa: BLE001
            pass

    def _session_wants_abw(self) -> bool:
        """ABW-on only on quiet abw_prime_port, or lab global abw_enable (no dual-port)."""
        abw_port = int(getattr(self.settings, "abw_prime_port", 0) or 0)
        if abw_port > 0:
            return int(self.listen_port) == abw_port
        return bool(getattr(self.settings, "abw_enable", False))

    async def _send_abw_assignment_notice(self, *, wire_slot: int | None = None) -> None:
        """ACTIVE 0xA8 notice — unlocks CONVOY Blake jobs when ABW is required."""
        if wire_slot is None:
            wire_slot = int(self.abw_wire_slot) & 0x0F
        key = new_xor_key()
        notice = pack_assignment_notice(wire_slot=wire_slot, xor_key=key, active=True)
        self.abw_required = True
        self.abw_wire_slot = wire_slot
        self.abw_xor_key = key
        self.abw_key_hash = xor_key_hash(key)
        await self.send_channel(notice, signed=False)
        log.info(
            "sent ABW assignment notice ACTIVE slot=%s key_hash=%s… listen=%s",
            wire_slot,
            self.abw_key_hash[:8].hex(),
            self.listen_port,
        )

    async def _abw_reveal_current(self) -> None:
        if not self.abw_xor_key or len(self.abw_xor_key) != 16:
            return
        slot = int(self.abw_wire_slot) & 0x0F
        reveal = pack_reveal(wire_slot=slot, xor_key=self.abw_xor_key)
        await self.send_channel(reveal, signed=False)
        log.info(
            "sent ABW reveal slot=%s key_hash=%s…",
            slot,
            self.abw_key_hash[:8].hex() if self.abw_key_hash else "",
        )

    def _start_abw_rotator(self) -> None:
        interval = float(getattr(self.settings, "abw_rotate_seconds", 0) or 0)
        if interval <= 0:
            return
        if self._abw_rotate_task and not self._abw_rotate_task.done():
            return

        async def _loop() -> None:
            try:
                while True:
                    await asyncio.sleep(interval)
                    if not self.abw_required or not self.configured:
                        continue
                    try:
                        old_slot = int(self.abw_wire_slot) & 0x0F
                        await self._abw_reveal_current()
                        new_slot = (old_slot + 1) & 0x0F
                        await self._send_abw_assignment_notice(wire_slot=new_slot)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("ABW rotate failed: %s", exc)
            except asyncio.CancelledError:
                return

        self._abw_rotate_task = asyncio.create_task(_loop())

    def _stop_abw_rotator(self) -> None:
        t = self._abw_rotate_task
        self._abw_rotate_task = None
        if t and not t.done():
            t.cancel()

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
        self._note_dialect_ok()
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

    @staticmethod
    def _pow_coinbase_ascii(msg: bytes, after_user: int) -> str:
        """ASCII view of POW section 0x02 coinb1||coinb2 when present (else "")."""
        i = after_user
        while i < len(msg):
            tag = msg[i]
            i += 1
            if tag == 0xFE:
                break
            if tag == 0x01 and i + 68 <= len(msg):
                mcount = msg[i + 67]
                need = 68 + int(mcount) * 32
                if i + need > len(msg):
                    break
                i += need
                continue
            if tag == 0x02 and i + 5 <= len(msg):
                c1 = struct.unpack_from("<H", msg, i + 1)[0]
                c2 = struct.unpack_from("<H", msg, i + 3)[0]
                start = i + 5
                end = start + c1 + c2
                if end > len(msg) or c1 < 0 or c2 < 0:
                    break
                raw = msg[start:end]
                return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)
            if tag == 0x03 and i + 17 <= len(msg):
                i += 17
                continue
            if tag == 0x04 and i + 4 <= len(msg):
                i += 4
                continue
            if tag == 0x05 and i + 1 <= len(msg):
                i += 1
                continue
            break
        return ""

    async def _try_learn_nickname_from_pow(
        self, address: str, msg: bytes, after_user: int
    ) -> None:
        """If address has blank nickname and POW carries coinbase 0x02, learn secondary tag.

        Prefetch method credited to iohzard; call-out from Taki. Best-effort only —
        never rejects a share for missing/invalid nick.

        Coinbase ascii is only held for this call (not stored on shares). When learn
        fails, rarely log a short ascii probe (≤1/addr/30m) so we can see whether a
        2nd tag is present.
        """
        addr = (address or "").strip()
        if not addr:
            return
        known, has = self.qguard.nick_status(addr)
        if known and has:
            return
        if not known:
            try:
                nmap = await self.store.nicknames_for_addresses([addr])
            except Exception as exc:  # noqa: BLE001
                log.debug("nickname lookup failed: %s", exc)
                return
            has = bool((nmap.get(addr) or "").strip())
            self.qguard.set_nick_status(addr, has)
            if has:
                return
        ascii_cb = self._pow_coinbase_ascii(msg, after_user)
        matched = ""
        nick = None
        reason = "no_pow_coinbase_0x02"
        if ascii_cb:
            try:
                from tides_pool.block_confirm import extract_secondary_tag

                try:
                    from tides_pool.block_confirm import matched_pool_tag as _matched_pool_tag
                except ImportError:  # older image without helper
                    _matched_pool_tag = None  # type: ignore[assignment]

                legacy = str(
                    getattr(self.settings, "coinbase_tag_legacy", "TIDES") or "TIDES"
                )
                primary = str(self.settings.coinbase_tag_primary or "").strip()
                if _matched_pool_tag is not None:
                    matched = _matched_pool_tag(ascii_cb, primary, legacy) or ""
                else:
                    # Prefer primary if present in ascii, else first legacy hit.
                    matched = ""
                    for cand in [primary, *[t.strip() for t in legacy.split(",")]]:
                        if cand and cand in ascii_cb:
                            matched = cand
                            break
                if not matched:
                    reason = "no_pool_primary_tag"
                else:
                    nick = extract_secondary_tag(ascii_cb, matched)
                    reason = "ok" if nick else "no_secondary_after_primary"
            except Exception as exc:  # noqa: BLE001
                log.debug("nickname extract failed: %s", exc)
                reason = f"extract_error:{exc}"
                nick = None
        if not nick:
            # Rare probe: same throttled learn path, plus ≤1 log / addr / 30m.
            if self.qguard.should_log_nick_pow_probe(addr, interval_sec=1800.0):
                snippet = (ascii_cb or "")[:160]
                log.info(
                    "nickname POW probe address=%s reason=%s primary=%r ascii=%r",
                    addr[:28],
                    reason,
                    matched or None,
                    snippet,
                )
            return
        try:
            await self.store.set_address_nickname(addr, nick)
            self.qguard.set_nick_status(addr, True)
            log.info(
                "nickname learned from POW coinbase address=%s nick=%s",
                addr[:28],
                nick,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("nickname save from POW failed: %s", exc)

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

        live_max = await self.store.max_share_seq()
        head_seq = int(live_max or 0)
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
            # Website numbers = frozen coinbaser cache (no re-walk of shares).
            # Match chain against current/prior ~5s cache ticks when possible.
            # Lock share_head to matched snap max_seq so post-snap shares
            # (drift) fall into the next block window automatically.
            try:
                from tides_pool.block_confirm import coinbase_payout_map

                chain_map = (
                    coinbase_payout_map(resolved_blk) if resolved_blk is not None else None
                )
                snap = self.coinbaser_cache.snapshot_for_block(
                    int(reward_sats), chain=chain_map
                )
                if snap is not None:
                    snap_head = int(
                        snap.get("max_seq")
                        or snap.get("share_head_seq")
                        or 0
                    )
                    if snap_head > 0:
                        # Never advance past live tip; never go backwards past 0
                        head_seq = min(snap_head, int(live_max or snap_head))
                    snap["share_head_seq"] = head_seq
                    snap["live_max_seq"] = int(live_max or 0)
                    snap["captured_at"] = datetime.now(timezone.utc).isoformat()
                    intended_json = json.dumps(snap, separators=(",", ":"))
                    if snap.get("source") == "coinbaser_cache_match":
                        log.info(
                            "BLOCK FOUND matched cache snap height=%s payees=%s "
                            "share_head=%s live_max=%s drift_shares≈%s",
                            resolved_height,
                            len(snap.get("outputs") or []),
                            head_seq,
                            live_max,
                            max(0, int(live_max or 0) - int(head_seq or 0)),
                        )
                else:
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
        # Rotate eras at locked head: work after head_seq is next-block current.
        self.coinbaser_cache.rotate_after_find(
            share_head_seq=int(head_seq or 0),
            reason=f"block found height={resolved_height} head={head_seq}",
        )
        log.info(
            "BLOCK FOUND finder=%s worker=%s height=%s hash=%s reward=%s mode=%s "
            "share_head=%s live_max=%s bonus_next=%s (marked_paid=%s, pending confirm)",
            finder,
            worker,
            resolved_height,
            block_hash[:16] if block_hash else "",
            reward_sats,
            payout_mode,
            head_seq,
            live_max,
            bonus,
            paid_n,
        )

    async def _stamp_sv1_stratum_nick(self, address: str) -> None:
        """Mark SV1/local-GW payout addresses as Stratum Endpoint immediately.

        Does not wait for an accepted/good share — any POW with a valid payout
        username on a fee-peer session is enough. Debounced per process.
        """
        if not self.settings.is_local_work_fee_peer(self.peer_ip):
            return
        addr = (address or "").strip()
        if not addr:
            return
        cache = self.coinbaser_cache
        stamped = cache._sv1_stratum_nick_stamped
        if addr in stamped:
            return
        stamped.add(addr)
        try:
            await self.store.set_address_nickname(addr, _STRATUM_ENDPOINT_NICK)
            self.qguard.set_nick_status(addr, True)
            log.info(
                "stratum nick: stamped %r on %s (fee-peer %s, no wait for good share)",
                _STRATUM_ENDPOINT_NICK,
                addr[:28],
                self.peer_ip,
            )
        except Exception:  # noqa: BLE001
            stamped.discard(addr)
            log.exception("stratum nick: stamp failed for %s", addr[:28])

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
        ntime = struct.unpack_from("<I", msg, 5)[0]
        nonce = struct.unpack_from("<I", msg, 9)[0]
        # username C-string at offset 30
        rest = msg[30:]
        nul = rest.find(b"\x00")
        username = (rest[:nul] if nul >= 0 else rest).decode("utf-8", errors="replace")
        address = username.split(".", 1)[0]
        worker = username.split(".", 1)[1] if "." in username else None
        # TLV payload starts after username NUL + 4 reserved bytes.
        after_user = 30 + (nul + 1 if nul >= 0 else len(rest)) + 4
        abw_slot = self._parse_pow_abw_slot(msg, after_user) if self.abw_required else None

        # SV1 / local-GW: stamp cohort nick as soon as username is a real payout
        # address — do not wait for accept / good PoT / non-r27.
        if is_valid_payout_address(address):
            await self._stamp_sv1_stratum_nick(address)

        def _reject(reason: int, why: str) -> None:
            log.warning(
                "share REJECT %s user=%r coinbase_id=%s flags=%02x target=%s nonce=%08x block=%s peer=%s ua=%r",
                why,
                username,
                coinbase_id,
                flags,
                target_byte,
                nonce,
                is_block,
                self.peer_key,
                self.client_ua or "(unknown)",
            )

        async def _send_reject(reason: int, why: str) -> None:
            _reject(reason, why)
            resp = bytearray()
            resp.append(0x8F)
            resp.append(DATUM_POW_REJECTED)
            resp.extend(struct.pack("<H", reason))
            resp.extend(struct.pack("<I", nonce))
            resp.append(target_byte & 0xFF)
            resp.append(job_id & 0xFF)
            await self.send_channel(bytes(resp), signed=False)

        # --- Always-on cheap checks (every share): claimed PoT / work band ---
        min_tb = self.settings.share_target_byte_min()
        if not target_byte_allowed(
            target_byte,
            is_block=is_block,
            min_tb=min_tb,
            max_tb_share=self.settings.share_target_byte_max,
            max_tb_block=self.settings.share_target_byte_max_block,
        ):
            await _send_reject(DATUM_REJECT_BAD_TARGET, "bad target_byte")
            await self.store.record_share_attempt(
                address or username,
                accepted=False,
                reason_code=DATUM_REJECT_BAD_TARGET,
                why="bad target_byte",
                worker=worker,
                is_block=is_block,
            )
            return

        self._pow_share_seq += 1
        # Optional ntime skew (default off — false-rejected real Gateways).
        if bool(getattr(self.settings, "share_ntime_check", False)):
            every = max(int(self.settings.pow_check_every), 1)
            do_extra = is_block or (self._pow_share_seq % every == 0)
            if do_extra and not ntime_skew_ok(
                ntime,
                now=time.time(),
                max_skew_sec=int(self.settings.share_ntime_max_skew_sec),
            ):
                await _send_reject(DATUM_REJECT_BAD_NTIME, "bad ntime")
                await self.store.record_share_attempt(
                    address or username,
                    accepted=False,
                    reason_code=DATUM_REJECT_BAD_NTIME,
                    why="bad ntime",
                    worker=worker,
                    is_block=is_block,
                )
                return

        # DATUM/Ocean convention: stratum username must be a payout address.
        # With Gateway "Pool Pass Full Users" (override address), a bare worker
        # name like "rig1" lands here and cannot be remapped — we never see
        # mining.pool_address on the share wire.
        if not is_valid_payout_address(address):
            await _send_reject(DATUM_REJECT_BAD_USERNAME, "bad payout address")
            try:
                self.coinbaser_cache.note_bad_payout_username(username)
                self.coinbaser_cache.note_reject_ua(self.client_ua, kind="bad_payout")
            except Exception:  # noqa: BLE001
                pass
            # Audit trail (full bech32/base58 check is cheap; do not skip for new miners).
            try:
                await self.store.record_share_attempt(
                    address or username,
                    accepted=False,
                    reason_code=DATUM_REJECT_BAD_USERNAME,
                    why="bad payout address",
                    worker=worker,
                    is_block=is_block,
                )
            except Exception:  # noqa: BLE001
                pass
            return

        # When multi-out was assigned: refuse empty / subsidy-only / 0xFF
        # non-blocks (nudge GW for a new template); still keep block finds.
        cb_ok, cb_why = self._coinbase_id_ok(
            coinbase_id, subsidy_only=subsidy_only, is_block=is_block
        )
        if not cb_ok:
            _reject(DATUM_REJECT_BAD_COINBASE_OUTPUTS, cb_why)
            try:
                self.coinbaser_cache.note_reject_ua(self.client_ua, kind="r27")
            except Exception:  # noqa: BLE001
                pass
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
                why=cb_why,
                worker=worker,
                is_block=is_block,
            )
            self.qguard.note_attempt(
                address,
                accepted=False,
                reason_code=DATUM_REJECT_BAD_COINBASE_OUTPUTS,
                why=cb_why,
            )
            await self._maybe_quarantine(
                address, is_block=is_block, msg=msg, after_user=after_user
            )
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


        tlv_height, tlv_value = self._parse_pow_job_meta(msg, after_user)

        work = share_work_from_target_byte(
            target_byte,
            min_share_difficulty=float(self.settings.min_share_difficulty),
            work_ceiling=self.settings.share_work_ceiling(),
        )
        if work <= 0:
            await _send_reject(DATUM_REJECT_BAD_TARGET, "zero work")
            return

        # Optional per-address work cap. multiplier<=0 → normal pool (full credit).
        cap = self.settings.address_work_cap()
        window = self.settings.address_work_cap_window_sec
        used = 0
        credit = work
        acked = False
        try:
            if cap > 0:
                used = await self.store.work_for_address_since(address, window)
                remaining = max(cap - used, 0)
                credit = min(work, remaining)

            async def _persist_accepted() -> None:
                if credit > 0:
                    if self.on_share:
                        await self.on_share(address, credit, worker)
                    else:
                        # Local GW work skim: (100-bps)% miner + skim.
                        # Of skim: ops_share_bps → OPS now; remainder accrues and
                        # is flushed on coinbaser tick ∝ window work (non-OPS).
                        # Not coinbaser fee_bps. BPS is hot-reloadable via
                        # meta.runtime_fees (see tides_pool.runtime_fees).
                        fee_bps = int(
                            await self.coinbaser_cache.runtime_fees.local_work_fee_bps()
                        )
                        ops_addr = (self.settings.pool_ops_address or "").strip()
                        skim = (
                            fee_bps > 0
                            and bool(ops_addr)
                            and self.settings.is_local_work_fee_peer(self.peer_ip)
                        )
                        community_work = 0
                        if skim:
                            miner_work = credit * (10_000 - fee_bps) // 10_000
                            skim_work = credit - miner_work
                            ops_frac = int(
                                await self.coinbaser_cache.runtime_fees.local_work_fee_ops_share_bps()
                            )
                            ops_frac = max(0, min(10_000, ops_frac))
                            ops_work = skim_work * ops_frac // 10_000
                            community_work = skim_work - ops_work
                        else:
                            miner_work = credit
                            ops_work = 0
                        if miner_work > 0:
                            row = await self.store.append_share(
                                address,
                                miner_work,
                                worker=worker,
                                fee_bps=0,
                                connection_type=("sv1" if skim else "datum"),
                            )
                            self.coinbaser_cache.note_share(
                                seq=row.seq,
                                address=row.address,
                                work=row.work,
                                fee_bps=row.fee_bps,
                            )
                        if ops_work > 0:
                            ops_worker = (
                                getattr(
                                    self.settings, "local_work_fee_worker", None
                                )
                                or "OPERATION FEE"
                            )
                            row2 = await self.store.append_share(
                                ops_addr,
                                ops_work,
                                worker=ops_worker,
                                fee_bps=0,
                            )
                            self.coinbaser_cache.note_share(
                                seq=row2.seq,
                                address=row2.address,
                                work=row2.work,
                                fee_bps=row2.fee_bps,
                            )
                        if community_work > 0:
                            self.coinbaser_cache.note_community_fee(community_work)
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
                if self.qguard.should_check_auto_q(address):
                    await self._try_learn_nickname_from_pow(address, msg, after_user)

            if is_block:
                # Blocks: persist (incl. finder/window) THEN ack — never ack-before-write.
                await _persist_accepted()
                if address:
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
                if cap > 0 and credit < work:
                    log.info(
                        "share OK (CAPPED) user=%s work=%s credited=%s used=%s/%s/%ss nonce=%08x cb_id=%s BLOCK",
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
                        "share OK user=%s work=%s nonce=%08x cb_id=%s BLOCK",
                        username,
                        work,
                        nonce,
                        coinbase_id,
                    )
                self._note_dialect_ok()
                await self._send_share_response(
                    status=DATUM_POW_ACCEPTED,
                    reason=0,
                    nonce=nonce,
                    target_byte=target_byte,
                    job_id=job_id,
                    abw_slot=abw_slot,
                )
                return

            # Normal shares: ack first so Gateway never waits on Postgres (>30s → reconnect).
            await self._send_share_response(
                status=DATUM_POW_ACCEPTED,
                reason=0,
                nonce=nonce,
                target_byte=target_byte,
                job_id=job_id,
                abw_slot=abw_slot,
            )
            acked = True
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
                    "share OK user=%s work=%s nonce=%08x cb_id=%s",
                    username,
                    work,
                    nonce,
                    coinbase_id,
                )
            self._note_dialect_ok()
            try:
                await _persist_accepted()
            except Exception as exc:  # noqa: BLE001
                log.warning("share persist failed after ack user=%s: %s", username, exc)
        except Exception as exc:  # noqa: BLE001
            if acked:
                log.warning("share path error after ack user=%s: %s", username, exc)
                return
            log.warning("share reject: %s", exc)
            await self._send_share_response(
                status=DATUM_POW_REJECTED,
                reason=DATUM_REJECT_OTHER,
                nonce=nonce,
                target_byte=target_byte,
                job_id=job_id,
                abw_slot=abw_slot,
            )

    def _parse_pow_abw_slot(self, msg: bytes, after_user: int) -> int | None:
        """Return wire slot from POW TLV 0x05 when present."""
        i = after_user
        while i < len(msg):
            tag = msg[i]
            i += 1
            if tag == 0xFE:
                break
            if tag == 0x01 and i + 68 <= len(msg):
                mcount = msg[i + 67]
                need = 68 + int(mcount) * 32
                if i + need > len(msg):
                    break
                i += need
                continue
            if tag == 0x02 and i + 5 <= len(msg):
                c1 = struct.unpack_from("<H", msg, i + 1)[0]
                c2 = struct.unpack_from("<H", msg, i + 3)[0]
                i += 5 + c1 + c2
                continue
            if tag == 0x03 and i + 17 <= len(msg):
                i += 17
                continue
            if tag == 0x04 and i + 4 <= len(msg):
                i += 4
                continue
            if tag == 0x05 and i + 1 <= len(msg):
                slot = int(msg[i])
                return slot if 0 <= slot <= 15 else None
            break
        return None

    async def _send_share_response(
        self,
        *,
        status: int,
        reason: int,
        nonce: int,
        target_byte: int,
        job_id: int,
        abw_slot: int | None = None,
        raw_pow_hash: bytes | None = None,
    ) -> None:
        """Send 0x8F; when ABW-on + slot, also ABW-shaped ack and 0xA5/0xA7.

        Open Gateway 0x27 does not carry raw_pow_hash; with
        abw_verify_all_shares_on_disclosure=false a zero hash receipt/release is
        still wire-valid (forget is a no-op if unmatched). Proves Prime→GW path.
        """
        if self.abw_required and abw_slot is not None:
            h = raw_pow_hash if raw_pow_hash and len(raw_pow_hash) == 32 else bytes(32)
            try:
                await self.send_channel(
                    pack_share_response_abw(
                        status=status,
                        reason=reason,
                        nonce=nonce,
                        target_byte=target_byte,
                        job_id=job_id,
                        wire_slot=abw_slot,
                        raw_pow_hash=h,
                    ),
                    signed=False,
                )
                await self.send_channel(
                    pack_candidate_receipt(wire_slot=abw_slot, raw_pow_hash=h),
                    signed=False,
                )
                await self.send_channel(
                    pack_candidate_release(wire_slot=abw_slot, raw_pow_hash=h),
                    signed=False,
                )
                log.info(
                    "ABW share ack+receipt+release status=0x%02x slot=%s hash=%s…",
                    status,
                    abw_slot,
                    h[:4].hex(),
                )
                return
            except Exception as exc:  # noqa: BLE001
                log.warning("ABW share ack path failed, falling back to legacy: %s", exc)
        resp = bytearray()
        resp.append(0x8F)
        resp.append(status & 0xFF)
        resp.extend(struct.pack("<H", reason & 0xFFFF))
        resp.extend(struct.pack("<I", nonce & 0xFFFFFFFF))
        resp.append(target_byte & 0xFF)
        resp.append(job_id & 0xFF)
        await self.send_channel(bytes(resp), signed=False)

    async def run(self) -> None:
        try:
            await self.handshake()
            peer = self.writer.get_extra_info("peername")
            log.info(
                "DATUM Gateway connected from %s listen=%s ua=%r",
                peer,
                self.listen_port,
                self.client_ua or "(unknown)",
            )
            max_len = int(self.settings.datum_max_cmd_len)
            while True:
                hdr_x = await self._read_exact(4)
                hdr_p = xor_header(hdr_x, self.recv_hdr_key)
                self.recv_hdr_key = header_xor_feedback(self.recv_hdr_key)
                h = unpack_header(hdr_p)
                if not cmd_len_allowed(h["cmd_len"], max_len):
                    log.warning(
                        "cmd_len %s exceeds max %s from %s cmd=%s — closing",
                        h["cmd_len"],
                        max_len,
                        peer,
                        h.get("proto_cmd"),
                    )
                    return
                payload = await self._read_exact(h["cmd_len"])
                if h["is_encrypted_channel"]:
                    assert self.box is not None
                    try:
                        # ciphertext includes MAC; decrypt with current recv nonce
                        pt = self.box.decrypt(payload, bytes(self.recv_nonce))
                    except CryptoError:
                        log.error(
                            "channel decrypt failed cmd=%s len=%s",
                            h["proto_cmd"],
                            h["cmd_len"],
                        )
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
                    log.debug(
                        "unhandled proto_cmd=%s sealed=%s",
                        h["proto_cmd"],
                        h["is_encrypted_pubkey"],
                    )
        finally:
            self._stop_abw_rotator()


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
        log.info(
            "DATUM Gateway disconnected %s ua=%r",
            peer,
            getattr(sess, "client_ua", "") or "(unknown)",
        )
    except Exception:
        log.exception("DATUM Prime session error from %s", peer)
    finally:
        try:
            coinbaser_cache.dialect.note_disconnect(
                ip=getattr(sess, "peer_ip", "") or "",
                ua=getattr(sess, "client_ua", "") or "",
                configured=bool(getattr(sess, "configured", False)),
                configure_ver=str(getattr(sess, "_configure_ver", "") or ""),
                configure_at=float(getattr(sess, "_configure_at", 0.0) or 0.0),
                dialect_ok=bool(getattr(sess, "_dialect_ok", False)),
            )
        except Exception:  # noqa: BLE001
            pass
        coinbaser_cache.gateway_sessions = max(0, coinbaser_cache.gateway_sessions - 1)
        try:
            coinbaser_cache.drop_gateway_session(sess.peer_key)
        except Exception:  # noqa: BLE001
            pass
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


class _PrimeListenBundle:
    """One or two asyncio servers; api lifespan treats this like AbstractServer."""

    def __init__(self, servers: list[asyncio.AbstractServer]) -> None:
        self.servers = list(servers)

    @property
    def sockets(self):
        out = []
        for s in self.servers:
            out.extend(list(s.sockets or []))
        return out

    def close(self) -> None:
        for s in self.servers:
            s.close()

    async def wait_closed(self) -> None:
        for s in self.servers:
            await s.wait_closed()


async def start_datum_prime(
    settings: Settings,
    store: Store,
    keys_path: Path,
) -> tuple[_PrimeListenBundle, PoolKeys, CoinbaserSplitCache]:
    keys = PoolKeys.load_or_create(keys_path)
    coinbaser_cache = CoinbaserSplitCache(store, settings)
    quarantine_guard = QuarantineGuard(settings)
    coinbaser_cache.start_background()

    async def _client(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
        await handle_client(
            r, w, keys, settings, store, coinbaser_cache, quarantine_guard
        )

    servers: list[asyncio.AbstractServer] = [
        await asyncio.start_server(
            _client,
            host=settings.host,
            port=settings.datum_prime_port,
        )
    ]
    abw_port = int(getattr(settings, "abw_prime_port", 0) or 0)
    if abw_port > 0 and abw_port != int(settings.datum_prime_port):
        servers.append(
            await asyncio.start_server(
                _client,
                host=settings.host,
                port=abw_port,
            )
        )
        log.info(
            "DATUM Prime ABW test port listening on %s (same DB; public :%s unchanged)",
            abw_port,
            settings.datum_prime_port,
        )

    bundle = _PrimeListenBundle(servers)
    socks = ", ".join(str(s.getsockname()) for s in bundle.sockets)
    log.info(
        "DATUM Prime listening on %s pubkey=%s coinbaser_cache=%ss q_check_every=%s abw_port=%s",
        socks,
        keys.pubkey_hex,
        coinbaser_cache.ttl(),
        int(getattr(settings, "quarantine_check_every_n", 10) or 10),
        abw_port or "off",
    )
    return bundle, keys, coinbaser_cache
