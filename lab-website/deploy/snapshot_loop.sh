#!/bin/sh
# Rebuild snapshots on a fixed cadence (default 5 minutes from loop start).
set -eu
INTERVAL="${LAB_SNAP_INTERVAL_SEC:-300}"
echo "snapshot_loop interval=${INTERVAL}s starting"
while true; do
  start=$(date +%s)
  echo "=== snapshot begin $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  if python -m lab_web.build_snapshots; then
    echo "=== snapshot ok ==="
  else
    echo "=== snapshot FAILED (keeping previous files) ===" >&2
  fi
  end=$(date +%s)
  elapsed=$((end - start))
  wait=$((INTERVAL - elapsed))
  if [ "$wait" -lt 5 ]; then
    wait=5
  fi
  echo "=== sleep ${wait}s (elapsed ${elapsed}s, target ${INTERVAL}s) ==="
  sleep "$wait"
done
