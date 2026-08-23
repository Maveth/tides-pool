#!/bin/bash
set -euo pipefail
python3 <<'PY'
import json
p = "/mnt/Alexandria/local/tides-pool/deploy/datum-pool/config.json"
d = json.load(open(p))
d["datum"]["pool_pubkey"] = ""
d["datum"]["pool_host"] = "tides.maveth.ca"
d["datum"]["pool_port"] = 28916
json.dump(d, open(p, "w"), indent=2)
print("pool_host", d["datum"]["pool_host"])
print("pool_pubkey empty?", d["datum"]["pool_pubkey"] == "")
PY
bash /mnt/Alexandria/local/tides-pool/deploy/datum-pool/start-datum-pool.sh
sleep 10
sudo docker logs bip110-datum-pool 2>&1 | tail -80 | grep -iE 'Auto-fetch|pubkey|MOTD|configure|Min Diff|ERROR|WARN|handshake|missing'
