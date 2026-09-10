# tides-pool design (v1)

See also the approved session plan. This file is the in-repo summary.

## Responsibility split

| Actor | Owns |
|-------|------|
| Miner DATUM Gateway + local node | Templates, tx selection, local share pre-check |
| tides-pool | Share log, TIDES, coinbase **suggestions**, share accept/reject |
| knots-pool (dedicated) | Watch our blocks; hold ops fee keys only |

## TIDES

- Append-only share log; each share contributes `work = share_difficulty`.
- On pool block with difficulty `D`: window = `min(8 * D, total_work)`.
- Walk backward from **job-issue share-log head** (not wall-clock find time).
- Split `R = subsidy + fees` by work share; apply fee flags; floor to sats.

## Fee / finder

```text
R = block reward
TIDES miners : 0.90 R   (in coinbase of N, suggested before find)
Finder credit: 0.08 R   (owed to finder of N; paid in N+1… suggestions)
Pool ops     : 0.02 R   (in coinbase of N)
```

## Live lab wiring (NAS)

| Piece | Detail |
|-------|--------|
| RC3 Knots | `bip110-knots-testnet4` RPC `192.168.0.143:48332` — tides-pool syncs tip/diff every ~15s |
| Pool UI | `http://192.168.0.143:8088/` |
| Solo DATUM (unchanged) | `:23335` / API `:7154` → `mqMY6…` (maveth_tn4) |
| **DATUM2 (pool gateway)** | `:23336` / API `:7155` → **`mfh5aSGhAWyJ2cv8vU2S1jZ1bujwEizRV3`** |
| Wallet backup | `O:\bip110minner\wallets\tides_pool_tn4\` (separate from maveth) |

**DATUM Prime (encrypted `pool_host` protocol)** is still required before DATUM2 can send shares / get TIDES coinbase splits from tides-pool. Until then DATUM2 runs **non-pooled** (100% to ops address) but is ready for `pool_host=127.0.0.1:28916` once Prime is implemented.

## Website (v1 — in scope)


Ocean-like **stats site** (not a full portal):

- Pool overview: difficulty, TIDES window fill, share log, fee/finder summary
- Contributors in current window (address, work, share %)
- Recent pool blocks
- **Address lookup**: window %, estimated next payout, pending finder credit, **recent shares** for that address

Served at `/` from the same FastAPI process (`src/tides_pool/static/`).

**Live (NAS):** `http://192.168.0.143:8088/` (host port 8088; 8080 is SABnzbd).


## Non-goals (v1)

Lightning, bare stratum templates, GPU-direct-to-pool, custodial balances, full Ocean portal polish / auth.

## Payout username validation

Prime validates the share username (before `.worker`) with `is_valid_payout_address()`:

- **Accept** → credit TIDES work to that address.
- **Reject** (`0x66` / reason `14` BAD_USERNAME) → no credit (covers junk like `box2`).
- Coinbaser also folds any unencodable address into the ops output (defense-in-depth).

Chain sync / UI tip labels: **RC3** (Catbus) on testnet4 Blake2b.
