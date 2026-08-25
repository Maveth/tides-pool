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
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from nacl.public import Box, PrivateKey, PublicKey, SealedBox
from nacl.signing import SigningKey, VerifyKey
from nacl.exceptions import BadSignatureError, CryptoError

from tides_pool.addresses import address_to_script
from tides_pool.config import Settings, finder_credit_bps, miner_reward_bps
from tides_pool.store import Store
from tides_pool.tides import coinbase_suggestion, split_reward


log = logging.getLogger("tides_pool.datum_prime")

AcceptShareCb = Callable[[str, int, str | None], Awaitable[None]]


def script_for_address(addr: str, fallback_ops: str) -> bytes:
    try:
        return address_to_script(addr)
    except ValueError:
        log.warning("cannot encode %s — using ops address script", addr)
        return address_to_script(fallback_ops)



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
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.pool_keys = pool_keys
        self.settings = settings
        self.store = store
        self.on_share = on_share
        self.send_hdr_key = 0
        self.recv_hdr_key = 0
        self.send_nonce = bytearray(24)
        self.recv_nonce = bytearray(24)
        self.box: Box | None = None
        self.session_sign: SigningKey | None = None
        self.configured = False
        self.coinbaser_id = 1

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
            raise RuntimeError(f"expected hello cmd1 sealed, got {h}")
        ct = await self._read_exact(h["cmd_len"])
        try:
            opened = SealedBox(self.pool_keys.box_sk).decrypt(ct)
        except CryptoError as e:
            raise RuntimeError(f"hello seal open failed: {e}") from e
        if len(opened) < 64 + 128:
            raise RuntimeError("hello too short")
        msg, sig = opened[:-64], opened[-64:]
        client_lt_ed = msg[0:32]
        client_lt_x = msg[32:64]
        client_sess_ed = msg[64:96]
        client_sess_x = msg[96:128]
        # find 0xFE then nk
        try:
            fe = msg.index(0xFE, 128)
        except ValueError as e:
            raise RuntimeError("hello missing 0xFE") from e
        if fe + 5 > len(msg):
            raise RuntimeError("hello missing nk")
        nk = struct.unpack_from("<I", msg, fe + 1)[0]
        try:
            VerifyKey(client_lt_ed).verify(msg, sig)
        except BadSignatureError as e:
            raise RuntimeError("hello signature bad") from e

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
        # Build TIDES suggestion (empty window → 100% ops)
        shares = await self.store.list_shares_newest(limit=50_000)
        diff_meta = await self.store.get_meta("block_difficulty", "1") or "1"
        try:
            block_diff = max(int(float(diff_meta)), 1)
        except ValueError:
            block_diff = 1
        tides = split_reward(
            shares,
            reward_sats=value,
            block_difficulty=block_diff,
            window_blocks=self.settings.window_blocks,
            miner_bps=miner_reward_bps(self.settings),
            min_output_sats=self.settings.min_output_sats,
            pool_ops_address=self.settings.pool_ops_address,
        )
        finder, credit = await self.store.pending_finder_credit()
        outs = coinbase_suggestion(
            tides,
            pool_ops_address=self.settings.pool_ops_address or "ops",
            finder_address=finder or "",
            finder_credit_sats=credit,
            min_output_sats=self.settings.min_output_sats,
        )
        if not outs:
            outs = [{"address": self.settings.pool_ops_address, "sats": value, "kind": "ops"}]

        blob = bytearray()
        blob.append(self.coinbaser_id & 0xFF)
        self.coinbaser_id = (self.coinbaser_id % 250) + 1
        assigned = 0
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
            script = script_for_address(addr, ops)
            blob.extend(struct.pack("<Q", sats))
            blob.append(len(script))
            blob.extend(script)
            assigned += sats
            detail.append(f"{addr[:12]}…:{sats}")
            if assigned >= value:
                break
        if assigned == 0:
            script = script_for_address(ops, ops)
            blob.extend(struct.pack("<Q", value))
            blob.append(len(script))
            blob.extend(script)
            detail.append(f"{ops[:12]}…:{value}")

        resp = bytearray()
        resp.append(0x11)
        resp.extend(struct.pack("<Q", value))
        resp.extend(struct.pack("<I", len(blob)))
        resp.extend(blob)
        await self.send_channel(bytes(resp), signed=False)
        log.info("coinbaser value=%s outs=%d assigned=%s [%s]", value, len(outs), assigned, "; ".join(detail))

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
        """Previous-finder bonus: this finder gets 8% on *next* coinbasers; prior credit marked paid.

        Order matters: finder_credits.from_height / paid_in_height FK → blocks(height),
        so record the block row *before* marking prior credits paid or opening a new credit.
        """
        block_hash = f"pool-{height}-{finder[:8]}-{nonce:08x}"
        await self.store.record_block(
            height=height,
            block_hash=block_hash,
            difficulty=difficulty,
            reward_sats=reward_sats,
            finder_address=finder,
        )
        await self.store.set_meta("last_height", str(height))
        paid_n = await self.store.mark_finder_credits_paid(height)
        bonus = reward_sats * finder_credit_bps(self.settings) // 10_000
        await self.store.open_finder_credit(height, finder, bonus)
        log.info(
            "BLOCK FOUND finder=%s worker=%s height=%s reward=%s bonus_next=%s (marked_paid=%s)",
            finder,
            worker,
            height,
            reward_sats,
            bonus,
            paid_n,
        )

    async def _pow(self, msg: bytes) -> None:
        if len(msg) < 31:
            return
        job_id = msg[1]
        flags = msg[3]
        is_block = bool(flags & 0x01)
        target_byte = msg[4]
        nonce = struct.unpack_from("<I", msg, 9)[0]
        # username C-string at offset 30
        rest = msg[30:]
        nul = rest.find(b"\x00")
        username = (rest[:nul] if nul >= 0 else rest).decode("utf-8", errors="replace")
        address = username.split(".", 1)[0]
        worker = username.split(".", 1)[1] if "." in username else None
        after_user = 30 + (nul + 1 if nul >= 0 else len(rest)) + 4  # username + NUL + 4 reserved
        tlv_height, tlv_value = self._parse_pow_job_meta(msg, after_user)

        if target_byte == 0xFF:
            work = max(int(self.settings.min_share_difficulty), 4)
        else:
            work = 1 << int(target_byte)

        try:
            # GPU-friendly per-address work cap (rolling window). Valid PoW still ACKed;
            # only TIDES credit is limited so ASICs cannot dominate the share log.
            cap = self.settings.address_work_cap()
            window = self.settings.address_work_cap_window_sec
            used = await self.store.work_for_address_since(address, window)
            remaining = max(cap - used, 0)
            credit = min(work, remaining)
            if credit > 0:
                if self.on_share:
                    await self.on_share(address, credit, worker)
                else:
                    await self.store.append_share(address, credit, worker=worker, fee_bps=0)
            status = 0x50  # accepted (even if credit==0 — proof ok, capped)
            reason = 0
            if credit < work:
                log.info(
                    "share OK (CAPPED) user=%s work=%s credited=%s used=%s/%s/%ss nonce=%08x",
                    username,
                    work,
                    credit,
                    used,
                    cap,
                    window,
                    nonce,
                )
            else:
                log.info(
                    "share OK user=%s work=%s nonce=%08x%s",
                    username,
                    work,
                    nonce,
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
            status = 0x66
            reason = 30
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
) -> None:
    peer = writer.get_extra_info("peername")
    log.info("DATUM Gateway connected from %s", peer)
    sess = DatumPrimeSession(reader, writer, pool_keys, settings, store)
    try:
        await sess.run()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        log.info("DATUM Gateway disconnected %s", peer)
    except Exception:
        log.exception("DATUM Prime session error from %s", peer)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass


async def start_datum_prime(
    settings: Settings,
    store: Store,
    keys_path: Path,
) -> tuple[asyncio.AbstractServer, PoolKeys]:
    keys = PoolKeys.load_or_create(keys_path)
    server = await asyncio.start_server(
        lambda r, w: handle_client(r, w, keys, settings, store),
        host=settings.host,
        port=settings.datum_prime_port,
    )
    socks = ", ".join(str(s.getsockname()) for s in server.sockets or [])
    log.info("DATUM Prime listening on %s pubkey=%s", socks, keys.pubkey_hex)
    return server, keys
