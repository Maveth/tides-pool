#!/usr/bin/env python3
"""Patch DATUM Gateway output_script_2_addr to show testnet/TN4 addresses in UI."""
from pathlib import Path
import sys

p = Path(sys.argv[1] if len(sys.argv) > 1 else "/mnt/Alexandria/local/bip110-lab/datum-pow/src/datum_utils.c")
t = p.read_text()
if "version = 0x6F; /* testnet/TN4 P2PKH */" in t:
    print("already patched")
    sys.exit(0)

old = """\tif ((script[0] == 0xA9) || (script[0] == 0x76)) { // P2SH / P2PKH
\t\tif (script[0] == 0xA9) {
\t\t\tversion = 5;
\t\t\tptr = &script[2];
\t\t\tif (len != 23) return 0;
\t\t} else {
\t\t\tversion = 0;
\t\t\tptr = &script[3];
\t\t\tif (len != 25) return 0;
\t\t}"""

new = """\t/* BIP110 lab: display addresses as testnet/TN4 (m/n/2), not mainnet (1/3).
\t * Scripts themselves are network-agnostic; only the UI base58 version byte differs. */
\tif ((script[0] == 0xA9) || (script[0] == 0x76)) { // P2SH / P2PKH
\t\tif (script[0] == 0xA9) {
\t\t\tversion = 0xC4; /* testnet P2SH */
\t\t\tptr = &script[2];
\t\t\tif (len != 23) return 0;
\t\t} else {
\t\t\tversion = 0x6F; /* testnet/TN4 P2PKH */
\t\t\tptr = &script[3];
\t\t\tif (len != 25) return 0;
\t\t}"""

if old not in t:
    raise SystemExit("P2PKH/P2SH pattern not found")
t = t.replace(old, new, 1)
old2 = 'if (segwit_addr_encode(addr, "bc", version, &script[2], programLen) != 1)'
new2 = 'if (segwit_addr_encode(addr, "tb", version, &script[2], programLen) != 1)'
if old2 not in t:
    raise SystemExit("bech32 hrp pattern not found")
t = t.replace(old2, new2, 1)
p.write_text(t)
print(f"patched {p}")
