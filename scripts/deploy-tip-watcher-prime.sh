#!/usr/bin/env bash
# Deploy fast tip watcher into live Prime with minimal downtime.
#
# CRITICAL: Prime restart drops ALL DATUM sessions. Resume helps reconnects,
# but some miners still lag. Keep bounce under ~5–10s.
#
# Best-effort low-downtime sequence:
#   1) preflight import smoke (no restart)
#   2) ${DOCKER[@]} cp patched files into running container
#   3) ONLY after CONFIRM_PRIME_RESTART=1: ${DOCKER[@]} restart Prime (not web/GW)
#   4) wait /health + tip-watcher log line
#
# Usage:
#   bash scripts/deploy-tip-watcher-prime.sh          # preflight+cp only
#   CONFIRM_PRIME_RESTART=1 bash scripts/deploy-tip-watcher-prime.sh
set -euo pipefail
DOCKER=(sudo docker)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CTR="${PRIME_CTR:-deploy-tides-prime-1}"
SRC="$ROOT/src/tides_pool"
FILES=(datum_prime.py config.py)

for f in "${FILES[@]}"; do
  [[ -f "$SRC/$f" ]] || { echo "missing $SRC/$f" >&2; exit 1; }
done

echo "=== preflight (must pass — no restart yet) ==="
bash "$ROOT/scripts/prime-preflight-import.sh" "${FILES[@]}"

echo "=== ${DOCKER[@]} cp into Prime (live process still on OLD code until restart) ==="
for f in "${FILES[@]}"; do
  ${DOCKER[@]} cp "$SRC/$f" "$CTR:/app/src/tides_pool/$f"
  echo "copied $f"
done

if [[ "${CONFIRM_PRIME_RESTART:-}" != "1" ]]; then
  echo
  echo "STAGED ONLY. Live Prime still runs previous code until restart."
  echo "When ready (user OK): CONFIRM_PRIME_RESTART=1 $0"
  echo "Expect ~3–10s miner reconnects; resume tokens should limit empty-blast."
  exit 0
fi

echo "=== restart Prime only (web/GW untouched) ==="
t0=$(date +%s%3N)
${DOCKER[@]} restart "$CTR"
ok=0
for i in $(seq 1 60); do
  if ${DOCKER[@]} exec "$CTR" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2)" 2>/dev/null; then
    echo "prime health ok ($(( ($(date +%s%3N) - t0) ))ms)"
    ok=1
    break
  fi
  sleep 0.5
done
if [[ "$ok" != 1 ]]; then
  echo "FAIL: Prime unhealthy after restart" >&2
  ${DOCKER[@]} logs --tail 50 "$CTR" >&2 || true
  exit 1
fi

echo "=== verify tip watcher started ==="
# give bg tasks a moment
sleep 2
if ${DOCKER[@]} logs --since 2m "$CTR" 2>&1 | grep -q "tip watcher started"; then
  echo "OK: tip watcher log seen"
else
  echo "WARN: tip watcher log not seen yet — check TIDES_TIP_POLL_SECONDS / logs" >&2
  ${DOCKER[@]} logs --tail 30 "$CTR" 2>&1 | grep -iE 'tip|coinbaser|listening' || true
fi
${DOCKER[@]} exec "$CTR" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).read()[:220])"
echo "done"
