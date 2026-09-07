# tides-pool

**DATUM Prime + TIDES** mining pool for **Bitcoin mainnet Blake2b (BIP110 / RDTS)**.

Public site: **[https://riptide.maveth.ca/](https://riptide.maveth.ca/)** / **[https://tides.maveth.ca/](https://tides.maveth.ca/)** (RIPTIDE dashboard)  
Prime: **`tides.maveth.ca:28916`**

Miners do **not** stratum to Prime for work templates. Your **DATUM Gateway** builds jobs and talks to Prime for coinbaser + share accounting.

```text
ASIC/GPU  →  YOUR DATUM Gateway (Blake2b)  →  tides-pool Prime (:28916)
                                              ↑ coinbaser + TIDES shares
Stats UI  ←  HTTP (:8088)  [lab-website snapshot dash — see lab-website/]
Live API  ←  HTTP (:8087)  [deploy-tides-web — source for snaps / fresher JSON]
```

**Public dash architecture:** [`lab-website/`](lab-website/) + [`docs/SITE_SNAPSHOT.md`](docs/SITE_SNAPSHOT.md)
(5‑min snapshots + live gateway-class / manual-adjustment overlays).

Related Gateway builds:

| Prefer | Notes |
|--------|--------|
| [Leo StartOS `pow_0.4.1_18+`](https://github.com/Retropex/datum-gateway-startos/releases) | Known-good multi-out / tip path |
| Experimental CONVOY (`b9ea7dc`+) | Dual-speak configure v3 + ABW-off on this Prime — still experimental |
| [MaVeTh datum_gateway `bip110-pow-v2`](https://github.com/Maveth/datum_gateway/tree/bip110-pow-v2) | Solo-capable Blake Gateway |

## What this repo is

| Included | Not included |
|----------|----------------|
| DATUM Prime (encrypted pool protocol) | Stratum templates to ASICs |
| TIDES share log + live coinbaser | Lightning / custodial balances |
| Web UI (contributors, charts, health) | Your wallet private keys |
| Optional web/prime split (`TIDES_ROLE`) | Automatic off-chain finder payouts |

## Quick join (Gateway operators)

In **your** DATUM config:

```json
"datum": {
  "pool_host": "tides.maveth.ca",
  "pool_port": 28916,
  "pool_pubkey": "<128-hex from site Join /api/info>",
  "pooled_mining_only": false,
  "pool_pass_full_users": false,
  "pool_pass_workers": true
}
```

**Important**

- **Pool Pass Full Users = OFF** — miners should send **worker names only**; username must resolve to a **`bc1…` payout** (or `bc1….worker`). Bare nicknames → `bad payout address` rejects.
- Prefer Leo **`_18+`** for production Gateways. Watch CONVOY for type-0 / empty-tip coinbase behavior on Blake.

Stats + connect copy: **https://tides.maveth.ca/**

## Fees (live policy)

- **DATUM / own Gateway (preferred):** `TIDES_FEE_BPS=0` — coinbase = window work only (no ops cut).
- **Pool SV1** `riptide.maveth.ca:23337` (**strongly discouraged**): separate **work skim** via `meta.runtime_fees` / `local_work_fee_bps` (currently **2%**); half → live miners as `STRATUM FEE`, half → ops. Not a coinbaser cut.
- Username on SV1: `bc1…payout.worker`, password `x`.

Do not assume README fee math matches production without checking `/api/stats` → `fee_bps` and site How to connect.

## Run (TrueNAS / Docker)

**Split (recommended live):** website restarts do not bounce Gateways.

```bash
cd deploy
# set TIDES_POOL_OPS_ADDRESS, RPC, keys path, etc.
docker compose -f docker-compose.yml -f docker-compose.split.yml up -d --build
```

| Process | Role | Ports |
|---------|------|-------|
| `lab-website` | Public snapshot UI + overlays | host **8088** (see `lab-website/`) |
| `tides-web` | Live `/api/*` (snap source) | host **8087** |
| `tides-prime` | DATUM Prime + coinbaser | host **28916** (+ health **8089**) |
| `postgres` | share / block DB | internal |
| `bip110-datum-sv1` | Pool SV1 stratum (discouraged) | host **23337** |

Single-process (lab / legacy): `docker compose up -d --build` with `TIDES_ROLE=all`.

Persist Prime keys under `deploy/datum-pool/pool_keys.json` (gitignored).

See `docs/` for design notes, ports, and wallets. After UI-only edits on a live host, re-`docker cp` static/API into the web container (image recreate wipes overlays).

## Health / privacy

- `/health` and `/api/health` expose status for the header chip.
- Gateway peer IPs in `checks.gateway_uas` are **masked** to `*.*.*.last` (full IPs stay in Prime logs only).

## Payout verification

- Helper: `tides_pool/payment_verify.py`
- Unit tests: `tests/test_payment_verify.py`
- Live gate (NAS): `scripts/_live_verify_payments_vs_coinbase.py` — web coinbaser vs Prime + last find listed vs chain

## Solo DATUM vs this pool

| | Gateway | This repo |
|--|---------|-----------|
| Role | Templates + Stratum to hardware | Shares + TIDES coinbaser |
| Solo | `pool_host: ""` | not used |
| Pooled | `pool_host` → Prime | runs Prime |

## License

MIT
