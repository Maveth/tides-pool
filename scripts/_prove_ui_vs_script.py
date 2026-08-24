#!/usr/bin/env python3
"""Prove DATUM stock UI labels (bc1 / 1…) decode to the same scriptPubKey as TN4 (tb1 / m|n)."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tides_pool.addresses import (  # noqa: E402
    _BECH32_CHARSET,
    _bech32_hrp_expand,
    _bech32_polymod,
    _convertbits,
    address_to_script,
)


def b58encode(b: bytes) -> str:
    alphabet = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(b, "big")
    out = b""
    while n:
        n, r = divmod(n, 58)
        out = bytes([alphabet[r]]) + out
    pad = 0
    for c in b:
        if c == 0:
            pad += 1
        else:
            break
    return (alphabet[0:1] * pad + out).decode()


def script_to_addr_ui(script: bytes, *, testnet_ui: bool) -> str:
    if script.startswith(b"\x76\xa9\x14") and script.endswith(b"\x88\xac") and len(script) == 25:
        h160 = script[3:23]
        ver = 0x6F if testnet_ui else 0x00
        payload = bytes([ver]) + h160
        chk = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return b58encode(payload + chk)
    if script.startswith(b"\x00\x14") and len(script) == 22:
        hrp = "tb" if testnet_ui else "bc"
        prog = script[2:]
        data = [0] + _convertbits(list(prog), 8, 5, True)
        polymod = _bech32_polymod(_bech32_hrp_expand(hrp) + data + [0] * 6) ^ 1
        data = data + [(polymod >> 5 * (5 - i)) & 31 for i in range(6)]
        return hrp + "1" + "".join(_BECH32_CHARSET[d] for d in data)
    if script.startswith(b"\xa9\x14") and script.endswith(b"\x87") and len(script) == 23:
        h160 = script[2:22]
        ver = 0xC4 if testnet_ui else 0x05
        payload = bytes([ver]) + h160
        chk = hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
        return b58encode(payload + chk)
    raise ValueError(script.hex())


def main() -> None:
    rows = [
        ("ours A", "n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5"),
        ("ours B", "mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3"),
        ("friend", "tb1qp9saey2fnj5403xyz23akrlnz89985wwztvzta"),
        ("ops", "mqKdiu6W825MWc31NACiwxRchTb4dP2NRH"),
    ]
    print(f"{'who':8} {'scriptPubKey':44} {'house_UI':42} {'stock_UI':42} same_script?")
    for who, addr in rows:
        sc = address_to_script(addr)
        house = script_to_addr_ui(sc, testnet_ui=True)
        stock = script_to_addr_ui(sc, testnet_ui=False)
        back = address_to_script(stock)
        print(f"{who:8} {sc.hex():44} {house:42} {stock:42} {back == sc}")
    print()
    print("Block embeds script bytes from Prime, not the UI string.")
    print("Stock UI string -> script round-trip is identical.")
    print("Wrong pay only if outputs_count==0 (non-pooled) -> local mining.pool_address.")


if __name__ == "__main__":
    main()
