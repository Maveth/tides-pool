#!/bin/bash
set -euo pipefail
sudo docker exec deploy-postgres-1 psql -U tides -d tides -c "DELETE FROM finder_credits WHERE from_height = 999001;"
sudo docker exec deploy-postgres-1 psql -U tides -d tides -c "DELETE FROM blocks WHERE height = 999001 OR block_hash LIKE 'lab-%';"
sudo docker exec deploy-postgres-1 psql -U tides -d tides -c "DELETE FROM meta WHERE key = 'last_height' AND value = '999001';"
sudo docker exec deploy-postgres-1 psql -U tides -d tides -c "SELECT count(*) AS blocks FROM blocks;"
sudo docker exec deploy-postgres-1 psql -U tides -d tides -c "SELECT count(*) AS open_credits FROM finder_credits WHERE paid_in_height IS NULL;"
# restart API so any in-memory last_height is gone (uses postgres)
cd /mnt/Alexandria/local/tides-pool/deploy
sudo docker compose restart tides-pool
sleep 3
curl -sS -X POST http://127.0.0.1:8088/api/admin/resync-chain
echo
echo "=== blocks api ==="
curl -sS http://127.0.0.1:8088/api/blocks
echo
echo "=== stats ==="
curl -sS http://127.0.0.1:8088/api/stats
echo
