#!/bin/bash
# Sync lab tree, ensure snapshot-refresher (5m) is up, sync static.
set -euo pipefail
ROOT=/mnt/Alexandria/bitcoin/lab-website
if [ ! -d "$ROOT/src" ] && [ -d /mnt/mnt/Alexandria/bitcoin/lab-website ]; then
  ROOT=/mnt/mnt/Alexandria/bitcoin/lab-website
fi
SRC=/tmp/bip110-lab-website
test -d "$SRC/src/lab_web"
test -f "$SRC/deploy/snapshot_loop.sh"
echo "ROOT=$ROOT"
mkdir -p "$ROOT"/{src,snapshots,deploy,static}
rsync -a --delete \
  --exclude snapshots/ \
  --exclude logs/ \
  "$SRC/src/" "$ROOT/src/"
rsync -a --delete "$SRC/static/" "$ROOT/static/"
rsync -a "$SRC/deploy/" "$ROOT/deploy/"
chmod +x "$ROOT/deploy/snapshot_loop.sh"

cd "$ROOT/deploy"
docker compose -p lab-website build lab-web
docker compose -p lab-website up -d lab-web snapshot-refresher

sleep 2
docker ps --filter name=lab-website --format '{{.Names}} {{.Status}}'
curl -sS -m 8 http://127.0.0.1:8090/ | grep -oE 'snap5m|snapFreshness|5 min|live APIs on' | sort -u || true
curl -sS -m 8 http://127.0.0.1:8090/api/meta | head -c 280; echo
echo LAB_SNAP5M_UP
