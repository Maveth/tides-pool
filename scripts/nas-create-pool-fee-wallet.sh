#!/bin/bash
# Dedicated POOL FEE wallet (2% keep). Not miner identity, not MaVeTh solo.
set -euo pipefail
CLI=(sudo docker exec bip110-knots-testnet4 bitcoin-cli -testnet4 -rpcuser=datum -rpcpassword=YOUR_LAB_PASSWORD -rpcport=48332)

"${CLI[@]}" createwallet "tides_pool_fee_tn4" false false "" false true true 2>/dev/null || \
  echo "wallet may already exist"

ADDR=$("${CLI[@]}" -rpcwallet=tides_pool_fee_tn4 getnewaddress "tides-pool-fee" legacy)
echo "ADDR=${ADDR}"
"${CLI[@]}" -rpcwallet=tides_pool_fee_tn4 getaddressinfo "${ADDR}"
"${CLI[@]}" -rpcwallet=tides_pool_fee_tn4 listdescriptors true > /tmp/tides_pool_fee_tn4_descriptors.json
"${CLI[@]}" -rpcwallet=tides_pool_fee_tn4 backupwallet "/tmp/tides_pool_fee_tn4_backup.dat"
sudo docker cp bip110-knots-testnet4:/tmp/tides_pool_fee_tn4_backup.dat /tmp/tides_pool_fee_tn4_backup.dat 2>/dev/null || true
sudo docker cp bip110-knots-testnet4:/tmp/tides_pool_fee_tn4_descriptors.json /tmp/tides_pool_fee_tn4_descriptors.json 2>/dev/null || true

cat > /tmp/tides_pool_fee_tn4_meta.json <<EOF
{
  "wallet_name": "tides_pool_fee_tn4",
  "network": "testnet4",
  "fee_address": "${ADDR}",
  "address_type": "legacy",
  "role": "Pool fee keep (2% of block). Finder bonus 8% goes to previous block finder.",
  "fee_bps": 1000,
  "ops_keep_of_fee_bps": 2000,
  "finder_of_fee_bps": 8000,
  "notes": {
    "gpu_miner": "n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5",
    "old_ops_now_miner_b": "mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3",
    "maveth_solo": "mqMY6RozgMuiV4SsoQShd2Rn57JT7wTe72"
  }
}
EOF
echo "${ADDR}" > /tmp/tides_pool_fee_tn4_address.txt
sudo chmod 644 /tmp/tides_pool_fee_tn4_backup.dat 2>/dev/null || true
sudo cp -f /tmp/tides_pool_fee_tn4_backup.dat /mnt/Alexandria/local/tides-pool/tides_pool_fee_tn4_backup.dat
sudo chown truenas_admin:truenas_admin /mnt/Alexandria/local/tides-pool/tides_pool_fee_tn4_backup.dat
ls -la /tmp/tides_pool_fee_tn4_*
echo "READY_ADDR=${ADDR}"
