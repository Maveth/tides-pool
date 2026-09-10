#!/usr/bin/env bash
# CRITICAL: Prime/SV1 restart drops ALL miners — some never return.
# Downtime must be under ~5s. ALWAYS tell the user and get explicit OK before restart.
# ALWAYS run import preflight first. Never restart on untested files.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTR="${PRIME_CTR:-deploy-tides-prime-1}"
SRC="$ROOT/src/tides_pool"
FILES=(datum_prime.py store.py)

for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done

echo "=== preflight (must pass or we abort — no restart) ==="
bash "$ROOT/scripts/prime-preflight-import.sh" "${FILES[@]}"

echo "=== copy into Prime ==="
for f in "${FILES[@]}"; do
  docker cp "$SRC/$f" "$CTR:/app/src/tides_pool/$f"
done

echo "=== restart Prime only ==="
if [[ "${CONFIRM_PRIME_RESTART:-}" != "1" ]]; then
  echo "REFUSING restart: set CONFIRM_PRIME_RESTART=1 after user explicit OK (miners drop on bounce)." >&2
  exit 3
fi
t0=$(date +%s)
docker restart "$CTR"
ok=0
for i in $(seq 1 45); do
  if docker exec "$CTR" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" 2>/dev/null; then
    echo "prime health ok ($(( $(date +%s) - t0 ))s)"
    ok=1
    break
  fi
  sleep 1
done
if [[ "$ok" != 1 ]]; then
  echo "FAIL: Prime did not become healthy within 45s" >&2
  docker logs --tail 40 "$CTR" >&2 || true
  exit 1
fi
docker exec "$CTR" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()[:180])"
echo "done"
