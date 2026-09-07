#!/bin/bash
set -euo pipefail
ROOT=/mnt/Alexandria/bitcoin/lab-website
SRC=/tmp/bip110-lab-website
rsync -a --delete "$SRC/static/" "$ROOT/static/"
# keep howto image if somehow missing
if [[ ! -f "$ROOT/static/pool_pass_full.png" && -f /mnt/Alexandria/local/tides-pool/src/tides_pool/static/pool_pass_full.png ]]; then
  cp -a /mnt/Alexandria/local/tides-pool/src/tides_pool/static/pool_pass_full.png "$ROOT/static/"
fi
curl -sS -m 8 http://127.0.0.1:8088/ | grep -oE 'sv1howto2|strongly discouraged|preferred · 0% fees|fee-warn-red|Variable work fee' | sort -u
curl -sS -m 8 -o /dev/null -w "img %{http_code}\n" http://127.0.0.1:8088/static/pool_pass_full.png
echo DONE
