#!/bin/sh
# Split snaps: main-page (pool) ~5 min; miner pages less often (~15 min).
# Keeps previous files so browser refresh never blanks.
set -eu
POOL_INTERVAL="${LAB_SNAP_INTERVAL_SEC:-300}"
USER_INTERVAL="${LAB_SNAP_USER_INTERVAL_SEC:-900}"
POLL="${LAB_FIND_POLL_SEC:-15}"
MIN_REST="${LAB_SNAP_MIN_REST_SEC:-30}"
LIVE="${LAB_LIVE_WEB:-http://deploy-tides-web-1:8080}"
SNAP_DIR="${LAB_SNAP_DIR:-/app/snapshots}"
mkdir -p "$SNAP_DIR"

echo "snapshot_loop pool=${POOL_INTERVAL}s users=${USER_INTERVAL}s min_rest=${MIN_REST}s poll=${POLL}s"

last_h=""
last_pool_start=0
last_pool_end=0
last_user_start=0
last_user_end=0

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
  mode="$1"
  reason="$2"
  start=$(date +%s)
  echo "=== snapshot begin $(date -u +%Y-%m-%dT%H:%M:%SZ) mode=${mode} reason=${reason} ==="
  if LAB_SNAP_MODE="$mode" python -m lab_web.build_snapshots; then
    echo "=== snapshot ok mode=${mode} ==="
  else
    echo "=== snapshot FAILED mode=${mode} (keeping previous files) ===" >&2
  fi
  end=$(date +%s)
  echo "=== snapshot elapsed $((end - start))s mode=${mode} ==="
  if [ "$mode" = "pool" ]; then
    last_pool_start=$start
    last_pool_end=$end
  else
    last_user_start=$start
    last_user_end=$end
  fi
}

run_snap pool "startup"
run_snap users "startup"

while true; do
  h=$(get_height | tr -d '\r')
  trigger=""
  if [ -f "$SNAP_DIR/trigger_refresh" ]; then
    trigger=1
    rm -f "$SNAP_DIR/trigger_refresh"
  fi

  now=$(date +%s)
  do_pool=""
  do_users=""
  pool_reason=""
  user_reason=""

  if [ -n "$trigger" ]; then
    do_pool=1
    pool_reason="trigger_refresh"
  fi
  if [ -n "$h" ] && [ -n "$last_h" ] && [ "$h" != "$last_h" ]; then
    do_pool=1
    pool_reason="new_find_${h}_was_${last_h}"
  fi
  if [ -n "$h" ]; then
    last_h="$h"
  fi

  if [ -z "$do_pool" ] && [ "$last_pool_start" -gt 0 ]; then
    if [ $((now - last_pool_start)) -ge "$POOL_INTERVAL" ] && [ $((now - last_pool_end)) -ge "$MIN_REST" ]; then
      do_pool=1
      pool_reason="interval_${POOL_INTERVAL}s"
    fi
  fi
  if [ "$last_user_start" -gt 0 ]; then
    if [ $((now - last_user_start)) -ge "$USER_INTERVAL" ] && [ $((now - last_user_end)) -ge "$MIN_REST" ]; then
      do_users=1
      user_reason="interval_${USER_INTERVAL}s"
    fi
  fi

  if [ -n "$do_pool" ]; then
    run_snap pool "$pool_reason"
  fi
  if [ -n "$do_users" ]; then
    run_snap users "$user_reason"
  fi
  sleep "$POLL"
done
