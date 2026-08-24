"""Minimal TN4/mainnet address → scriptPubKey (legacy P2PKH/P2SH + bech32 v0)."""

from __future__ import annotations

import hashlib


_B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


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


def _bech32_polymod(values: list[int]) -> int:
    gen = (0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3)
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_verify(hrp: str, data: list[int], *, spec: str) -> bool:
    const = 1 if spec == "bech32" else 0x2BC830A3
    return _bech32_polymod(_bech32_hrp_expand(hrp) + data) == const


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool = True) -> list[int] | None:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    max_acc = (1 << (frombits + tobits - 1)) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = ((acc << frombits) | value) & max_acc
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def bech32_decode(addr: str) -> tuple[str, int, bytes]:
    """Decode bech32/bech32m address → (hrp, witver, witprog). Raises ValueError."""
    addr = addr.strip()
    if any(ord(x) < 33 or ord(x) > 126 for x in addr):
        raise ValueError("invalid bech32 character")
    if addr.lower() != addr and addr.upper() != addr:
        raise ValueError("mixed-case bech32")
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        raise ValueError("invalid bech32 length/separator")
    hrp = addr[:pos]
    try:
        data = [_BECH32_CHARSET.index(c) for c in addr[pos + 1 :]]
    except ValueError as exc:
        raise ValueError("invalid bech32 data character") from exc
    if _bech32_verify(hrp, data, spec="bech32"):
        spec = "bech32"
    elif _bech32_verify(hrp, data, spec="bech32m"):
        spec = "bech32m"
    else:
        raise ValueError("bad bech32 checksum")
    # data = [witver] + program_5bit + checksum[6]; strip checksum before convertbits
    payload = data[:-6]
    decoded = _convertbits(payload[1:], 5, 8, False)
    if decoded is None or not (2 <= len(decoded) <= 40):
        raise ValueError("invalid witness program length")
    witver = payload[0]
    if witver > 16:
        raise ValueError("invalid witness version")
    if witver == 0 and len(decoded) not in (20, 32):
        raise ValueError("invalid v0 witness program length")
    if witver == 0 and spec != "bech32":
        raise ValueError("v0 must use bech32")
    if witver != 0 and spec != "bech32m":
        raise ValueError("v1+ must use bech32m")
    return hrp, witver, bytes(decoded)


def address_to_script(addr: str) -> bytes:
    """Return scriptPubKey for a Bitcoin address. Raises ValueError if unsupported."""
    addr = addr.strip()
    if not addr:
        raise ValueError("empty address")
    if addr.lower().startswith(("bc1", "tb1", "bcrt1")):
        hrp, witver, prog = bech32_decode(addr)
        if hrp not in ("bc", "tb", "bcrt"):
            raise ValueError(f"unexpected bech32 hrp: {hrp}")
        # BIP141: OP_n OP_PUSHBYTES_len <prog>
        # witver 0..16 → OP_0 (0x00) or OP_1..OP_16 (0x51..0x60)
        op_ver = 0x00 if witver == 0 else (0x50 + witver)
        return bytes([op_ver, len(prog)]) + prog
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
