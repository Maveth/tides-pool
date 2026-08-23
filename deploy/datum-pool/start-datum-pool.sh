#!/bin/bash
# Secondary DATUM Gateway for tides-pool (does not touch bip110-datum-pow :23335)
set -euo pipefail
ROOT=/mnt/Alexandria/local/tides-pool
CFG_DIR="${ROOT}/deploy/datum-pool"
LOG_DIR="${ROOT}/datum-pool-logs"
SUBMIT_DIR=/tmp/datum_submitblocks_tides

mkdir -p "${LOG_DIR}" "${SUBMIT_DIR}"
# DATUM panics if it cannot open the log file
chmod 777 "${LOG_DIR}" "${SUBMIT_DIR}" || true
touch "${LOG_DIR}/gateway-pool.log" || true
chmod 666 "${LOG_DIR}/gateway-pool.log" || true
sudo docker rm -f bip110-datum-pool 2>/dev/null || true


sudo docker run -d --name bip110-datum-pool --restart unless-stopped --network host \
  -v "${CFG_DIR}/config.json:/app/config/config.json:ro" \
  -v "${LOG_DIR}:/var/log/datum" \
  -v "${SUBMIT_DIR}:${SUBMIT_DIR}" \
  bip110-datum-pow:lab

sleep 2
sudo docker ps --filter name=bip110-datum-pool --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
sudo docker logs --tail 30 bip110-datum-pool || true
echo "Stratum: tcp://192.168.0.143:23336  API: http://192.168.0.143:7155"
