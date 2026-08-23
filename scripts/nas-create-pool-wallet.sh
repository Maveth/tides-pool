#!/bin/bash
set -euo pipefail
CLI=(sudo docker exec bip110-knots-testnet4 bitcoin-cli -testnet4 -rpcuser=datum -rpcpassword=YOUR_LAB_PASSWORD -rpcport=48332)

# Create dedicated descriptor wallet (do not touch maveth_tn4)
"${CLI[@]}" createwallet "tides_pool_tn4" false false "" false true true 2>/dev/null || \
  echo "wallet may already exist"

"${CLI[@]}" -rpcwallet=tides_pool_tn4 getwalletinfo

ADDR=$("${CLI[@]}" -rpcwallet=tides_pool_tn4 getnewaddress "tides-pool-ops" legacy)
echo "ADDR=${ADDR}"

"${CLI[@]}" -rpcwallet=tides_pool_tn4 getaddressinfo "${ADDR}"

# Export descriptors (includes private keys when true)
"${CLI[@]}" -rpcwallet=tides_pool_tn4 listdescriptors true > /tmp/tides_pool_tn4_descriptors.json

# Wallet file backup into node datadir then copy out
"${CLI[@]}" -rpcwallet=tides_pool_tn4 backupwallet "/tmp/tides_pool_tn4_backup.dat"

# Meta
cat > /tmp/tides_pool_tn4_meta.json <<EOF
{
  "wallet_name": "tides_pool_tn4",
  "network": "testnet4",
  "ops_address": "${ADDR}",
  "address_type": "legacy",
  "created_note": "Pool ops / DATUM2 payout address. Separate from maveth_tn4.",
  "node": "bip110-knots-testnet4"
}
EOF
echo "${ADDR}" > /tmp/tides_pool_tn4_address.txt

# Copy backup from container to host if needed
sudo docker cp bip110-knots-testnet4:/tmp/tides_pool_tn4_backup.dat /tmp/tides_pool_tn4_backup.dat 2>/dev/null || true
sudo docker cp bip110-knots-testnet4:/tmp/tides_pool_tn4_descriptors.json /tmp/tides_pool_tn4_descriptors.json 2>/dev/null || true

ls -la /tmp/tides_pool_tn4_*
echo "READY_ADDR=${ADDR}"
