"""Minimal TN4/mainnet address → scriptPubKey (legacy P2PKH + bech32 stub)."""

from __future__ import annotations

import hashlib


_B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s.encode("ascii"):
        n = n * 58 + _B58.index(ch)
    # leading zeros
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    full = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
    return b"\x00" * pad + full


def address_to_script(addr: str) -> bytes:
    """Return scriptPubKey for a Bitcoin address. Raises ValueError if unsupported."""
    addr = addr.strip()
    if not addr:
        raise ValueError("empty address")
    if addr.startswith(("bc1", "tb1", "bcrt1")):
        raise ValueError(f"bech32 not implemented yet: {addr}")
    raw = b58decode(addr)
    if len(raw) < 5:
        raise ValueError("address too short")
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise ValueError(f"bad checksum: {addr}")
    version, h160 = payload[0], payload[1:]
    if len(h160) != 20:
        raise ValueError("expected 20-byte hash160")
    # 0x00 mainnet P2PKH, 0x6f testnet/regtest P2PKH, 0x6f also used on testnet4 legacy
    if version in (0x00, 0x6F):
        return b"\x76\xa9\x14" + h160 + b"\x88\xac"  # OP_DUP OP_HASH160 <20> OP_EQUALVERIFY OP_CHECKSIG
    if version in (0x05, 0xC4):
        return b"\xa9\x14" + h160 + b"\x87"  # P2SH
    raise ValueError(f"unsupported address version 0x{version:02x}")
