"""Address → scriptPubKey: legacy P2PKH/P2SH + bech32/bech32m witness programs."""

from __future__ import annotations

import hashlib


_B58 = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s.encode("ascii"):
        n = n * 58 + _B58.index(ch)
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    full = n.to_bytes((n.bit_length() + 7) // 8 or 1, "big")
    return b"\x00" * pad + full


def _bech32_polymod(values: list[int]) -> int:
    gen = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= gen[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp: str) -> list[int]:
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def _bech32_verify(hrp: str, data: list[int], spec: str) -> bool:
    const = 1 if spec == "bech32" else 0x2BC830A3
    return _bech32_polymod(_bech32_hrp_expand(hrp) + data) == const


def _convertbits(data: list[int], frombits: int, tobits: int, pad: bool = True) -> list[int] | None:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << tobits) - 1
    for value in data:
        if value < 0 or (value >> frombits):
            return None
        acc = (acc << frombits) | value
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


def decode_bech32(addr: str) -> tuple[str, int, bytes]:
    """Return (hrp, witver, witprog)."""
    addr = addr.strip()
    if any(ord(x) < 33 or ord(x) > 126 for x in addr):
        raise ValueError("invalid bech32 char")
    if addr.lower() != addr and addr.upper() != addr:
        raise ValueError("mixed case bech32")
    addr = addr.lower()
    pos = addr.rfind("1")
    if pos < 1 or pos + 7 > len(addr) or len(addr) > 90:
        raise ValueError("invalid bech32 length")
    hrp = addr[:pos]
    data_part = addr[pos + 1 :]
    data: list[int] = []
    for ch in data_part:
        try:
            data.append(_CHARSET.index(ch))
        except ValueError as e:
            raise ValueError(f"invalid bech32 char {ch!r}") from e
    if _bech32_verify(hrp, data, "bech32"):
        spec = "bech32"
    elif _bech32_verify(hrp, data, "bech32m"):
        spec = "bech32m"
    else:
        raise ValueError("bad bech32 checksum")
    decoded = _convertbits(data[1:-6], 5, 8, False)
    if decoded is None or not (2 <= len(decoded) <= 40):
        raise ValueError("invalid witness program")
    witver = data[0]
    if witver > 16:
        raise ValueError("invalid witness version")
    if witver == 0 and spec != "bech32":
        raise ValueError("v0 must be bech32")
    if witver != 0 and spec != "bech32m":
        raise ValueError("v1+ must be bech32m")
    if witver == 0 and len(decoded) not in (20, 32):
        raise ValueError("v0 program must be 20 or 32 bytes")
    if witver == 1 and len(decoded) != 32:
        raise ValueError("taproot program must be 32 bytes")
    return hrp, witver, bytes(decoded)


def address_to_script(addr: str) -> bytes:
    """Return scriptPubKey for a Bitcoin address. Raises ValueError if unsupported."""
    addr = addr.strip()
    if not addr:
        raise ValueError("empty address")
    if addr.lower().startswith(("bc1", "tb1", "bcrt1")):
        hrp, witver, prog = decode_bech32(addr)
        if hrp not in ("bc", "tb", "bcrt"):
            raise ValueError(f"unexpected hrp {hrp}")
        # Accept mainnet + testnet encodings (lab may validate either).
        if witver == 0:
            return bytes([0x00, len(prog)]) + prog
        if 1 <= witver <= 16:
            op = witver if witver <= 16 else witver
            # OP_1..OP_16 are 0x51..0x60; OP_0 is 0x00
            push_ver = 0x50 + witver
            return bytes([push_ver, len(prog)]) + prog
        raise ValueError(f"unsupported witver {witver}")

    raw = b58decode(addr)
    if len(raw) < 5:
        raise ValueError("address too short")
    payload, checksum = raw[:-4], raw[-4:]
    if hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] != checksum:
        raise ValueError(f"bad checksum: {addr}")
    version, h160 = payload[0], payload[1:]
    if len(h160) != 20:
        raise ValueError("expected 20-byte hash160")
    if version in (0x00, 0x6F):
        return b"\x76\xa9\x14" + h160 + b"\x88\xac"
    if version in (0x05, 0xC4):
        return b"\xa9\x14" + h160 + b"\x87"
    raise ValueError(f"unsupported address version 0x{version:02x}")


def is_valid_payout_address(addr: str) -> bool:
    """True iff addr encodes to a supported scriptPubKey (legacy / bech32 / bech32m)."""
    try:
        address_to_script(addr)
        return True
    except (ValueError, KeyError, IndexError):
        return False
