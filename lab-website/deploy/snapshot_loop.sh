#!/bin/sh
# Rebuild snapshots on a 5-minute cadence, and immediately when a new pool find
# appears (or when lab-web writes snapshots/trigger_refresh).
set -eu
INTERVAL="${LAB_SNAP_INTERVAL_SEC:-300}"
POLL="${LAB_FIND_POLL_SEC:-15}"
LIVE="${LAB_LIVE_WEB:-http://deploy-tides-web-1:8080}"
SNAP_DIR="${LAB_SNAP_DIR:-/app/snapshots}"
mkdir -p "$SNAP_DIR"

echo "snapshot_loop interval=${INTERVAL}s find_poll=${POLL}s live=${LIVE}"

last_h=""
last_run=0

get_height() {
  python3 - <<PY
import json, urllib.request
try:
    with urllib.request.urlopen("${LIVE}/api/stats", timeout=8) as r:
        d = json.loads(r.read().decode())
    h = d.get("last_pool_block_height")
    print(h if h is not None else "")
except Exception:
    print("")
PY
}

run_snap() {
  reason="$1"
  start=$(date +%s)
  echo "=== snapshot begin $(date -u +%Y-%m-%dT%H:%M:%SZ) reason=${reason} ==="
  if python -m lab_web.build_snapshots; then
    echo "=== snapshot ok ==="
  else
    echo "=== snapshot FAILED (keeping previous files) ===" >&2
  fi
  last_run=$(date +%s)
  echo "=== snapshot elapsed $((last_run - start))s ==="
}

# Initial full snap so the site is never empty after restart.
run_snap "startup"

while true; do
  h=$(get_height | tr -d '\r')
  trigger=""
  if [ -f "$SNAP_DIR/trigger_refresh" ]; then
    trigger=1
    rm -f "$SNAP_DIR/trigger_refresh"
  fi

  need=""
  reason=""
  if [ -n "$trigger" ]; then
    need=1
    reason="trigger_refresh"
  fi
  if [ -n "$h" ] && [ -n "$last_h" ] && [ "$h" != "$last_h" ]; then
    need=1
    reason="new_find_${h}_was_${last_h}"
  fi
  if [ -n "$h" ]; then
    last_h="$h"
  fi

  now=$(date +%s)
  elapsed=$((now - last_run))
  if [ -z "$need" ] && [ "$elapsed" -ge "$INTERVAL" ]; then
    need=1
    reason="interval_${INTERVAL}s"
  fi

  if [ -n "$need" ]; then
    run_snap "$reason"
  fi
  sleep "$POLL"
done
