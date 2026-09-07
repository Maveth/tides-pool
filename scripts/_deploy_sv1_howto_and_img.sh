#!/bin/bash
set -euo pipefail
ROOT=/mnt/Alexandria/bitcoin/lab-website
SRC=/tmp/bip110-lab-website
ALX_STATIC=/mnt/Alexandria/local/tides-pool/src/tides_pool/static

rsync -a --delete "$SRC/static/" "$ROOT/static/"
# restore missing howto screenshot from live tides static
if [[ ! -f "$ROOT/static/pool_pass_full.png" ]]; then
  cp -a "$ALX_STATIC/pool_pass_full.png" "$ROOT/static/pool_pass_full.png"
  echo "copied pool_pass_full.png"
else
  # still refresh from ALX if present (authoritative)
  if [[ -f "$ALX_STATIC/pool_pass_full.png" ]]; then
    cp -a "$ALX_STATIC/pool_pass_full.png" "$ROOT/static/pool_pass_full.png"
    echo "refreshed pool_pass_full.png"
  fi
fi
ls -la "$ROOT/static/pool_pass_full.png"
# static is bind-mounted — no recreate needed
curl -sS -m 8 -o /dev/null -w "img %{http_code}\n" http://127.0.0.1:8088/static/pool_pass_full.png
curl -sS -m 8 http://127.0.0.1:8088/ | grep -oE 'joinSv1Box|riptide\.maveth\.ca:23337|pool_pass_full|sv1howto1|password = x' | sort -u
echo DONE
