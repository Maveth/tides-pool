#!/bin/bash
# Park Fly pool GW on 23346/7165; bring CONVOY up on 23336/7155.
# Requires image datum_gateway:convoy-b9ea7dc and prepared configs.
set -euo pipefail
IMG=datum_gateway:convoy-b9ea7dc
FLY_IMG=bip110-datum-pow:fly-pr17-121edd0
CONVOY_CFG=/mnt/Alexandria/local/tides-pool/deploy/datum-pool-convoy/config.json
PARK_CFG=/mnt/Alexandria/local/tides-pool/deploy/datum-pool-fly-parked/config.json
LOGS=/mnt/Alexandria/local/tides-pool/datum-pool-logs
SUB=/tmp/datum_submitblocks_tides

if ! sudo docker image inspect "$IMG" >/dev/null 2>&1; then
  echo "FATAL: missing image $IMG — wait for build"
  tail -30 /mnt/Alexandria/local/bip110-lab/build-convoy-datum.log || true
  exit 1
fi
test -f "$CONVOY_CFG"
test -f "$PARK_CFG"
mkdir -p "$LOGS" "$SUB"

echo "=== stop current pool GW ==="
sudo docker stop bip110-datum-pool || true
# rename old so we can reuse the name for CONVOY
if sudo docker ps -aq --filter name=^bip110-datum-pool$ | grep -q .; then
  sudo docker rename bip110-datum-pool bip110-datum-pool-fly-pre-convoy || true
fi
# remove any previous parked/convoy leftovers
sudo docker rm -f bip110-datum-pool-fly-parked bip110-datum-pool 2>/dev/null || true

echo "=== start parked Fly on 23346/7165 ==="
sudo docker run -d --name bip110-datum-pool-fly-parked \
  --network host --restart unless-stopped \
  -v "$PARK_CFG:/app/config/config.json:ro" \
  -v "$LOGS:/var/log/datum" \
  -v "$SUB:/tmp/datum_submitblocks_tides" \
  "$FLY_IMG"
sleep 2

echo "=== start CONVOY on 23336/7155 (name bip110-datum-pool) ==="
# Override image HEALTHCHECK (defaults to :23334); this GW uses stratum :23336 + API :7155.
sudo docker run -d --name bip110-datum-pool \
  --network host --restart unless-stopped \
  --health-cmd='nc -z 127.0.0.1 23336 && nc -z 127.0.0.1 7155 || exit 1' \
  --health-interval=30s \
  --health-timeout=5s \
  --health-retries=3 \
  --health-start-period=20s \
  -v "$CONVOY_CFG:/app/config/config.json:ro" \
  -v "$LOGS:/var/log/datum" \
  -v "$SUB:/tmp/datum_submitblocks_tides" \
  "$IMG"
sleep 5

echo "=== status ==="
sudo docker ps --filter name=bip110-datum-pool --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
sudo ss -lptn | grep -E '23336|23346|7155|7165' || true
echo "=== CONVOY logs ==="
sudo docker logs --tail 40 bip110-datum-pool 2>&1 | tail -40
echo "=== tides health ==="
curl -sS -m 10 http://127.0.0.1:8088/api/health | head -c 350; echo
echo "=== prime configure lines (recent) ==="
sudo docker logs deploy-tides-pool-1 --since 3m 2>&1 | grep -E 'configure v3-abw-off|configure v1|b9ea7dc|handshake OK' | tail -25 || true
echo CUTOVER_OK
