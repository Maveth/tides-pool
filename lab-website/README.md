# RIPTIDE public site (snapshot dash)

This tree is the **production front door** for [riptide.maveth.ca](https://riptide.maveth.ca) / [tides.maveth.ca](https://tides.maveth.ca) (NPM → host **`:8088`**).

It serves the same UI as the old live tides-web static (`index.html` / `app.js` / `style.css`), but **most JSON is snapshotted** from the live API process so the public dash stays fast.

## Port map (NAS)

| Port | Process | Role |
|------|---------|------|
| **`:8088`** | `lab-website-1` (this project) | **Public site** — 5‑min snapshots + live overlays |
| **`:8087`** | `deploy-tides-web-1` | **Live real-time APIs** (source for snaps; use for fresher `/api/*`) |
| **`:28916`** | `deploy-tides-prime-1` | DATUM Prime (Gateways only — never point ASICs here) |
| **`:23337`** | `bip110-datum-sv1` | Pool SV1 stratum (**strongly discouraged**) |

Compose project name: **`lab-website`** (do **not** use `-p deploy` or you can orphan live tides).

NAS paths:

- Code / static bind: `/mnt/Alexandria/bitcoin/lab-website/`
- Snapshots volume: `/mnt/Alexandria/bitcoin/lab-website/snapshots/`
- Live tides overlay (api/models/store): `/mnt/Alexandria/local/tides-pool/src/tides_pool/`

## What is snapshotted vs live

**Snapshotted (~every 5 minutes)** by `lab-website-snap-1` (`snapshot-refresher` → `lab_web.build_snapshots`):

- `/api/stats`, `/api/info`, `/api/coinbaser`, `/api/blocks` (base), `/api/contributors` (work rows), pool charts, `/api/health` (base)
- `/api/user/{addr}` (+ payouts / shares / charts) for window contributors

**Live overlays** (Postgres `meta`, short TTL ~15s) in `lab_web/app.py`:

- Contributor **gateway class** `cb_type_status` / `cb_type_tip` (✓ / ⚠ / ?)
- Block **`manual_adjustment`** (LISTED_ONLY payout table under “manual adjustment”)

**Always current (static HTML):** How to connect (DATUM + SV1), fee promo, Pool Pass screenshot.

UI soft-refreshes ~60s and **re-reads the latest snapshot** (plus overlays).

## Connect (documented on site)

**DATUM (preferred · 0% coinbaser fees)**

- Run your own Knots + DATUM Gateway → `pool_host` / `pool_port=28916`
- Miners → *your* Gateway stratum (never `:28916`)
- Pool Pass Full Users **OFF**; worker name only

**Pool SV1 (strongly discouraged)**

```text
stratum+tcp://riptide.maveth.ca:23337
username = bc1…yourpayout.worker
password = x
```

Variable **work fee (currently 2%)** on that path only: half → live miners as `STRATUM FEE`, half → ops. Prefer DATUM.

## Ops commands

```bash
cd /mnt/Alexandria/bitcoin/lab-website/deploy

# Public site + 5-min snap loop
docker compose -p lab-website up -d --build lab-web snapshot-refresher

# One-shot full snapshot (pool + per-user)
docker compose -p lab-website run --rm snapshot-builder
```

From the Windows workspace (via `scripts/nas_run.ps1`):

```powershell
# Sync static only (bind-mounted)
.\scripts\nas_run.ps1 -File scripts\_deploy_static_only.sh

# After tides-web force-recreate: restore overlays (api.py + models.py)
# or eras in store.py — see docs/GROK_AGENT_NOTES.md
```

### After `deploy-tides-web` force-recreate

Image wipe loses docker-cp overlays. Re-copy from Alexandria **at least**:

- `api.py` (blocks `manual_adjustment`, SSR `?v=`, contrib attach)
- `models.py` (`manual_adjustment` on `BlockOut`, eras fields on `Contributor`)
- `store.py` if `contributor_rows` eras were patched (`eras_with_work*`)

Then restart `deploy-tides-web-1` and optionally refresh `contributors.json`.

## Layout

```
lab-website/
  deploy/           # compose, Dockerfile, snapshot_loop.sh
  src/lab_web/      # FastAPI: snapshots + live overlays
  static/           # prod UI (index/app/style + pool_pass_full.png)
  snapshots/        # runtime only — gitignored
  README.md
```

## Related docs

- Living ops notes: [`docs/GROK_AGENT_NOTES.md`](../docs/GROK_AGENT_NOTES.md)
- Agent hard rules: [`AGENTS.md`](../AGENTS.md) / [`Agents.md`](../Agents.md)
