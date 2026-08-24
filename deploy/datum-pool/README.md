# DATUM2 — pool Gateway (secondary)

Mirrors solo DATUM (`bip110-datum-pow`) but is the **pooled** path into tides-pool.

| | Solo (keep) | DATUM2 (this) |
|--|-------------|----------------|
| Stratum | 23335 | **23336** |
| API UI | 7154 | **7155** |
| `mining.pool_address` | solo payout | **TIDES miner identity** (one address) |
| Tags | Totoro / MaVeTh | TIDES / Maveth_tides |
| `pool_host` | empty (solo) | tides-pool DATUM Prime (`192.168.0.143:28916`) |
| `vardiff_min` | high (ASIC) | **4** (GPU-friendly; power-of-two) |

## Username mode (important)

For one Gateway → one TIDES payout address with per-GPU workers:

```json
"datum": {
  "pool_pass_full_users": false,
  "pool_pass_workers": true
}
```

- GPUs authorize as **worker-only** usernames: `.GPU1`, `.GPU2`, `.GPU3`
- DATUM prepends `mining.pool_address` → TIDES sees `ADDRESS.GPU1`, etc.
- Do **not** enable `pool_pass_full_users` unless you intentionally want each miner address credited separately on TIDES.

`mining.pool_address` should be the miner identity on TIDES (lab: `n1Qve…`). Keep the pool ops/fee address (`mqKdiu…`) as tides-pool `TIDES_POOL_OPS_ADDRESS`, not as the Gateway miner identity.

## Start on NAS (host network)

```bash
# copy example → config.json and edit secrets/addresses first
cp config.example.json config.json
bash /mnt/Alexandria/local/tides-pool/deploy/datum-pool/start-datum-pool.sh
```

- Stratum: `tcp://192.168.0.143:23336`
- UI: http://192.168.0.143:7155/
- Pool: http://192.168.0.143:8088/

Windows GPU lab launcher: `O:\bip110minner\scripts\tides_mine_3gpu_supervisor.py`  
Identities: `O:\bip110minner\scripts\tides_gpu_identities.json`

## Notes

- `config.json` / `pool_keys.json` are gitignored — never commit secrets.
- Empty `pool_pubkey` is OK on MaVeTh Blake builds (auto-fetch from tides-pool).
- If TIDES logs `append_share() got an unexpected keyword argument 'difficulty'`, the running container has a stale `datum_prime.py` — redeploy from this repo.
