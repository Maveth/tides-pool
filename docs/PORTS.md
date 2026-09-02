# Lab ports cheat sheet

| What | URL / endpoint | Notes |
|------|----------------|-------|
| **tides-pool website** | http://192.168.0.143:8088/ | TIDES stats, address lookup, shares |
| **DATUM2 Gateway UI** | http://192.168.0.143:7155/ | Secondary DATUM (pool path) status / local shares |
| DATUM2 stratum | `tcp://192.168.0.143:23336` | GPU only |
| DATUM Prime | `192.168.0.143:28916` | Encrypted pool_host (Gateway→pool) |
| Solo DATUM (MaVeTh) | stratum `:23335`, UI `:7154` | ASICs — leave alone |
| RC3 Knots RPC | `192.168.0.143:48332` | LAN only |

## DATUM2 mode

- Container: `bip110-datum-pool`
- `datum.pool_host` = `127.0.0.1:28916` → **talking to tides-pool Prime** (pooled coinbaser + share submit)
- `pooled_mining_only` = **false** → if Prime dies, Gateway can fall back to 100% `mining.pool_address` (safer for lab). Set **true** for Ocean-strict (disconnect miners if pool unreachable).
- Coinbase **primary tag** when pooled: comes from Prime `0x99` configure → **`TIDES`** (Ocean-style override). Local JSON also has `TIDES` / `MaVeTh`.

## Wallets (O:)

See `docs/WALLETS.md`. Quick map:

| Role | Address | Backup folder |
|------|---------|----------------|
| **Pool fee 2%** | `mqKdiu…` | `wallets\tides_pool_fee_tn4\` |
| GPU miner A | `n1Qve…` | `wallets\tides_gpu1_tn4\` |
| Miner B | `mfh5a…` | `wallets\tides_pool_tn4\` |
| Solo MaVeTh | `mqMY6…` | `wallets\maveth_tn4_*` |
