# tides-pool design (v1)

In-repo summary. **Live join / fee policy / ports:** see root `README.md` (mainnet Blake2b / RIPTIDE). Early sections below still describe the original TN4 lab math.

## Responsibility split

| Actor | Owns |
|-------|------|
| Miner DATUM Gateway + local node | Templates, tx selection, local share pre-check |
| tides-pool | Share log, TIDES, coinbase **suggestions**, share accept/reject |
| knots / ops wallet | Watch pool blocks; hold ops fee keys only |

## TIDES

- Append-only share log; each share contributes `work = share_difficulty` (Diff1 units).
- **Live window mode:** last **N pool finds** (default `window_blocks=8` → **7 confirmed + current**), cutoff = share head of the Nth-last confirmed find (orphans excluded).
- Legacy ocean-style `min(8 * D, total_work)` walk is lab-only / not used for live payouts.
- Split block reward by work share; apply fee settings; floor to sats.

## Fee / finder

Configured by `TIDES_FEE_BPS` and `TIDES_FINDER_FEE_SHARE_BPS` (not hard-coded).

```text
Example when fee_bps=500 and finder_fee_share_bps=8000:
  R = block reward
  TIDES miners : ~0.95 R   (window work)
  Finder credit: ~0.04 R   (prior finder; next coinbase)
  Pool ops     : ~0.01 R

When fee_bps=0 (common live):
  coinbase = 100% window work; no ops line; no in-coinbase finder bonus
  (any finder thank-you is manual / off-chain)
```

## Live wiring (NAS / mainnet)

| Piece | Detail |
|-------|--------|
| Public UI | https://tides.maveth.ca/ (`:8088` on NAS) |
| Prime | `tides.maveth.ca:28916` (`deploy-tides-prime-1`) |
| Split | `TIDES_ROLE=web|prime` — UI restart must not bounce Gateways |
| Knots | NAS mainnet node (RPC LAN-only); Windows RC4 also runs for ops wallets |

## Website (v1 — in scope)


Ocean-like **stats site** (not a full portal):

- Pool overview: difficulty, TIDES window fill, share log, fee/finder summary
- Contributors in current window (address, work, share %)
- Recent pool blocks
- **Address lookup**: window %, estimated next payout, pending finder credit, **recent shares** for that address

Served at `/` from FastAPI (`src/tides_pool/static/`).

**Process roles (Phase 1):** `TIDES_ROLE=all|web|prime` (default `all`).
- `web` — HTTP + static; no DATUM Prime; health probes `TIDES_PRIME_HOST:28916`; coinbaser UI prefers Prime-written `coinbaser_last_json` meta.
- `prime` — Prime TCP + chain sync; minimal HTTP (`/api/health`); no static UI.
- Compose split: `deploy/docker-compose.split.yml` (`tides-web` :8088, `tides-prime` :28916).

**Live (NAS):** `http://192.168.0.143:8088/` (host port 8088; 8080 is SABnzbd). Still `role=all` until split cutover.


## Non-goals (v1)

Lightning, bare stratum templates, GPU-direct-to-pool, custodial balances, full Ocean portal polish / auth.

## Payout username validation

Prime validates the share username (before `.worker`) with `is_valid_payout_address()`:

- **Accept** → credit TIDES work to that address.
- **Reject** (`0x66` / reason `14` BAD_USERNAME) → no credit (covers junk like `box2`).
- Coinbaser also folds any unencodable address into the ops output (defense-in-depth).

Chain sync / UI tip labels: **RC3** (Catbus) on testnet4 Blake2b.
