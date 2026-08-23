# tides-pool

**DATUM Prime + TIDES** mining pool server (lab / TN4 Blake2b).

This is the **pool** side. Miners do **not** stratum to this host for work templates.

```text
GPU/ASIC  →  YOUR DATUM Gateway (Blake2b)  →  tides-pool Prime (:28916)
                                              ↑ coinbaser + share accounting
Stats site  ←  HTTP (:8088)
```

For the **Blake2b-only DATUM Gateway** (solo by default), see  
**https://github.com/Maveth/datum_gateway/tree/bip110-pow-v2**

## What this repo is

| Included | Not included |
|----------|----------------|
| DATUM Prime (encrypted `pool_host` protocol) | Stratum work templates for ASICs |
| TIDES share log + coinbase suggestions | Lightning payouts |
| Optional finder-fee split + per-address work cap | Custodial balances |
| Simple stats / join UI | Your wallet private keys |

## Quick join (operators of a Gateway)

In **your** DATUM config (not this server’s stratum):

```json
"datum": {
  "pool_host": "tides.maveth.ca",
  "pool_port": 28916,
  "pool_pubkey": "",
  "pooled_mining_only": false
}
```

Empty `pool_pubkey` works on MaVeTh Blake Gateway builds (auto-fetch from  
`https://<pool_host>/api/pool_pubkey`). Paste the pubkey on other builds.

Stats: **https://tides.maveth.ca/**

## Run (TrueNAS / Docker)

```bash
cd deploy
# set TIDES_POOL_OPS_ADDRESS to your fee address
docker compose up -d --build
```

- UI/API: host **8088** → container 8080  
- Prime: host **28916**  
- Generate/persist keys under `deploy/datum-pool/pool_keys.json` (gitignored)

See `docs/` for wallets, ports, and fee math.

## Solo DATUM vs this pool

| | Gateway repo | This repo |
|--|--------------|-----------|
| Role | Build templates + Stratum to hardware | Coordinate shares + TIDES coinbaser |
| Solo | `pool_host: ""` | not used |
| Pooled | `pool_host` → Prime | runs Prime |

## License

MIT
