#!/usr/bin/env bash
# CRITICAL: Prime/SV1 restart drops ALL miners — some never return.
# Downtime must be under ~5s. ALWAYS tell the user and get explicit OK before restart.
# ALWAYS run import preflight first. Never restart on untested files.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTR="${PRIME_CTR:-deploy-tides-prime-1}"
SRC="$ROOT/src/tides_pool"
STAGE_HOST="${TMPDIR:-/tmp}/prime-preflight-$$"
STAGE_CTR="/tmp/prime-preflight-$$"

if [[ $# -gt 0 ]]; then
  FILES=("$@")
else
  FILES=(datum_prime.py store.py)
fi

cleanup() {
  rm -rf "$STAGE_HOST" 2>/dev/null || true
  sudo docker exec "$CTR" rm -rf "$STAGE_CTR" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== prime preflight (import smoke) ctr=$CTR ==="

if ! sudo docker inspect "$CTR" --format '{{.State.Status}}' 2>/dev/null | grep -qx running; then
  echo "FAIL: Prime container not running — refusing preflight/deploy" >&2
  exit 2
fi

# Live health before we even stage
if ! sudo docker exec "$CTR" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" 2>/dev/null; then
  echo "FAIL: Prime /health not OK — fix live first, do not deploy" >&2
  exit 2
fi

mkdir -p "$STAGE_HOST/tides_pool"
# Snapshot live package from container (exact deps Prime actually has)
sudo docker cp "$CTR:/app/src/tides_pool/." "$STAGE_HOST/tides_pool/"

for f in "${FILES[@]}"; do
  base="$(basename "$f")"
  host_f="$SRC/$base"
  if [[ ! -f "$host_f" ]]; then
    echo "FAIL: missing host file $host_f" >&2
    exit 1
  fi
  # Syntax check on host first
  python3 -c "import ast; ast.parse(open('$host_f').read())"
  cp -f "$host_f" "$STAGE_HOST/tides_pool/$base"
  echo "staged $base"
done

sudo docker cp "$STAGE_HOST" "$CTR:$STAGE_CTR"

# Import smoke inside container with staged tree first on PYTHONPATH
out="$(
sudo docker exec -i -e "STAGE=$STAGE_CTR" "$CTR" python3 <<'PY'
import os, sys
stage = os.environ["STAGE"]
sys.path.insert(0, stage)
# Drop cached app imports if any
for k in list(sys.modules):
    if k == "tides_pool" or k.startswith("tides_pool."):
        del sys.modules[k]
errors = []
for mod in (
    "tides_pool.block_confirm",
    "tides_pool.store",
    "tides_pool.datum_prime",
    "tides_pool.chain_sync",
    "tides_pool.api",
):
    try:
        __import__(mod)
        print(f"ok import {mod}", flush=True)
    except Exception as exc:
        errors.append(f"{mod}: {exc}")
        print(f"FAIL import {mod}: {exc}", flush=True)
if errors:
    raise SystemExit(1)
# Optional: nick helpers used by probe path
import tides_pool.datum_prime as dp
assert hasattr(dp.QuarantineGuard, "should_log_nick_pow_probe"), "missing should_log_nick_pow_probe"
from tides_pool.block_confirm import extract_secondary_tag
print("ok extract_secondary_tag", bool(extract_secondary_tag("xRIPTIDE.TestNick", "RIPTIDE")), flush=True)
print("PREFLIGHT_OK", flush=True)
PY
)"
echo "$out"
echo "$out" | grep -q 'PREFLIGHT_OK' || {
  echo "FAIL: preflight did not print PREFLIGHT_OK" >&2
  exit 1
}

echo "=== preflight PASSED — safe to copy+restart ==="
