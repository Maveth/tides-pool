# RIPTIDE public site — snapshot architecture

Public dashboards for **riptide.maveth.ca** / **tides.maveth.ca** are served from the
[`lab-website/`](../lab-website/) tree (Docker project `lab-website`, host port **`:8088`**).

## Why

Live tides-web `/api/contributors` and related endpoints are expensive under multi-tab
poll. The public site reads **pre-built JSON snapshots** (~every **5 minutes**) so the
UI stays snappy. A few fields that must stay fresh are **overlaid live** from Postgres
`meta` on each request.

## Components

| Piece | Container / path | Notes |
|-------|------------------|--------|
| Public web | `lab-website-1` → `:8088` | FastAPI `lab_web.app` + bind-mounted `static/` |
| Snapshot loop | `lab-website-snap-1` | `deploy/snapshot_loop.sh`, interval 300s |
| Live API source | `deploy-tides-web-1` → `:8087` (host), `:8080` (docker) | Snapper pulls `http://deploy-tides-web-1:8080` |
| Snapshots dir | `/mnt/Alexandria/bitcoin/lab-website/snapshots/` | Not in git |

## Snapshotted endpoints

Built by `python -m lab_web.build_snapshots`:

- Pool: `stats`, `info`, `coinbaser`, `blocks`, `contributors` (≤500), `charts/pool` 24h+7d, `health`
- Per miner: `users/<addr>/{user,payouts,shares,charts_*}.json` for window contributors + coinbaser payees

## Live overlays (public `:8088`)

In `lab_web/app.py` (requires `TIDES_DATABASE_URL`):

| Overlay | Source | TTL |
|---------|--------|-----|
| Gateway class ✓/⚠/? | `meta.cb_type_status_v1` → `by_address` | ~15s |
| Manual LISTED_ONLY table | `meta.manual_adjustment_<height>` | per request |

Everything else on `/api/contributors` / `/api/blocks` comes from the snap files.

## UI notes (static)

- Contributors: **10/page**, column **`% Blocks w Shares`** (`eras_with_work_pct`); no Last share column
- How to connect: **DATUM preferred · 0% fees**; **Pool SV1 strongly discouraged** (`riptide.maveth.ca:23337`, `bc1….worker`, password `x`, fee in red)
- Cache-bust query on `app.js` / `style.css` (bump when shipping static)

## Fee model (reminder)

| Path | Fee |
|------|-----|
| Own DATUM → Prime `:28916` | **0%** coinbaser (`TIDES_FEE_BPS=0`) |
| Pool SV1 `:23337` | Work skim `local_work_fee_bps` (hot `runtime_fees`, **currently 200 = 2%**); half ops / half miners as `STRATUM FEE` |

## Recover after tides-web recreate

`docker compose … --force-recreate` of `tides-web` drops docker-cp overlays. Restore from
`/mnt/Alexandria/local/tides-pool/src/tides_pool/`:

```bash
WEB=deploy-tides-web-1
APP=/app/src/tides_pool
ALX=/mnt/Alexandria/local/tides-pool/src/tides_pool
docker cp "$ALX/api.py" "$WEB:$APP/api.py"
docker cp "$ALX/models.py" "$WEB:$APP/models.py"
docker cp "$ALX/store.py" "$WEB:$APP/store.py"   # if eras / contributor_rows patched
docker restart "$WEB"
```

Then refresh public contrib snap if needed (`snapshot-builder` or pull `/api/contributors` into `snapshots/contributors.json`).

## See also

- [`lab-website/README.md`](../lab-website/README.md) — deploy commands + port map
- [`GROK_AGENT_NOTES.md`](GROK_AGENT_NOTES.md) — living ops diary
