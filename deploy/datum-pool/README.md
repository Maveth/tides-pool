# DATUM2 — pool Gateway (secondary)

Mirrors solo DATUM (`bip110-datum-pow` on **:23335**) but:

| | Solo (keep) | DATUM2 (this) |
|--|-------------|----------------|
| Stratum | 23335 | **23336** |
| API UI | 7154 | **7155** |
| Payout address | `mqMY6…` (maveth_tn4) | **`mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3`** (tides_pool_tn4) |
| Tags | Totoro / MaVeTh | TIDES / MaVeTh |
| `pool_host` | empty (solo) | empty until DATUM Prime on tides-pool is live |
| `vardiff_min` | 16384 (ASIC) | **4** (GPU-friendly; power-of-two) |

GPU lab config on Windows: `O:\bip110minner\config.lab-tides-datum.yaml`  
User/worker: `mfh5a….Maveth_GPU1`

Wallet backups (do **not** overwrite maveth):  
`O:\bip110minner\wallets\tides_pool_tn4\`

## Start on NAS (host network, like solo)

```bash
bash /mnt/Alexandria/local/tides-pool/deploy/datum-pool/start-datum-pool.sh
```

Point a miner / test Gateway ASICs at: `tcp://192.168.0.143:23336`

## When tides-pool speaks DATUM Prime

Set in `config.json`:

```json
"datum": {
  "pool_host": "127.0.0.1",
  "pool_port": 28916,
  "pool_pubkey": "<128-hex from tides-pool>",
  "pooled_mining_only": true
}
```

Until then, DATUM2 still builds local templates paying **100% to mfh5a…** (solo-style), while tides-pool UI/API already syncs RC2 tip/difficulty.
