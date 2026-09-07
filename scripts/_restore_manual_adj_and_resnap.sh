#!/bin/bash
# Restore tides-web api.py overlay (manual_adjustment) wiped by force-recreate,
# then refresh public snapshot so :8088 gets the payout tables.
set -euo pipefail
WEB=deploy-tides-web-1
APP=/app/src/tides_pool
ALX=/mnt/Alexandria/local/tides-pool/src/tides_pool

test -f "$ALX/api.py"
grep -q 'manual_adjustment_' "$ALX/api.py"

echo "=== docker cp api.py overlay ==="
cp -a "$ALX/api.py" "$ALX/api.py.bak.pre-restore-adj.$(date -u +%Y%m%dT%H%M%SZ)"
docker cp "$ALX/api.py" "$WEB:$APP/api.py"
docker restart "$WEB"

echo "=== wait :8087 ==="
for i in $(seq 1 40); do
  code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8087/api/stats || echo 000)
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo "8087 stats $code"
[[ "$code" == "200" ]]

echo "=== verify manual_adjustment on live API ==="
python3 - <<'PY'
import json, urllib.request
raw = urllib.request.urlopen("http://127.0.0.1:8087/api/blocks?limit=30", timeout=60).read()
rows = json.loads(raw)
n = sum(1 for b in rows if b.get("manual_adjustment"))
print(f"blocks={len(rows)} with_adj={n}")
for b in rows:
    adj = b.get("manual_adjustment")
    if adj and isinstance(adj.get("pays"), list) and adj["pays"]:
        print(f"ok height={b.get('height')} pays={len(adj['pays'])} title={adj.get('title')}")
        break
else:
    raise SystemExit("no manual_adjustment.pays found — overlay incomplete?")
PY

echo "=== one-shot snapshot rebuild for public :8088 ==="
cd /mnt/Alexandria/bitcoin/lab-website/deploy
docker compose -p lab-website run --rm snapshot-builder

echo "=== verify snap has adj ==="
python3 - <<'PY'
import json, urllib.request
raw = urllib.request.urlopen("http://127.0.0.1:8088/api/blocks?limit=30", timeout=30).read()
rows = json.loads(raw)
n = sum(1 for b in rows if b.get("manual_adjustment"))
print(f"snap blocks={len(rows)} with_adj={n}")
for b in rows:
    adj = b.get("manual_adjustment")
    if adj and adj.get("pays"):
        print(f"snap ok height={b.get('height')} pays={len(adj['pays'])}")
        break
else:
    raise SystemExit("snap still missing manual_adjustment")
PY
echo RESTORE_OK
