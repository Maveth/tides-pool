#!/bin/bash
# Cut over public :8088 to 5-min snapshot site (lab-website).
# Live tides-web APIs move to host :8087 (docker internal :8080 unchanged for snapper).
set -euo pipefail

DEPLOY=/mnt/Alexandria/local/tides-pool/deploy
SPLIT="$DEPLOY/docker-compose.split.yml"
ROOT=/mnt/Alexandria/bitcoin/lab-website
if [ ! -d "$ROOT/deploy" ] && [ -d /mnt/mnt/Alexandria/bitcoin/lab-website ]; then
  ROOT=/mnt/mnt/Alexandria/bitcoin/lab-website
fi
SRC=/tmp/bip110-lab-website
TS=$(date -u +%Y%m%dT%H%M%SZ)

test -f "$SPLIT"
test -d "$SRC/deploy"

echo "=== sync lab-website tree ==="
mkdir -p "$ROOT"/{src,snapshots,deploy,static}
rsync -a --delete --exclude snapshots/ --exclude logs/ "$SRC/src/" "$ROOT/src/"
rsync -a --delete "$SRC/static/" "$ROOT/static/"
rsync -a "$SRC/deploy/" "$ROOT/deploy/"
chmod +x "$ROOT/deploy/snapshot_loop.sh"

echo "=== move tides-web host port 8088 -> 8087 ==="
cp -a "$SPLIT" "$SPLIT.bak.snap8088.$TS"
if grep -q '"8087:8080"' "$SPLIT" || grep -q '8087:8080' "$SPLIT"; then
  echo "tides-web already on 8087"
else
  # only the tides-web publish line (8088:8080 under tides-web service)
  python3 - <<'PY'
from pathlib import Path
p = Path("/mnt/Alexandria/local/tides-pool/deploy/docker-compose.split.yml")
t = p.read_text(encoding="utf-8")
old = t
# Replace first/only host mapping 8088:8080 (tides-web)
if "8088:8080" not in t:
    raise SystemExit("8088:8080 not found in split compose")
t = t.replace("8088:8080", "8087:8080", 1)
if t == old:
    raise SystemExit("no change")
p.write_text(t, encoding="utf-8")
print("patched split compose 8088->8087")
PY
fi

cd "$DEPLOY"
# Recreate ONLY tides-web so Prime stays up
docker compose -f docker-compose.yml -f docker-compose.split.yml up -d --no-deps --force-recreate tides-web

echo "=== wait live API on :8087 ==="
for i in $(seq 1 40); do
  code=$(curl -sS -m 4 -o /dev/null -w '%{http_code}' http://127.0.0.1:8087/api/stats || echo 000)
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo "live :8087 /api/stats -> $code"
[[ "$code" == "200" ]]

echo "=== bring snapshot site onto :8088 (+ :8090) ==="
cd "$ROOT/deploy"
docker compose -p lab-website build lab-web
docker compose -p lab-website up -d lab-web snapshot-refresher

echo "=== wait public :8088 ==="
for i in $(seq 1 40); do
  code=$(curl -sS -m 4 -o /dev/null -w '%{http_code}' http://127.0.0.1:8088/ || echo 000)
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo "public :8088 / -> $code"
[[ "$code" == "200" ]]

echo "=== smoke ==="
curl -sS -m 8 http://127.0.0.1:8088/ | grep -oE 'snap5m|snapFreshness|23337|DATUM preferred|lab-banner' | sort | uniq -c || true
curl -sS -m 8 http://127.0.0.1:8088/api/meta | head -c 220; echo
curl -sS -m 8 http://127.0.0.1:8088/api/health | python3 -c 'import sys,json;d=json.load(sys.stdin);print("8088 lab_website",d.get("lab_website"),"snap",d.get("snapshot_as_of"))'
curl -sS -m 8 -o /dev/null -w "8087 stats %{http_code}\n" http://127.0.0.1:8087/api/stats
curl -sS -m 8 -o /dev/null -w "8087 user %{http_code}\n" http://127.0.0.1:8087/api/user/bc1qy9ms6fd0fht9g624ap42wln7ut56pcrrksxhvt
curl -sS -m 8 -o /dev/null -w "8090 alias %{http_code}\n" http://127.0.0.1:8090/
docker ps --format '{{.Names}} {{.Ports}}' | grep -E 'tides-web|lab-website' || true
echo CUTOVER_OK
