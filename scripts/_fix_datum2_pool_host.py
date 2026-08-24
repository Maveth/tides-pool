#!/usr/bin/env python3
"""Point DATUM2 pool_host at LAN Prime IP (no DNS / public name)."""
from __future__ import annotations

import json
import sys

PATH = "/mnt/Alexandria/local/tides-pool/deploy/datum-pool/config.json"
HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.143"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 28916

d = json.load(open(PATH))
print("BEFORE", d.get("datum"))
d.setdefault("datum", {})
d["datum"]["pool_host"] = HOST
d["datum"]["pool_port"] = PORT
d["datum"]["pool_pubkey"] = ""  # re-fetch / use handshake
json.dump(d, open(PATH, "w"), indent=2)
print("AFTER", json.load(open(PATH))["datum"])
