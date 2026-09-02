# TN4 wallets (keep separate)

Network: **testnet4** · Blake2b · **Catbus RC3**.

| Role | Address | Wallet name | Backup on `O:` |
|------|---------|-------------|----------------|
| **Pool fee keep (2%)** | `mqKdiu6W825MWc31NACiwxRchTb4dP2NRH` | `tides_pool_fee_tn4` | `wallets\tides_pool_fee_tn4\` |
| GPU miner A | `n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5` | `tides_gpu1_tn4` | `wallets\tides_gpu1_tn4\` |
| Miner B / SimDatum2 | `mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3` | `tides_pool_tn4` | `wallets\tides_pool_tn4\` |
| Solo MaVeTh (DATUM :23335) | `mqMY6…` | `maveth_tn4` | `wallets\maveth_tn4_*` |

## Fee split (per pool-found block reward R)

| Slice | Amount | When |
|-------|--------|------|
| TIDES miners | **90%** of R | Always (window share %) |
| Previous finder bonus | **8%** of that block’s R | When Prime sees a block-find share (`is_block`); added into **later** coinbasers for all Gateways |
| Pool fee keep | **2%** of R | To `mqKdiu…` while a finder credit is outstanding (ops bucket minus bonus) |
| Pool fee (bootstrap) | **10%** of R | No prior finder credit yet → all fee to `mqKdiu…` |

Finder bonus cannot go in the *same* block’s coinbase (DATUM freezes outputs before find). Credit is a fixed sats debt (8% of the found block) included in subsequent templates until the next pool block is found.

## Tags vs workers

| Layer | What | Example |
|-------|------|---------|
| Pool primary (Prime `0x99`) | Shared pool brand in coinbase | `TIDES` |
| DATUM2 secondary (Gateway config) | This Gateway’s brand in coinbase | `Maveth_tides` |
| Stratum worker (per miner) | Unique on tides site / share log | `Maveth_tides_GPU1`, `Maveth_tides_B` |

External miners: set **their own** DATUM Gateway `coinbase_tag_secondary`, and stratum user `ADDRESS.Maveth_tides_<name>`. One Gateway = one secondary tag in the template; worker uniqueness is in the username.

## Payout address = stratum username (required)

TIDES / DATUM Prime treat the stratum **username** (before any `.worker` suffix) as the **payout script**.

| Allowed | Rejected |
|---------|----------|
| Valid TN4 addresses: `tb1q…`, `tb1p…`, legacy `m…` / `n…` / `2…` | Nicknames, worker-only names, device labels (`box2`, `rig1`, …) |
| `ADDRESS.worker` (worker is metadata only) | Empty / checksum-invalid / wrong-network addresses |

Invalid usernames are **rejected** at share submit (`BAD_USERNAME` / status `0x66`) — **no TIDES credit**, and they never appear as coinbase outputs (any leftover folds to ops).

This matches Ocean/DATUM conventions: the Gateway `mining.pool_address` and miner usernames must be real Bitcoin addresses.

## Per-address work cap (GPU-friendly)

Baseline GPU ≈ **2.5 GH/s** → ~**2095** difficulty-1 work / hour.  
**20× cap** ≈ **~41,910 work / rolling hour** per payout address.

ASIC / multi-rig far above that: shares may still ACK, but **TIDES credit stops** once the cap is hit (`share OK (CAPPED)` in Prime logs).  
Tune via `TIDES_GPU_BASELINE_HASHRATE_HS`, `TIDES_ADDRESS_WORK_CAP_MULTIPLIER`, `TIDES_ADDRESS_WORK_CAP_WINDOW_SEC`.

