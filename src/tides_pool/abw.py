"""Minimal DATUM ABW (anti-withholding) helpers for lab Prime.

Wire layouts reverse-engineered from CONVOYMining/datum_gateway (draft rev 0).
Lab-only — not a full CONVOY Prime clone.
"""
from __future__ import annotations

import hashlib
import os

DATUM_ABW_DRAFT_REVISION = 0
DATUM_ABW_ASSIGNMENT_ACTIVE = 0x01
DATUM_CONFIG_FLAG_ABW_DISABLED = 0x01
TAG_XOR_KEY = "Bitcoin block hash PoW XOR key"


def tagged_hash(tag: str, data: bytes) -> bytes:
    """BIP-340 TaggedHash: SHA256(SHA256(tag)||SHA256(tag)||data)."""
    th = hashlib.sha256(tag.encode("utf-8")).digest()
    return hashlib.sha256(th + th + data).digest()


def xor_key_hash(xor_key: bytes) -> bytes:
    if len(xor_key) != 16:
        raise ValueError("xor_key must be 16 bytes")
    return tagged_hash(TAG_XOR_KEY, xor_key)


def new_xor_key() -> bytes:
    """Non-null 16-byte key (gateway rejects all-zero key_hash)."""
    while True:
        key = os.urandom(16)
        if key != bytes(16) and xor_key_hash(key) != bytes(32):
            return key


def pack_assignment_notice(*, wire_slot: int = 0, xor_key: bytes, active: bool = True) -> bytes:
    """Mining subcmd 0xA8 body+subcmd: ACTIVE notice unlocks Blake jobs when ABW required."""
    if not (0 <= wire_slot <= 15):
        raise ValueError("wire_slot out of range")
    kh = xor_key_hash(xor_key)
    body = bytearray()
    body.append(0xA8)
    body.append(DATUM_ABW_DRAFT_REVISION)
    body.append(DATUM_ABW_ASSIGNMENT_ACTIVE if active else 0)
    body.append(wire_slot & 0xFF)
    body.extend(kh)
    body.append(0xFE)
    if len(body) != 1 + 36:
        raise RuntimeError(f"bad notice len {len(body)}")
    return bytes(body)


def pack_activation(*, wire_slot: int = 0) -> bytes:
    body = bytearray()
    body.append(0xA6)
    body.append(DATUM_ABW_DRAFT_REVISION)
    body.append(wire_slot & 0xFF)
    body.append(0xFE)
    return bytes(body)


def pack_reveal(*, wire_slot: int, xor_key: bytes) -> bytes:
    if len(xor_key) != 16:
        raise ValueError("xor_key must be 16 bytes")
    body = bytearray()
    body.append(0xA9)
    body.append(DATUM_ABW_DRAFT_REVISION)
    body.append(wire_slot & 0xFF)
    body.extend(xor_key)
    body.append(0xFE)
    return bytes(body)


def pack_candidate_receipt(*, wire_slot: int, raw_pow_hash: bytes) -> bytes:
    """Mining subcmd 0xA5 — pool ack'd an ABW candidate (35-byte body after subcmd)."""
    if not (0 <= wire_slot <= 15):
        raise ValueError("wire_slot out of range")
    if len(raw_pow_hash) != 32:
        raise ValueError("raw_pow_hash must be 32 bytes")
    body = bytearray()
    body.append(0xA5)
    body.append(DATUM_ABW_DRAFT_REVISION)
    body.append(wire_slot & 0xFF)
    body.extend(raw_pow_hash)
    body.append(0xFE)
    if len(body) != 1 + 35:
        raise RuntimeError(f"bad receipt len {len(body)}")
    return bytes(body)


def pack_candidate_release(*, wire_slot: int, raw_pow_hash: bytes) -> bytes:
    """Mining subcmd 0xA7 — pool released/discarded an ABW candidate."""
    if not (0 <= wire_slot <= 15):
        raise ValueError("wire_slot out of range")
    if len(raw_pow_hash) != 32:
        raise ValueError("raw_pow_hash must be 32 bytes")
    body = bytearray()
    body.append(0xA7)
    body.append(DATUM_ABW_DRAFT_REVISION)
    body.append(wire_slot & 0xFF)
    body.extend(raw_pow_hash)
    body.append(0xFE)
    if len(body) != 1 + 35:
        raise RuntimeError(f"bad release len {len(body)}")
    return bytes(body)


def pack_share_response_abw(
    *,
    status: int,
    reason: int,
    nonce: int,
    target_byte: int,
    job_id: int,
    wire_slot: int,
    raw_pow_hash: bytes,
) -> bytes:
    """0x8F share response with ABW section 0x06 (44 bytes after subcmd)."""
    if len(raw_pow_hash) != 32:
        raise ValueError("raw_pow_hash must be 32 bytes")
    if not (0 <= wire_slot <= 15):
        raise ValueError("wire_slot out of range")
    import struct

    body = bytearray()
    body.append(0x8F)
    body.append(status & 0xFF)
    body.extend(struct.pack("<H", reason & 0xFFFF))
    body.extend(struct.pack("<I", nonce & 0xFFFFFFFF))
    body.append(target_byte & 0xFF)
    body.append(job_id & 0xFF)
    body.append(0x06)
    body.append(wire_slot & 0xFF)
    body.extend(raw_pow_hash)
    body.append(0xFE)
    if len(body) != 1 + 44:
        raise RuntimeError(f"bad abw share resp len {len(body)}")
    return bytes(body)
