#!/bin/bash
# 1) Restore tides-web models.py+api.py overlays (manual_adjustment field)
# 2) Redeploy public lab-web with live cb_type + adj overlays
set -euo pipefail
WEB=deploy-tides-web-1
APP=/app/src/tides_pool
ALX=/mnt/Alexandria/local/tides-pool/src/tides_pool
ROOT=/mnt/Alexandria/bitcoin/lab-website
SRC=/tmp/bip110-lab-website
TS=$(date -u +%Y%m%dT%H%M%SZ)

echo "=== restore tides-web overlays ==="
grep -q 'manual_adjustment' "$ALX/models.py"
grep -q 'manual_adjustment_' "$ALX/api.py"
cp -a "$ALX/api.py" "$ALX/api.py.bak.fixoverlay.$TS"
cp -a "$ALX/models.py" "$ALX/models.py.bak.fixoverlay.$TS"
docker cp "$ALX/api.py" "$WEB:$APP/api.py"
docker cp "$ALX/models.py" "$WEB:$APP/models.py"
# also static not required for public snap site
docker restart "$WEB"

echo "=== wait :8087 ==="
for i in $(seq 1 40); do
  code=$(curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:8087/api/stats || echo 000)
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo "8087 $code"

echo "=== live API adj check ==="
python3 - <<'PY'
import json, urllib.request
raw = urllib.request.urlopen("http://127.0.0.1:8087/api/blocks?limit=20", timeout=90).read()
rows = json.loads(raw)
n = sum(1 for b in rows if b.get("manual_adjustment"))
print(f"live with_adj={n}/{len(rows)}")
for b in rows:
    adj = b.get("manual_adjustment") or {}
    pays = adj.get("pays") if isinstance(adj, dict) else None
    if pays:
        print(f"  height={b['height']} pays={len(pays)} title={adj.get('title')}")
        break
else:
    print("WARN live still missing adj — dash cache? will rely on snap-site DB overlay")
PY

echo "=== sync + rebuild public site ==="
rsync -a --delete "$SRC/src/" "$ROOT/src/"
rsync -a "$SRC/deploy/" "$ROOT/deploy/"
cd "$ROOT/deploy"
docker compose -p lab-website build lab-web
docker compose -p lab-website up -d lab-web

sleep 2
echo "=== public overlays ==="
python3 - <<'PY'
import json, urllib.request
# contrib cb_type
req = urllib.request.Request("http://127.0.0.1:8088/api/contributors?limit=5")
raw = urllib.request.urlopen(req, timeout=30).read()
rows = json.loads(raw)
print("contrib sample:")
for r in rows[:5]:
    print(" ", r.get("nickname"), r.get("cb_type_status"), (r.get("cb_type_tip") or "")[:70])
# blocks adj
raw = urllib.request.urlopen("http://127.0.0.1:8088/api/blocks?limit=20", timeout=30).read()
brows = json.loads(raw)
n = sum(1 for b in brows if b.get("manual_adjustment"))
print(f"snap-site with_adj={n}/{len(brows)}")
for b in brows:
    adj = b.get("manual_adjustment") or {}
    if isinstance(adj, dict) and adj.get("pays"):
        print(f"  height={b['height']} pays={len(adj['pays'])} status={adj.get('status')}")
        break
h = urllib.request.urlopen("http://127.0.0.1:8088/api/health", timeout=15).read()
hh = json.loads(h)
print("live_overlays", hh.get("live_overlays"))
PY
echo FIX_OK
