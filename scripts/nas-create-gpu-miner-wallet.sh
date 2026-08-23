#!/bin/bash
# Separate GPU-miner payout wallet (NOT ops mfh5a…, NOT maveth mqMY6…)
set -euo pipefail
CLI=(sudo docker exec bip110-knots-testnet4 bitcoin-cli -testnet4 -rpcuser=datum -rpcpassword=YOUR_LAB_PASSWORD -rpcport=48332)

"${CLI[@]}" createwallet "tides_gpu1_tn4" false false "" false true true 2>/dev/null || \
  echo "wallet may already exist"

ADDR=$("${CLI[@]}" -rpcwallet=tides_gpu1_tn4 getnewaddress "tides-gpu1" legacy)
echo "ADDR=${ADDR}"
"${CLI[@]}" -rpcwallet=tides_gpu1_tn4 getaddressinfo "${ADDR}"
"${CLI[@]}" -rpcwallet=tides_gpu1_tn4 listdescriptors true > /tmp/tides_gpu1_tn4_descriptors.json
"${CLI[@]}" -rpcwallet=tides_gpu1_tn4 backupwallet "/tmp/tides_gpu1_tn4_backup.dat"
sudo docker cp bip110-knots-testnet4:/tmp/tides_gpu1_tn4_backup.dat /tmp/tides_gpu1_tn4_backup.dat 2>/dev/null || true
sudo docker cp bip110-knots-testnet4:/tmp/tides_gpu1_tn4_descriptors.json /tmp/tides_gpu1_tn4_descriptors.json 2>/dev/null || true

cat > /tmp/tides_gpu1_tn4_meta.json <<EOF
{
  "wallet_name": "tides_gpu1_tn4",
  "network": "testnet4",
  "miner_address": "${ADDR}",
  "address_type": "legacy",
  "role": "GPU stratum user / TIDES miner payout (not pool ops)",
  "ops_address": "mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3",
  "maveth_solo": "mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72"
}
EOF
echo "${ADDR}" > /tmp/tides_gpu1_tn4_address.txt
sudo chmod 644 /tmp/tides_gpu1_tn4_backup.dat 2>/dev/null || true
sudo cp -f /tmp/tides_gpu1_tn4_backup.dat /mnt/Alexandria/local/tides-pool/tides_gpu1_tn4_backup.dat
sudo chown truenas_admin:truenas_admin /mnt/Alexandria/local/tides-pool/tides_gpu1_tn4_backup.dat
ls -la /tmp/tides_gpu1_tn4_*
echo "READY_ADDR=${ADDR}"
