# Grok agent notes — bip110minner / TIDES ops

Living document. Append dated bullets when you learn something that future sessions should not re-derive.

---

## Lab topology (multi-PC)

**There is more than one machine. Do not treat NAS GPUs as the 3090s.**

### NAS (`bip110-nas` → hostname `truenas`)

- Runs: TIDES pool (`deploy-tides-pool-1`), Postgres, Knots **main** node, DATUM gateway(s), mempool stack.
- GPU on this host: typically **GTX 1650** (e.g. TrueNAS Ollama app). **No RTX 3090s on the NAS.**
- `nvidia-smi` on NAS ≠ Windows mining farm.
- Knots compose: `/mnt/Alexandria/local/bip110-lab/docker-compose.pow-main.yml` (container `bip110-knots-main`).
- Public P2P: WAN **8333 → 192.168.0.143:8333**, DNS **`bip110.maveth.ca` → 174.3.117.235**, node flag **`-externalip=bip110.maveth.ca`** (added 2026-09-01). Keep RPC **8332** LAN-only.

### Windows workstation (this repo’s workspace PC)

- Also runs a **Knots node** (RPC cookie under `O:/Users/memys/AppData/Roaming/Bitcoin/.cookie` — see `docs/RPC_ACCESS.md`).
- **3 GPUs that can mine:** 2× **RTX 3090** + **RTX 5060 Ti**.
- Mining (`bip110miner` / tides GPU scripts) is often **turned off**; same GPUs used for **LLM** (`O:\llama\...\llama-server.exe`, high VRAM, util may still be low between requests).
- Before assuming “our GPUs are hashing for TIDES,” check Windows for `python -m bip110miner` / `tides_mine` / CUDA pow — not only NAS.

### Pool hashrate

- Live TIDES HR is dominated by **remote DATUM Gateways / ASICs** (workers like BoxII, GS*, sclite, Maveth.ASIC), not by Windows GPUs when mining is stopped.

---

## 2026-09-01 — TIDES UI / window / orphans

### Payout window (pool_finds)

- Setting: `TIDES_WINDOW_BLOCKS=8` (and `settings.window_blocks`).
- **Cutoff** = `share_head_seq` of the **8th-most-recent confirmed** pool find (`store.payout_window_cutoff_seq`).
- **In the window** = shares with `seq > cutoff` → effectively **7 confirmed finds + current** unfinished work.
- Orphans / misattributed finds **do not** advance or sit in the confirmed window (`list_confirmed_blocks` filters `status=confirmed`).
- **Prime** (`datum_prime._coinbaser`) uses `window_mode="pool_finds"` + cutoff. UI **must** use the same path (`_payout_window()` in `api.py`).
- Do **not** use ocean-style `window_slice(shares, difficulty×8)` for Contributors / coinbaser / user stats — that was a regression when a stripped `api.py` was docker-cp’d over prod.

### Site cards / wording

- Top cards include: Chain tip, Pool hashrate, Miners in window, **Reward window = `7 + current`**, Blocks/last 24h, Last pool block.
- **Blocks / last 24h** = confirmed+pending only; orphans reported separately as `orphans_last_24h` (card shows `N · M orphaned` when M>0).
- Count uses `blocks.accounted_at`, not explorer wall time.
- Contributors blurb: “7 confirmed finds + current (cutoff = 8th-last confirmed find)”.
- Cache-bust: `api.py` SSR rewrites `app.js` / `style.css` `?v=…` — bump both HTML and the rewrite strings together.

### Orphans

- UI: red `badge-orphan` + `tr.row-orphan`; recent list includes orphaned rows from `list_blocks` (all statuses).
- **Never delete orphan rows.** `mark_block_orphaned` sets `status=orphaned` + reason.
- **`clear_lab` / `DELETE FROM blocks` on prod is catastrophic** — wipes history including orphans.

#### Incident: height 962598

- Real TIDES orphan on bitcoind: `valid-fork` hash `0000000000000063b87be7ed0441e8fd498cabc912f1daee7a33094504157a9b`, coinbase `TIDES Maveth_tides`.
- Lost to active block at same height tagged `mycology anastomosing`.
- Was **missing** from `blocks` table (gap 962580 → 962658). User reports it had been present earlier and was removed (likely lab clear / bad deploy / mistaken wipe — treat as data loss).
- Backfilled 2026-09-01 as `orphaned` / reason `valid-fork lost to mycology anastomosing`.
- Finder is **`bc1q3l0q76kahen6m3n4d7adksq4mxmvutyndqd5f9`** (coinbase secondary `Maveth_tides`) — not BoxII/`bc1qk7zr…` (that was a bad backfill heuristic; corrected same day).
- Lesson: scan `getchaintips` for TIDES-tagged `valid-fork` tips and reconcile into `blocks` if MISSING; don’t assume “not in DB” means “not ours”. Set finder from coinbase identity, not guesswork.

### api.py deploy footgun

- Live NAS had a full `api.py` (pool_finds `_payout_window`, quarantine, block status fields, SSR coinbaser, `/blocks` page needs adding carefully).
- A declutter deploy once replaced it with a thinner local mirror → Contributors/coinbaser drifted to ocean work window; `window_confirmed_finds` stuck at 0.
- Recovery: restore from `api.py.bak.declutter-*` / `api.py.bak.windowalign-*` on NAS under `/mnt/Alexandria/local/tides-pool/src/tides_pool/`, then docker cp + restart.
- Local `_tmp_tides_pool_gh` is **not** always authoritative vs Alexandria + container.

### Coinbaser: site vs Gateway

- Same math when reward input matches.
- Website `/api/coinbaser` uses `reward_estimate` (subsidy, e.g. 3.125 BTC).
- Live Gateway coinbaser uses DATUM template **value** (subsidy + fees) → sats scale slightly; addresses/kinds/order should match.
- Finder bonus ≈ 4% of block (80% of 5% fee); `tides+finder` line = work share **+** bonus.

### Recommended Gateway

- **Official (Leo):** StartOS [`pow_0.4.1_18`](https://github.com/Retropex/datum-gateway-startos/releases/tag/pow_0.4.1_18) (pool-compat) or registry [`start9.mempool.guide`](https://start9.mempool.guide/) · Umbrel [`Retropex/Bitcoin-store`](https://github.com/Retropex/Bitcoin-store). **`_17` is type-0 → reject 27.**
- **Experimental (working):** MaVeTh StartOS [`pow_0.4.1_20`](https://github.com/Maveth/datum-gateway-startos/releases/tag/pow_0.4.1_20) (Fly tip multi-out).
- **Experimental (Leo CONVOY package — please test/report in channel):** Retropex StartOS [`pow_0.4.1_20`](https://github.com/Retropex/datum-gateway-startos/releases/tag/pow_0.4.1_20) (“Switch to DATUM Gateway from CONVOY”, pre-release). Prefer `_18` for production until proven.
- Paste `pool_pubkey` (128 hex) if the build does not auto-fetch.
- Type-0 / `coinbase_id=0` → reject 27 under enforcement.

### DATUM landscape (reviewed 2026-09-03)

- **InnerHat master:** Jason Sopko **#17** merged `2026-09-02` → tip `4cd17a06` (“Mine the pool's payout coinbase on BLAKE2b jobs” / type-4 index). MaVeTh #16/#15 closed unmerged (Jason’s shape won). Still open on InnerHat: #14 (submitblock duplicate), #12 (misconfig warns), #11, #13, #10.
- **Leo / Retropex Start9 DATUM:** still latest Blake package **`pow_0.4.1_17`** (2026-08-30) — **no** rebuild after #17. (Leo *did* ship Knots final `v29.4.1.knots20260508` on 2026-09-02.)
- **Fly:** `master` tip still **`5cd2fdd`** (console-collapse #3, 2026-08-31). Multi-out lives only on branch **`test/console-collapse-pr14-pr17` @ `121edd0`** — not merged to Fly master, no post-#17 sync. MaVeTh StartOS `_20` packages that tip.

### NAS / deploy cheatsheet

| Item | Value |
|------|--------|
| SSH host | `bip110-nas` |
| Runner | `scripts/nas_run.ps1` (`-Command` or `-File`) |
| Source tree | `/mnt/Alexandria/local/tides-pool/src/tides_pool/` |
| GitHub | [`Maveth/tides-pool`](https://github.com/Maveth/tides-pool) — synced from live NAS **2026-09-01** (`0bf1e34`). NAS Alexandria `.git` is **not** the publish path (often uncommitted); publish via `_tmp_tides_pool_gh` + live tarball overlay. |
| UI cache | Bump `?v=` on `app.js` / `style.css` in `static/index.html` **and** the regex substitutes in `api.py` `index()` together. Current live: `20260903g` (Pool Pass Full Users warning + screenshot). |
| Container | `deploy-tides-pool-1` |
| App path in container | `/app/src/tides_pool/` |
| HTTP | `http://127.0.0.1:8088` on NAS (public tides.maveth.ca) |
| Prime | port `28916` |
| Postgres | `postgresql://tides:tides@postgres:5432/tides` (in-compose) |
| Knots RPC (from pool) | `TIDES_BITCOIN_RPC_URL` e.g. `http://192.168.0.143:8332` user `datum` |

Prefer: write script locally → `scp` to `/tmp/` → `nas_run` bash/python. PowerShell mangling of remote quotes/heredocs is a recurring failure mode.

### RPC on Windows workstation

- Cookie: `O:/Users/memys/AppData/Roaming/Bitcoin/.cookie` (see `docs/RPC_ACCESS.md`).
- Node may also use rpcuser/rpcpassword `datum` / … on LAN.

---

### 2026-09-01 — Coinbaser Worker column

- “If we find a block now” shows **Worker** from DATUM stratum username (`address.worker` on shares), not coinbase secondary tags.
- API: `CoinbaseOutput.name` filled from latest share worker for that payout address (`ops` for ops line).
- Do not scrape secondary tags from mined blocks for this UI column.

### 2026-09-01 — Sky mempool.dat import (not “stale”)

SkyHawk dump (~32k txs, fresh; network mining near-empty blocks). `importmempool` → **107 ok / 32366 failed**.

**Root cause = minrelay floor mismatch, not age:**

| | Sky | Our Knots (`bip110-knots-main`) |
|--|-----|----------------------------------|
| `minrelaytxfee` | `0.000001` BTC/kvB (**0.1 sat/vB**) | default `0.00001` (**1 sat/vB**) — not set in container args |

Full dump classification (`scripts/_dig_mempool_full.py`, all 32473 txs):

| Reason | Count | % |
|--------|------:|--:|
| `min relay fee not met` | 22264 | 68.56% |
| `missing-inputs` | 9839 | 30.30% |
| `txn-already-known` (confirmed) | 331 | 1.02% |
| already in our mempool | 32 | 0.10% |
| would accept at current policy | 7 | 0.02% |

Fee rates on all 22264 min-relay rejects (from `reject-details` `paid < need`): **min 0.10 / p50 0.44 / max ~1.00 sat/vB** — **zero** below Sky’s 0.1, **zero** at/above our 1.0. Missing-inputs: **~72%** of checked parent vins are **in the same dump** (cascade). Missing-inputs cluster in the **last ~10k** entries; fee rejects dominate the first ~22k.

Empty blocks fit: sub-1 sat/vB volume lives on Sky’s lowered floor and does not propagate to default-1-sat/vB miners.

**mempool.dat v2 parse:** after CompactSize xor_key, XOR each payload byte with `key[file_offset % keylen]` (absolute file position). Count at plaintext offset after key ≈ 32473.

**Applied 2026-09-01:** compose `docker-compose.pow-main.yml` now has `-minrelaytxfee=0.000001` + `-incrementalrelayfee=0.000001`. After recreate, floor=0.1 sat/vB. Re-`importmempool` of Sky dump → mempool **51 → 32154** txs (~22.8 MB). Scripts: `_nas_lower_minrelay_reimport.sh`, `_dig_mempool_full.py`. Backup: `docker-compose.pow-main.yml.bak.minrelay-*`.

**`blockmintxfee` (2026-09-02):** Separate from relay. `minrelaytxfee` only admits txs to the mempool; **`blockmintxfee`** is the floor for `CreateNewBlock` / GBT inclusion (default still **1 sat/vB**). Without lowering it, sub-1 sat/vB txs sit in mempool but never get mined. Applied `-blockmintxfee=0.000001` alongside the relay knobs on `bip110-knots-main`. Script: `_fix_blockmintxfee.sh`.

### 2026-09-01 — Mempool Blake/RDTS P0 (800k WU + Kilombino pools)

Stock `mempool/frontend|backend:latest` on NAS (`docker-compose.mempool-tn4.yml`, filename legacy) was still at **4M WU** + stock `mempool/mining-pools` → projected blocks looked ~5× too empty and Blake miners showed Unknown / SHA brands.

**Applied on live NAS:**

| Knob | Value |
|------|-------|
| `BLOCK_WEIGHT_UNITS` / `MEMPOOL_BLOCK_WEIGHT_UNITS` | **800000** |
| `KEEP_BLOCKS_AMOUNT` | **24** |
| `MEMPOOL_POOLS_JSON_URL` | Kilombino `mempool-bip110` `pools-v2.json` |
| `MEMPOOL_POOLS_JSON_TREE_URL` | Kilombino git trees `main` |
| `MEMPOOL_POOLS_UPDATE_DELAY` | **3600** |
| `MEMPOOL_AUTOMATIC_POOLS_UPDATE` | **true** |

Verify: LAN `http://192.168.0.143:4080/resources/config.js` → `BLOCK_WEIGHT_UNITS = 800000`; `/api/v1/fees/mempool-blocks` first `blockVSize` ≈ **200000** when full (~99% observed). API log: `PoolsUpdater` import from Kilombino completed; DB has TIDES / MaVeTh / Kilombino / DATUM / Roughnecks / …

Compose path: `/mnt/Alexandria/local/bip110-lab/docker-compose.mempool-tn4.yml` (backup `*.bak-blake-p0-*`). Local mirror: `deploy/truenas-scale/docker-compose.mempool-tn4.yml`. Scripts: `_patch_mempool_blake_p0.py`, `_apply_mempool_blake_p0.sh`.

**Public recipe:** https://github.com/Maveth/mempool-blake (thin stock-image compose + README). Does **not** include Kilombino’s BIP110 goggles UI — weight + miner tags only.

Upstream notes: `jasonsopko/mempool` `KNOTS-BLAKE2B.md`. Stock fee-api still may pin tiers until upstream fee-api weight PR lands; weight fix alone already corrects projected-block geometry.

**NPM / public (checked 2026-09-02):** Was proxy host `mempool.maveth.ca` → `http://192.168.0.143:4080` (`proxy_host/20.conf`). From **LAN/NAS to WAN IP** (`174.3.117.235:443`) **all** `*.maveth.ca` time out — **no NAT hairpin/loopback**. Use LAN URLs from home (`:8088` tides); public HTTPS is for off-LAN clients.

**NPM mempool redirect (2026-09-06):** Stopped hosting local mempool (pull after reboot wiped images and stressed NAS). **DNS unchanged** (still A → public IP). NPM: soft-deleted proxy host **#20**; added **redirection_host #1** `mempool.maveth.ca` → **`https://mempool.guide$request_uri`** (301, preserve path, cert `npm-11` `*.maveth.ca`). Conf: `/data/nginx/redirection_host/1.conf`. Scripts: `scripts/_npm_mempool_redirect.py`, `scripts/_nas_npm_mempool_redirect.sh`. Verify on NAS: `curl --resolve mempool.maveth.ca:443:127.0.0.1 -k -D- https://mempool.maveth.ca/` → `301 Location: https://mempool.guide/`. Do **not** re-enable proxy #20 / bring up `:4080` unless intentionally self-hosting again.

**Pool pie “Other” (widget):** Stock = desktop **0.5%** / widget **1%** / mobile **2%**. Live patch on `930.*.js` (per-locale in place): widget **0.5%** (0.25 was too busy), always-include `/tides|maveth/i`, show `Name (x%)` on widget labels, `minShowLabelAngle:.3` on Mining pool pie. ~33 named slices + pinned tides* at 1w. Re-apply: `scripts/_patch_mempool_pool_pie.sh`. Persisted: `mempool-tn4/patches/pool-pie/*.patched`.

**Locale footgun:** Do **not** `docker cp` one locale’s `930.*.js` over the others. Those chunks bake i18n strings (`\uXXXX`); copying `ko`/`ja`/`zh` onto `en-US` makes the English UI show Asian text. Fix: restore each locale from the image, then patch per file (`_fix_mempool_locale_pie_patch.sh`).

**Duplicate `pools.unique_id` sweep (2026-09-02):** Collisions (Joque∩Tides.Maveth, SilentWave∩Tides.Veganic, Helios∩Tides, Roughnecks∩strukt, Kilombino∩Knots, … plus `unique_id=-1` TN4 dupes) made mempool show the wrong pool name for a shared id. Sweep: `scripts/_sweep_dup_pool_unique_ids.py --apply` (keep TIDES/regex winners; move losers to 9700+; delete exact -1 dupes). Then retag TIDES-site heights: `scripts/_rematch_tides_site_blocks.py --apply` (prefer tides* when coinbase has `TIDES`). Result: **21/22** site finds tagged tides/maveth on mempool (962658 is real PyBLOCK coinbase). `blocks.pool_id` = `pools.id` (PK); API `pool.id` = `unique_id`. Re-check after Kilombino pools JSON import — it can recreate collisions.

**Mempool pie TIDES merge:** Patch merges all `/tides|maveth/i` pools into one **TIDES** slice (`T/B/H` accumulators + blue `#1E88E5`). Re-apply via `scripts/_patch_mempool_pool_pie.sh`.

**Block rewards missing fees:** Site `reward_sats` was often **exactly 312500000** (subsidy) because find path used `reward_estimate` meta from `estimatesubsidy`, and TLV/value fell back the same. On-chain coinbases had fees (hundreds of k-sats to ~1.8M). Fix (deployed): `estimate_next_reward()` prefers GBT `coinbasevalue`; `_note_block_found` / confirm path set reward from `getblock` coinbase vout sum; `update_block_reward`. Backfill: `scripts/_backfill_tides_rewards_fees.py --apply`.

**False find 965261 / credit gate (2026-09-02):** Gateway `is_block` share at wrong height created synthetic `pool-965261-…`, opened finder credit, then reconcile `height_fix` → 965263. On-chain payouts for 965263 were fine; confusion was UI (coinbaser “Worker” vs block finder). Hardening: **do not record/credit until on-chain block verifies** with **TIDES tag AND ops payout address** in coinbase (`verify_pool_block`). Synthetic claims log and return without credit. UI “Found by” shows **worker · address** (965263 = **GS4** / quamnn). Cache `?v=20260902b`.

**Pending finder wrong after reassign:** Orphaning synthetic 965261 **reopened** Maveth’s 965224 finder credit (`paid_in_height` cleared) while GS4’s credit stayed open → `pending_finder_credit()` returned **Maveth**, so website **and live DATUM coinbaser** would pay Maveth the next finder bonus. Repair: mark id=23 paid_in=965263. Code fix in `reassign_pending_block`: after reopen/move, re-mark oldest unpaid `from_height < new_height` as `paid_in_height=new_height`.

**Coinbase secondary = nickname (2026-09-02):** After a verified find, parse secondary tag after `TIDES` (e.g. `Bitcoin ForkLift`) into `users.last_nickname`. Shown on blocks Found-by, contributors, and coinbaser. Stratum worker (GS4) ≠ nickname. Cache `?v=20260902c`. Future finds auto-save nickname in `datum_prime` + `block_confirm`. Backfill older finds: `scripts/_backfill_tides_nicknames.py --apply` (skips NYPost headline; mempool ascii must skip OP_PUSH bytes). Applied 2026-09-02: quamnn→Bitcoin ForkLift, Maveth→Maveth_tides, BoxII→Veganic, DonSATS addr, sclite/mhr6091 (962658 — check if still wanted; coinbase may not be “ours”).

**Mempool TIDES coinbaser labels (2026-09-02):** `customize.js` overlay (bind-mount `mempool-tn4/custom/customize.js`) rewrites TIDES-only block pool labels to **`Secondary (TIDES)`** (e.g. `Bitcoin ForkLift (TIDES)`) on the top block strip + block/mining views, with a **soft teal glow**. Parses `coinbaseSignatureAscii` after `TIDES`. Other pools unchanged. Also fixed a pre-existing syntax error (`/\\/block\\//`) that aborted the whole customize script, and stopped customize from clobbering `KEEP_BLOCKS_AMOUNT` back to 8 (config.js owns 24). Mirror: `deploy/truenas-scale/mempool-tn4-custom/customize.js`.

**Label stickiness:** Angular reverts badge text to pool name (`Tides.Maveth`) after our DOM rewrite; must re-check **visible text** (not only `data-bip110-tides`), MutationObserver + ~1.2s re-apply, XHR+fetch hooks, and per-block `/api/v1/block/…` fetch on detail pages. Nginx `location /resources/customize.` set to **no-store** (was 5m) so fixes apply without waiting; host copy `mempool-tn4/custom/nginx-mempool.conf` (container conf is not bind-mounted — re-apply after web recreate via `scripts/_fix_mempool_customize_cache.sh`).

**Banner removed (2026-09-02):** Dropped the sticky “BIP110 Blake2b TN4 / RC3” note — legacy and wrong for live Blake. `customize.js` now calls `removeLegacyBanner()` so old tabs lose it too.

**TIDES glow scope:** Full-3D teal via **`filter: drop-shadow`** on `.bitcoin-block` (follows isometric `::before`/`::after`) plus a soft **`.bip110-tides-ring`** on the front face. Do **not** use plain `outline` on the cube — that paints under the 3D faces. No glow on height/hash/detail tables.

**Charts + 30s refresh (2026-09-02):** Main page combined chart (pool HR · network est. · find markers) with **1h / 24h / 1w**; miner page personal HR + their finds. APIs: `/api/charts/pool`, `/api/user/{addr}/charts`. Share buckets via `share_work_buckets`; indexes `shares_accepted_at_idx`, `shares_address_accepted_at_idx`. Dual Y-axes (pool left, network right **0–4 PH/s**). Chart.js CDN. Soft refresh every 30s when tab visible. Cache `?v=20260902i`. Working copy: `_tmp_tides_charts/`. Payout window highlight: teal band from **8th-last confirmed find → now** + bright dots for the **7 in-window finds** (cutoff find muted); label `7 confirmed + current`.

**DATUM GBT refresh (2026-09-02):** Live TIDES gateway `bip110-datum-pool` `bitcoind.work_update_seconds` **5 → 20** (blocks ~2 min; 5s was too chatty). Config: `/mnt/Alexandria/local/tides-pool/deploy/datum-pool/config.json`. `bip110-datum-pow` already at 30. Restart gateway after change.

**Network hashrate history (2026-09-02):** Table `network_hashrate_samples(sampled_at, hs)`. Sampled ~every 60s in `chain_sync` via Knots `getnetworkhashps(120)` (same as site cards). Chart prefers samples (`network_source=samples`). Seeded linear ramp **2026-08-30 00:00 UTC @ 500 TH/s → first live sample (~2.78 PH/s)** hourly (`scripts/_seed_network_hr_aug30.py --apply`); does not overwrite live samples. Cache `?v=20260902n`.

**Miner payout totals:** `/api/user/{addr}` adds `total_earned_sats` (replay each confirmed find’s TIDES window split + paid finder credits) and `unpaid_pending_sats` (open finder credits only — est. next is a separate card). UI cards on address page: **Total earned** + **Est. next** (tides share + pending finder if yours). Unpaid pending card removed.

**Miner page UX (2026-09-02):** Cache `?v=20260902q`. Miner HR chart axis **always TH/s** (`fmtHashrateTH`, e.g. `0.12 TH/s` not `117 GH/s`). Layout under chart: collapsed **Payout history** → collapsed **Recent shares** (25/page pager). API `/api/user/{addr}/payouts` reconstructs tides lines + finder credits. Deploy from `_tmp_tides_charts/` via `scripts/_deploy_tides_charts.sh` (also copies `models.py`). Do not use `curl -I` against uvicorn for smoke — HEAD → 405.

**Blocks Ago + miner Last find (2026-09-02):** Cache `?v=20260902r`. Recent/all blocks tables add **Ago** column (`fmtAge` from `accounted_at`, hover = UTC). Miner cards add **Last find** = `height · age` from `UserStats.last_find_*` (newest non-orphan real find by that address as finder).

**Blocks Worker/Nickname columns (2026-09-02):** Cache `?v=20260902s`. Split combined Found-by into **Worker | Nickname | Address** (uses existing `finder_worker` / `finder_nickname` / `finder_address`). Same on `/blocks`.

**Nickname OP_PUSH junk (2026-09-02):** Live `extract_secondary_tag` allowed `.` in nicknames; `coinbase_ascii` maps nul/OP_PUSH to `.`, so DonSATS block 966083 stored `DonSATS......P.q`. Backfill already skipped leading `P.`/`q.` but not trailing glue. Fix: disallow `.` inside nicknames + `sanitize_nickname()` (cut on `..`, require `[A-Za-z]{3,}`, reject headline/high bytes); `set_address_nickname` sanitizes on write. Cleaned DB → `DonSATS`. Files: `block_confirm.py`, `store.py`.

**Manual nickname:** `bc1qlfhztudrsatw2n2nfwqvckhduj350r826h6fvz` (worker `hs`) → **ChatLab** (user-confirmed 2026-09-02; coinbase secondary not on those finds). `scripts/_set_chatlab_nick.sh`.

**NPM Asset Caching broke CSS (2026-09-02):** External report (Brave/Zorin) “stylesheet broken”. Root cause: NPM proxy host `21.conf` (`tides.maveth.ca` / `riptide.maveth.ca`) had **Asset Caching** (`include assets.conf`) which caches `*.css`/`*.js` with key `$host$request_uri` and **`proxy_cache_use_stale … http_502`**. After a tides-pool restart, `style.css?v=…` / `app.js?v=…` got cached as **502**; bare `/static/style.css` still 200. HTML 200 + CSS 502 = unstyled site in any browser. Fix: comment out `assets.conf` on host 21, clear `/var/lib/nginx/cache`, set `caching_enabled=0` in NPM DB, reload nginx. Keep Asset Caching **off** for this host. LAN `:8088` was fine the whole time (no NPM). No hairpin: LAN→WAN IP `:443` times out — test public via `curl --resolve tides.maveth.ca:443:127.0.0.1 …` on NAS.

**Local timestamps (2026-09-02):** Cache `?v=20260902t`. Tables/tooltips use visitor local time via `fmtLocalTime` (`Sep 2, 5:23:16 PM MDT`); relative **Ago** unchanged. Headers say **When** (not UTC).

**DATUM “flappy” client 192.168.0.202:** Rapid connect/close on `bip110-datum-pool` stratum is a **known miner keep-alive ping**, not a fault. Do not chase as a disconnect bug.

**Vel quarantine / allowlist (2026-09-02/03):** Full address `bc1qrmj0mpccd7e9ytkcnlqlcf0eneayxnasq8nvel`. Reject-27 then temp allowlist; ops reinstate. **Footgun:** with Prime coinbaser timing out → Gateway **0-output** jobs, `_assigned_multi_out()` is false so `cb_id=0` shares count as OK and **rehab auto-clears quarantine** after 3 goods. Fix: reasons starting with `ops ` skip rehab (`ops quarantine hold`). Sticky Q reason currently set. Also: `is_block=true` ~00:07 UTC with no `blocks`/finder row for Vel. Investigate coinbaser timeouts + false is_block separately.

**docker compose recreate wipes UI:** `tides-pool` image is Aug 30-ish; live UI/api live in `/mnt/Alexandria/local/tides-pool/src/` and are **docker cp**’d into the container. `docker compose up -d` / recreate resets container files to the image → site falls back to `app.js?v=20260830*`. After any recreate, re-cp static + api/store/models/block_confirm/chain_sync/datum_prime/config from Alexandria host tree (script `_restore_tides_ui_now.sh`). Better long-term: bind-mount `src` or rebuild image from current tree.

**Vel work removed from payouts (2026-09-03):** Address `bc1qrmj0…nasq8nvel`. Full copy in `shares_vel_zero_backup` (1205 rows / 19.7M work). Live `shares` rows for Vel **deleted** after backup (work=0 hit `shares_work_check` / `window_since_seq` ValueError → API 500). Also patched `tides.py` to `continue` on `work < 1` instead of raising. Result: Vel window/coinbaser **0**; others’ coinbaser fair. `share_attempts` untouched. Still ops-sticky quarantined.

**Vel Gateway config (2026-09-03):** His DATUM had `coinbase_tag_primary/secondary: BLAKERUNNERS` (not `TIDES`), `pool_address` = his payout, `pool_host: tides.maveth.ca:28916` + correct pubkey, `pooled_mining_only: false`. Log shows **BLOCK FOUND 966115** `0000…23be76be…` accepted by node; TIDES did not credit because coinbase was **ops-only single out** (no miner split) despite `TIDES.BLAKERUNNERS` tags. Come back: **Prime coinbaser timeouts** on lab GW (`Timeout waiting for coinbaser response from DATUM Prime` → `Generating coinbases for up to 0 outputs`) dense from ~23:53 Sep2 through Vel’s find window — root-cause Prime `_coinbaser` stalls/recreates.

**New-miner probation + rehab=5 (2026-09-03):** Do **not** assume good at first connect. `probation_good_shares=5`: no window credit until 5 consecutive good **multi-out** shares (`probation-good`); soft/non-multiout jobs → `probation-wait-multiout` (no credit, no streak). `users.probation_cleared_at` marks graduation; existing addrs with any `shares` row backfilled as cleared. Quarantine rehab now **5** (`quarantine_rehab_shares`), not 3. Ops-sticky (`ops …` reason) still never auto-clears.

**Vardiff vs coinbaser timeouts (2026-09-03):** Prime only sends **floor** via 0x99 configure (`TIDES_MIN_SHARE_DIFFICULTY=2048`) — does **not** re-tune per share. Climbing is **Gateway stratum vardiff** (lab: `vardiff_min=4`, `vardiff_target_shares_min=15`; job refresh `work_update_seconds=20`). Vel stayed at **work=16384** (~50 shares/min) while Don sat **2048–8192**; if target ~15/min, Vel should have climbed toward **64k+**. Slow/stuck vardiff → more share/job pressure. Coinbaser fetch is per **job**, ~every work_update, not per share — but Prime `_coinbaser` does `list_shares_newest(50_000)` + `split_reward` on the **asyncio event loop** (one process, ~1 core busy). Share flood + heavy coinbaser = responses late → Gateway `Timeout waiting for coinbaser` → `0 outputs`. Multicore would help only if coinbaser/share CPU moves off the loop (`to_thread` / workers); more Gateway threads alone won’t fix Prime serialization.

**Coinbaser cache + Q throttle (live 2026-09-03):** Pool-side only (`work_update` stays Gateway). `CoinbaserSplitCache`: reload window via `list_shares_after_cutoff` (no 50k cap) every `TIDES_COINBASER_CACHE_SECONDS` (default **15**) or on block-found invalidate; `0x10` rescales cached shares on `asyncio.to_thread`. Incremental `note_share` between reloads. `QuarantineGuard`: in-memory attempt ring; auto-Q from ring; clean miners every `TIDES_QUARANTINE_CHECK_EVERY_N` (default **10**); hot (r27) every reject; quarantine/probation flags cached. Deployed + restarted on `deploy-tides-pool-1`. GitHub: [`Maveth/tides-pool`](https://github.com/Maveth/tides-pool) commit **`3b4b6fe`**. Post-restart check (~30m): Prime **0** errors, **551** multi-out coinbasers, cache refresh ~0.14–0.20s; pool GW **0** timeouts, gen **9** outs (one brief 0-out during restart); `/api/coinbaser` address prefixes **MATCH** Prime. Local mirror: `_tmp_tides_pool_live/` / publish `_tmp_tides_pool_gh`.

**Ops-manual finds (live 2026-09-03, going forward only):** If on-chain coinbase is **TIDES tag + single value out to ops**, treat as pool find (`payout_mode=ops_manual`), confirm → **window advances**, freeze `intended_payout_json` snapshot, UI badge **manual payout** / **manual paid**. Flip done via SQL/`set_block_payout_meta` (no public checkbox v1). Single out to **non-ops** still rejected. **No backfill** of past finds (e.g. 966115). Classify: `block_confirm.classify_pool_coinbase`. GitHub: [`385b1a9`](https://github.com/Maveth/tides-pool/commit/385b1a9). Mark done: `UPDATE blocks SET manual_payout_done=true WHERE height=… AND payout_mode='ops_manual';`

**Health strip (live 2026-09-03):** `/health` and `/api/health` return real `ok|degraded|down` with Prime listening, gateway sessions, coinbaser cache age/outs/p99, RPC, DB, manual-payouts-pending. Header chip on main UI polls every 30s (`?v=20260903b`). HTTP stays 200 so the strip can show degraded; alert on `status != ok`. GitHub: [`2e04870`](https://github.com/Maveth/tides-pool/commit/2e04870). Live check: status=ok, GW=5, outs=9, cache~7s.

**Chart find-dot clip (2026-09-03):** Pool/user charts used a tight `x.max` on the last bucket + `layout.padding.right: 0`, so yellow find markers at “now” were cropped for a few minutes (worse on 1w). Fix: `chartXBounds` pads max by ~3% of span (15m–6h) and includes block timestamps; layout right pad 12px. Live `?v=20260903d`.

**How to connect → Leo official (2026-09-03):** Prefer Leo StartOS [`pow_0.4.1_18`](https://github.com/Retropex/datum-gateway-startos/releases/tag/pow_0.4.1_18) / Umbrel Bitcoin-store; keep experimental MaVeTh [`pow_0.4.1_20`](https://github.com/Maveth/datum-gateway-startos/releases/tag/pow_0.4.1_20). Dash no longer says BIP110 RC4. Live `?v=20260903f`.

**Site vs DATUM coinbaser amounts (2026-09-03):** Website used `reward_estimate` from GBT with `rules:[segwit]` only → Blake Knots refused → fell back to **subsidy 312500000** while Prime/Gateway used live template (~31255xxxx with fees). Fix: `estimate_next_reward()` asks `segwit+blake2b` first. After deploy, site reward≈312539998.

**Site vs Gateway coinbaser still diverged (same day):** Website `/api/coinbaser` recalculated via `_payout_window` + `split_reward`; Gateways used `CoinbaserSplitCache.build_outs(template_value)`. **Not the same path.** Fix (live `?v=20260903h`): site now calls the Prime cache with `last_prime_value` — verified same reward, same first 5 addrs as Prime log.

**Reject-27 quarantine (same day):** `bc1qe2xd2…rig1` and `bc1qq79kr…` hit reject-27 (`coinbase_id=0`) and auto-Q — classic pre-`_18` / type-0 Gateway. Separate noise: `bad payout address` for bare worker names (`rig1`, `MelvynsMiners_*`) without a bc1 username.

**Pool Pass Full Users (2026-09-03):** Tell miners **OFF** — use “Send as worker names”; bare names + full-pass → reject 14. Screenshot `/static/pool_pass_full.png`. Health: `checks.bad_payout` (`rejects_total` / `rejects_1h` / `top`) + warning `bad_payout_username_1h:…`. Live `?v=20260903g`.

**Gateway UA on Prime (2026-09-03):** Handshake `client_ua` now stored per session; `/api/health` exposes `gateway_uas`, `ua_handshakes_top`, `ua_reject27_top`, `ua_bad_payout_top`. Reject logs include `peer=` + `ua=`. Historical 48h: ~95% of reject-27 near `v0.4.1-beta/UNKNOWN_GIT_HASH` (Leo packages); `bc1qe2xd2…` = 1199/1200 UNKNOWN (IP `187.189.167.129`). Cannot distinguish Leo `_17` vs `_18` from UA alone — both UNKNOWN.

**Knots FINAL cutover (2026-09-03):** `bip110-knots-main` **RC4 → final** via official binary tarball → image `bip110-knots-pow:final` (`v29.4.1.knots20260508`). Tip unchanged **966264**; RPC back ~9s; TIDES `rpc_ok`. Headline hardcoded (PR #385) — remove `-blake2b_headline` from compose. Rollback: compose bak `*.bak.final-cutover.*` + recreate with `:rc4`. Script: `_nas_build_final_bin.sh` / `_cutover_knots_final.sh`.

### 2026-09-03 — CONVOY Gateway lab review (Phase 0; live untouched)

- **Clone:** `_tmp_forks/convoy-datum-gateway` @ **`b9ea7dc`** (`Merge branch blake2b_pt2`, Luke, 2026-09-03). Official Blake GW per btc-blake2b.org (need ≥ `56c31f4` for H1 header-v2 bit; tip includes that).
- **Compare baseline:** InnerHat prove tip **`2fea7e5`** (lab `:23436`) + live TIDES Prime `0x99` builder (`datum_prime.py` configure **version 1**).
- **Live spot-check (observe only):** `http://192.168.0.143:8088/api/health` → `status=ok`, `prime_listening`, `gateway_sessions=12`, `rpc_ok`. **No** NAS restarts / deploys for this work.
- **Why CONVOY ≠ live Prime today (wire incompat):**

| Configure field | Live / InnerHat (`2fea7e5`) | CONVOY `b9ea7dc` |
|-----------------|-----------------------------|------------------|
| `0x99` version byte | **1** | **3** (else “Bad configuration version”) |
| Payout script | `scriptsig`, buf up to 256 | `scriptpubkey`, **`MAX_OUTPUT_SCRIPT_LEN=83`** |
| `prime_id` | **`uint32` / `<I`** (TIDES packs `0x71DE5001`) | **`uint64` / `upk_u64le`** |
| After prime_id | coinbase tag | **40-byte resume token** then tag |
| Trailer | `\x00\xfe` | `config_flags` + `0xFE`; **`DATUM_CONFIG_FLAG_ABW_DISABLED=0x01`** |
| ABW | none | Full ABW; may defer readiness until ABW assigned unless flag disables |
| Tag budget | ~88 combined heuristic | `MAX_COINBASE_TAG_SPACE=82` (tighter with 64-bit prime id) |

- **Expected first failure vs current Prime:** handshake may succeed; configure parse fails immediately on version≠3 (or mis-aligns prime_id/resume/tag if somehow forced). Lab Prime **must** grow a configure-v3 builder (and decide ABW on/off) before pooled CONVOY e2e — **lab-only**; do not ship to `deploy-tides-pool-1` until proven.
- **Lab port map (Windows-only stack next):** UI **8188** / Prime **28926** / GW stratum **23436** / GW API **7255** / Knots regtest RPC **18543**. Forbidden: **8088 / 28916 / 23336 / 23335** and mainnet datadir `O:\Users\memys\AppData\Roaming\Bitcoin`.
- **Plan:** Windows workstation isolated regtest (Knots-final + lab Prime + CONVOY). Phase 0 done; Phase 1+ needs explicit OK.

### 2026-09-03 — CONVOY lab Phase 1: Knots-final regtest on Windows

- **Binary:** downloaded `bitcoin-29.4.1.knots20260508-win64-pgpverifiable.zip` (SHA256 `125bed64…07392` OK) → `O:\bip110-lab\knots-final-win64\…\bin\bitcoind.exe` reports **`v29.4.1.knots20260508`** (no rc).
- **Datadir:** `O:\bip110-lab\knots-regtest-data` only — **not** `O:\Users\memys\AppData\Roaming\Bitcoin`. Mainnet `bitcoin-qt` (RC4) left running.
- **Ports:** RPC **18543**, P2P **18544**, bind `127.0.0.1`. Conf needs network opts under `[regtest]` section.
- **Blake:** `-testactivationheight=blake2b@1` + `-blake2b_headline=CONVOYLAB` (regtest-only). `getdeploymentinfo` → `blake2b.height=1 active=true`. Mined 5 blocks; tip header `header_version=2`.
- **GBT:** `rules` include **`!blake2b`** (plus `!segwit`, csv, taproot). CONVOY can follow GBT without `pow_algorithm`.
- **Helper:** `O:\bip110-lab\start-knots-regtest.cmd`. Prefer keeping bitcoind as a long-lived process (Start-Process Hidden can exit with the parent shell).
- **Live untouched:** `:8088/api/health` still `ok`, `gateway_sessions=12`, `rpc_ok`.
- **Next:** Phase 2 lab Prime on **8188/28926** (needs Docker Desktop or native uvicorn+Postgres). CONVOY still needs Prime configure **v3**.

### 2026-09-03 — CONVOY lab Phase 2: native Prime + configure v3

- **DB:** NAS `deploy-postgres-1` `:5433` → **new** DB `tides_convoy_lab` (prod `tides` untouched; also existing `tides_prove`). Schema from `sql/001–003`.
- **Code:** lab-only copy `O:\bip110-lab\tides-pool-src\` (not deployed to NAS). `datum_prime.py` sends **0x99 configure v3**: u64 prime_id, 40-byte resume token, `config_flags=0x01` (**ABW disabled**), scriptPubKey ≤83.
- **Run:** `O:\bip110-lab\start-tides-lab.ps1` + `tides-lab.env` → UI **127.0.0.1:8188**, Prime **127.0.0.1:28926**, RPC → lab Knots **18543**, ops `bcrt1q609l7…`.
- **Health:** lab `/api/health` `status=ok network=regtest prime_listening`; live `:8088` still `ok` / main / GWs up.
- **Pool pubkey (lab):** in `O:\bip110-lab\tides-data\pool_keys.json` → use for CONVOY `datum.pool_pubkey`.
- **Next:** Phase 3 build/run CONVOY GW `@b9ea7dc` → stratum **23436**, pool_host `127.0.0.1:28926`. Docker Desktop was stopped earlier — start it or find another Linux build path.

### 2026-09-03 — CONVOY lab Phase 3: Gateway docker + handshake

- **Image:** `datum_gateway:convoy-b9ea7dc` built from `_tmp_forks/convoy-datum-gateway` @ `b9ea7dc`.
- **Container:** `convoy-gw-lab` — publish **127.0.0.1:23436** (stratum) + **7255** (API); `host.docker.internal` → Windows Knots `:18543` + lab Prime `:28926`.
- **Config:** `O:\bip110-lab\convoy-gw\config\config.json`. Note: **`bcrt1` bech32 panic** (“Could not generate output script for pool addr”); use **legacy regtest** `n…` / `m…` for `mining.pool_address`.
- **Handshake proof:** Prime log `client_ua='v0.4.1-beta/b9ea7dc3…+'` + `sent 0x99 configure v3 … abw=disabled`. Lab health `gateway_sessions=1`. Live `:8088` still ok (~11 GWs).
- **Stratum smoke:** subscribe/authorize OK; `mining.notify` with **8-byte ntime** hex (`00000000…`) + merkle list — Blake header-v2 shape.
- **Next:** Phase 4 `sia_mine` / CUDA against `127.0.0.1:23436` for share/block → Prime credit.

### 2026-09-03 — CONVOY lab Phase 4: CUDA share + regtest block

- **Miner:** `config.lab-convoy-regtest.yaml` → `python -m bip110miner.sia_mine --config … --cuda --device 0 --intensity 22 --seconds 90 --exit-on-share`.
- **Result (~18s):** stratum accept **True**; GW `BLOCK FOUND 0000000001c9d086…` → `submitblock` OK (second path `duplicate` normal); Knots tip **height 6** = that hash.
- **Prime:** coinbaser replies (empty window → **1 out** ops); share logged `REJECT new-miner probation: waiting for multi-out job … block=True` — expected with no share history / single-out. On-chain find still landed via Gateway→Knots.
- **Live:** `:8088` still `ok`, ~11 GWs. Lab stack: Knots `:18543`, Prime/UI `:28926/:8188`, CONVOY docker `convoy-gw-lab` `:23436`.

### 2026-09-03 — Lab dual configure + multi-out seed (not live)

- **Dual `0x99` (lab Prime only):** UA contains `b9ea7dc` / `convoy` (or `TIDES_CONFIGURE_V3_UA_SUBSTR`) → **v3 + ABW disabled**; else classic **v1**. Code: `O:\bip110-lab\tides-pool-src\tides_pool\datum_prime.py` (`_ua_wants_configure_v3`).
- **Multi-out seed:** `python O:\bip110-lab\seed_lab_shares.py` inserts shares for miner A + B into `tides_convoy_lab` → coinbaser **3 outs** (A + B + ops). Single miner alone with fee also yields **2 outs** (miner + ops); empty window = 1 out (ops-only).
- **Lab env:** `TIDES_PROBATION_GOOD_SHARES=1` for faster credit smoke; `TIDES_CONFIGURE_V3_UA_SUBSTR=b9ea7dc,convoy`.
- **Still needed for full e2e:** CONVOY container up → confirm log `configure v3-abw-off`; CUDA share with `coinbase_id!=0` matching multi-out assignment; probation clear + share credit. Docker Desktop may need a manual start if engine is down.

### 2026-09-03 — Live Prime: CONVOY dual-configure (v3+ABW-off)

- **Deployed** to `deploy-tides-pool-1` (+ Alexandria copy). Backup: `datum_prime.py.bak.convoy-v3-*` in container.
- **Behavior:** UA contains `b9ea7dc` or `convoy` (or `TIDES_CONFIGURE_V3_UA_SUBSTR`) → configure **v3 + ABW disabled**; else classic **v1**. Normal Leo/InnerHat/MaVeTh users unchanged.
- **Does not** fix Blake type-0 / empty-tip Gateway behavior — only handshake/configure readiness for CONVOY.
- Local mirror: `_tmp_tides_pool_live/datum_prime.py` (from live pre-patch + dual-speak).
- **GitHub:** [`Maveth/tides-pool`](https://github.com/Maveth/tides-pool) commit **`732d657`** — dual-speak Prime + How-to-connect “Experimental support for CONVOY DATUM (ABW-disable mode)”. Live UI `?v=20260903i`.

### 2026-09-03 — Lab Fly v1 GW + rotate miner

- **Fly lab GW:** image `datum_gateway:fly-lab-121edd0` (`_tmp_forks/flyelephant-datum` @ `121edd0`), container `fly-gw-lab`, stratum **127.0.0.1:23437**, API **7256**. Same lab Knots/Prime.
- **Dual configure proven live on lab Prime:** CONVOY UA → `configure v3-abw-off`; Fly UA `121edd0…` → `configure v1`. Both sessions concurrent (`gateway_sessions=2`), coinbaser `outs=3` after seed.
- **Rotate miner:** `O:\bip110-lab\rotate_lab_mine.py` — new legacy regtest addr per phase, random share target, switch CONVOY↔Fly. Summary → `solo_out/rotate_lab_summary.json`.

### 2026-09-04 — NAS pool cutover: CONVOY on ASIC ports

- **Done.** ASICs keep pointing at stratum **23336** / API **7155** — no miner reconfig needed.
- **Live pool GW:** container `bip110-datum-pool` → image `datum_gateway:convoy-b9ea7dc` (CONVOY tip). Config: `/mnt/Alexandria/local/tides-pool/deploy/datum-pool-convoy/config.json`.
- **Parked Fly (former pool GW):** `bip110-datum-pool-fly-parked` on **23346** / **7165**. Config: `…/deploy/datum-pool-fly-parked/config.json`. Pre-cutover container renamed `bip110-datum-pool-fly-pre-convoy` (stopped).
- **Settings matched from old pool:** `pool_address=bc1q3l0q76kahen6m3n4d7adksq4mxmvutyndqd5f9`, tags `TIDES` / `Maveth_tides`, Prime `127.0.0.1:28916`, same pubkey, `pool_pass_workers=true`, `work_update_seconds=20`, stratum `vardiff_min=4`, `allow_submitblock=false`. CONVOY-specific: `allow_hasher_time_rolling=false`, `abw_verify_all_shares_on_disclosure=false`; dropped Fly `pow_algorithm` / blake headline keys.
- **Prime dual-speak confirmed live:** CONVOY UA `b9ea7dc…` → `configure v3-abw-off`; parked Fly UA `121edd0…` → `configure v1`. Coinbaser multi-out OK (`Generating coinbases for up to 14 outputs`).
- **Scripts:** `_nas_build_convoy_datum.sh`, `_nas_prep_convoy_pool_config.py`, `_nas_cutover_pool_to_convoy.sh`, `_check_convoy_cutover_now.sh`.
- **Rollback:** stop CONVOY `bip110-datum-pool`; rename/start Fly on 23336/7155 from parked config (or revive `bip110-datum-pool-fly-pre-convoy` after port fix). Do **not** wipe TIDES DB.
- **Note:** `192.168.0.202` flappy connect/close on stratum is known keep-alive ping — not a CONVOY fault.
- **ASIC dual-pointer failover:** MaVeTh Goldshell has **2 stratum pointers**. When pool GW was briefly down for cutover, miner **auto-flipped to the 2nd pointer (normal DATUM solo)** → ~4 min gap with no `Maveth.ASIC` credits on Prime (`02:39`–`02:41Z`). Manual switch back to pool pointer → CONVOY `Share accepted` + Prime `share OK` resumed (`02:42Z+`). Future pool-GW restarts: expect failover to solo pointer unless both pointers are pool or miner is pinned; check miner UI after cutover.

### 2026-09-04 — Lab ABW-on stub (Windows Prime only)

- **ABW** = Anti-Withholding (Blake xor-key assignment). Live NAS stays **ABW-off**.
- **Lab-only flag:** `TIDES_ABW_ENABLE=1` in `O:\bip110-lab\tides-lab.env` → configure **v3-abw-on** (`config_flags=0`) then mining `0xA8` **ACTIVE** assignment notice (slot 0, non-null xor key / TaggedHash key_hash).
- **Code:** `O:\bip110-lab\tides-pool-src\tides_pool\abw.py` + `datum_prime.py` / `config.py` (`abw_enable`). Default **False** without env.
- **Proven:** Prime log `configure v3-abw-on` + `sent ABW assignment notice ACTIVE`; CONVOY reconnect → MOTD, coinbaser **outs=3**, stratum `mining.notify` (8-byte ntime). No “Waiting for … anti-withholding assignment”.
- **CUDA e2e (lab):** `sia_mine --config config.lab-convoy-regtest.yaml --cuda --device 0 --intensity 22` → stratum **accept**; under ABW-on, Prime saw multi-out `rehab-good` (`coinbase_id=2`, attempt id 9 @ 03:24Z). Later finds sometimes `coinbase_id=0` / reject-27 “not multi-out” (known Blake empty/type-0 job race — **not** ABW-specific). Lab vardiff temporarily lowered 64→4 for faster finds; miner user `labconvoy` (worker-only; GW prepends pool_address).
- **Not done:** reveal rotation, candidate receipt/release, ABW-shaped `0x8F`, NAS deploy. Do **not** set `TIDES_ABW_ENABLE` on live compose.

### 2026-09-04 — Live Prime: blank-nick from POW coinbase (staged, no restart)

- **Behavior (loads on next tides-pool restart):** on the same throttle as reject-27 auto-Q (`quarantine_check_every_n`), if `users.last_nickname` is blank and POW carries section **`0x02`**, parse secondary after primary via `extract_secondary_tag` → `set_address_nickname`. Skip addresses that already have a nick (in-memory `_nick_has` cache).
- **Files:** Alexandria `…/tides_pool/datum_prime.py` (+ bak `*.bak.nick-pow.*`); also `docker cp` into `deploy-tides-pool-1` **without** restart. Live process still runs old code until next reset.
- Does **not** invent nicks when `0x02` absent or secondary generic/empty.

### 2026-09-04 — Lab ABW reveal rotator

- Lab Prime (`TIDES_ABW_ENABLE=1`, `TIDES_ABW_ROTATE_SECONDS=60`): after ACTIVE `0xA8`, background task every N s sends **`0xA9` reveal** then **new ACTIVE notice** on next slot `(slot+1)%16`.
- Proven: Prime `sent ABW reveal slot=0` → `ACTIVE slot=1`; CONVOY `DATUM server retired BLAKE2b assignment slot 0`.
- **Cross-rotate CUDA (2026-09-04):** ~100s mine during slot 13→14 rotate: stratum **acc=15 rej=2** (stale-prevblk on job churn OK); Prime later **`share OK` cb_id=2** after rehab. Jobs kept flowing; type-0 treated as regtest noise for this suite.
- **Candidate receipt/release:** on share ack when ABW-on + POW has TLV `0x05` slot → ABW-shaped `0x8F` + **`0xA5` receipt** + **`0xA7` release**. Open GW `0x27` omits `raw_pow_hash` → zero hash (wire-valid; forget no-op when `abw_verify_all_shares_on_disclosure=false`). Lab: `ABW share ack+receipt+release … slot=0`; no CONVOY “Invalid … candidate receipt”.
- Code: `O:\bip110-lab\tides-pool-src\tides_pool\{abw,datum_prime,config}.py`.

### 2026-09-04 — Live: ABW code staged, **disabled**, no restart

- Alexandria + `deploy-tides-pool-1` files updated: `abw.py`, `datum_prime.py`, `config.py` (`abw_enable=False`, `abw_rotate_seconds=0`). Compose has **no** `TIDES_ABW_ENABLE`.
- **Running process unchanged** until next tides-pool restart → still current ABW-off dual-speak + old nick behavior until then.
- On next restart (without enabling ABW): nick-from-POW throttle + ABW code present but **v3-abw-off** unless `TIDES_ABW_ENABLE=1`.
- Backups: `*.bak.abw-stage.20260904T042459Z` (+ prior `*.bak.nick-pow.*`).

### 2026-09-04 — Day-1 payout audit (READ-ONLY)

- Script: `scripts/_audit_tides_payouts_day1.py` → NAS `/tmp/tides_payout_audit_day1.json` (+ local `_tmp_tides_payout_audit_day1.json`).
- Compares each `blocks` row’s **on-chain coinbase vouts** (Knots `getblock … 2`) to DB `reward_sats` / `intended_payout_json` when present.
- Result snapshot: **32** rows, **31** match reward total, **1** Δ=155 sats (#965263), **1** rpc fail (synthetic `pool-965261-…`), early **#961831/#961857 ops-only**, orphan **#962598** still has multi-out on that orphan hash. **No `intended_payout_json`** on historical multi-outs (snapshots were ops_manual-era).
- **That audit is NOT website Payment history vs coinbase.** It only checked block reward totals.

### 2026-09-04 — Payment vs coinbase verify (LIVE is the real gate)

- **Default work on LIVE**, not lab — unless user says lab project, or change is high-risk enough that lab dry-run is critical (see `Agents.md`).
- Helper: `tides_pool/payment_verify.py`. Fixture unit tests: `tests/test_payment_verify.py` (optional local).
- **Real gate for us:** `scripts/_live_verify_payments_vs_coinbase.py` on NAS — last confirmed find (UI Payment history vs chain; intended JSON if present) + **current** web `/api/coinbaser` vs prime `/api/coinbaser`. Exit 1 on hard mismatch.
- Run after payout-touching deploys: scp script → `nas_run` / `PYTHONPATH=.../src python3 /tmp/_live_verify_payments_vs_coinbase.py`.

### 2026-09-04 — GitHub not updated yet (credits for nick prefetch)

- **`Maveth/tides-pool` GitHub:** tip **`d7c4f79`** (2026-09-04) — README/DESIGN mainnet refresh + collapsible tables / chart axes / web-only health IP mask. Prior: `2da0407` IP mask, `1ba25dd` hide luck, `deddc4e` web/prime split + UI polish.
- When committing **user nickname prefetch** (POW TLV `0x02` / secondary-tag path): credit **iohzard** for the pre-fetch method, and **Taki** for pointing it out. Put in commit message + short comment near the nick-from-POW code.

### 2026-09-04 — Historical Fly vs CONVOY r27 (rotated logs)

- Logs still on NAS: `/mnt/Alexandria/local/tides-pool/datum-pool-logs/gateway-pool.log*` (+ `gateway-pool-fly-parked.log` idle).
- **Fly long-run (Aug 23–30):** ~**0** reason-27 / day (thousands of accepts) — matches “Fly worked with less.”
- **Aug 31:** first r27 appear (~0.9 / 1k accepts) — likely Prime multi-out enforcement turning on.
- **Sep 1–2 (still Fly active):** **6.8–9.1 r27 / 1k** — not clean; similar or worse than CONVOY now.
- **Sep 3:** 58 r27 + **67** coinbaser timeouts (bad day).
- **Sep 4 CONVOY active `gateway-pool.log`:** ~**6.2 r27 / 1k** (80 / 12990) + 11 timeouts.
- Takeaway: climbing counter ≠ “only CONVOY is broken”; Aug Fly was excellent, early-Sep Fly already had the same class of rejects. CONVOY continues the drip via job/coinbaser races.

### 2026-09-04 — CONVOY reject-27 / coinbase_id=0 (not GPUs)

- Live pool GW: `bip110-datum-pool` image **`datum_gateway:convoy-b9ea7dc`** (UA `b9ea7dc…`). Fly `121edd0` is **parked** (no miners).
- DATUM UI “pool shares rejected ~68–74” = **reason 27**, all **`block=False`** (not block finds).
- **~43/74 (58%) of r27 within 30s of `Timeout waiting for coinbaser`** — CONVOY falls through to type-0 / `coinbase_id=0` when coinbaser fetch times out; Prime correctly rejects (no credit).
- 11 coinbaser timeouts today (clustered ~06–08 UTC); after that a slow drip of r27 remains (job-switch race). Prime coinbaser itself is healthy now (15 outs, cache ~0.3s).
- Not a GPU-only lab artifact: **`Maveth.ASIC`** also hits r27 (`coinbase_id=0`). Separate bulk rejects: remote user `theosofica` bad payout address.
- Config OK: `allow_hasher_time_rolling=false` on live CONVOY mount. Next diagnostic: A/B ASIC on Fly parked briefly, or find CONVOY coinbaser-timeout knob / don’t stratum-publish until multi-out ready.

### 2026-09-04 — Contributors “Last share” column

- Between **Shares** and **This block**: `CURRENT` or `N ago` (tooltip has find height).
- `ago=0` → still has shares on unfinished current block; `N` → last shares were during the Nth-last confirmed find (ages out of window → less dilution for actives).
- API: `last_share_blocks_ago`, `last_share_block_height` on `/api/contributors`. Cache `?v=20260904ls`. Web-only deploy.

### 2026-09-04 — Web join pubkey empty after split

- Cause: web role read `pubkey_hex`/`pubkey` from `pool_keys.json`, but file field is **`pool_pubkey`**. Site showed “(paste 128-hex…)” / `/api/pool_pubkey` 503.
- Real pubkey (matches Prime): `b95abf4a11050c5164d09e9d3c4a18a4df735c5d670e3e974fc146794efa4ed9fbc78e7cd4b6abcd3fcc0e0619543d60004fdb49e9d2974582ae11988a03d743`
- Fix: web loads `pool_pubkey` (+ fallback fetch Prime `/api/info`). Deployed web-only restart 2026-09-04.

### 2026-09-04 — Split web vs Prime (LIVE)

- Goal: website deploys/restarts must not bounce DATUM Prime / Gateways.
- **Lab smoke PASS:** `O:\bip110-lab\lab_split_smoke.py` — kill/restart web, Prime `:28926` stayed up; coinbaser outs matched.
- **Live cutover 2026-09-04:** `deploy-tides-pool-1` → **`deploy-tides-prime-1`** (`:28916` + health `:8089`) + **`deploy-tides-web-1`** (`:8088`). Compose: `/mnt/Alexandria/local/tides-pool/deploy/docker-compose.split.yml` (+ base yml). Script: `scripts/_nas_cutover_web_prime_split.sh`.
- Proven live: **restart web → Prime container id unchanged**; Gateways back to **13** sessions; web/prime coinbaser both 15 outs.
- Ops: UI-only changes → `docker restart deploy-tides-web-1` (or rebuild web). Prime/protocol → `deploy-tides-prime-1` only.
- Roles: `TIDES_ROLE=web|prime|all`; web probes `TIDES_PRIME_HOST` (compose: `tides-prime`).

### 2026-09-04 — Chart range: Window (payout period)

- Pool + miner charts: **1h / 24h / 1w / Window**. `range=window` sets x-axis from payout-window cutoff find `accounted_at` → now (length follows find rate: hours…weeks). Bucket auto-scales (`_bucket_for_span`).
- API: `/api/charts/pool?range=window`, `/api/user/{addr}/charts?range=window` (+ `range_sec` in JSON). Cache-bust `?v=20260904w`.
- Deployed live 2026-09-04 (Alexandria + `deploy-tides-pool-1`, brief restart). Script: `scripts/_deploy_chart_window_range.sh`.

### 2026-09-04 — Website Payment history vs on-chain coinbase (all addrs)

- UI “Payment history” = `/api/user/{addr}/payouts` → `_lifetime_tides_share_lines` **reconstructs** each confirmed find by re-running `split_reward` on today’s share DB. It is **not** a snapshot of the coinbase that was mined.
- Veganic (`bc1qk7zr…nh3g8l`) #**966102**: site **0.45011468** BTC vs mempool/chain **0.43723378** BTC (Δ **+1,288,090** sats). Report is correct; chain paid less than the UI shows.
- Root pattern at 966102: `bc1qrmj0…nasq8nvel` is **CHAIN_ONLY** (8,559,960 on-chain, **0** in reconstructed window). Missing window work is redistributed across other miners on the website → systematic overstatements.
- Finder bonuses: site lists `kind=tides` and `kind=finder` separately; on-chain they are often **merged** into one vout. Compare `site_tides + finder_paid_in` vs `chain` (`d_s+f` column).
- Full matrix script: `scripts/_audit_site_vs_coinbase_all.py` (NAS). Artifacts: `tides_site_vs_coinbase_all.{json,csv,txt,xlsx}` in repo root (+ `/tmp/` on NAS). Sheets: `by_block_address`, `lifetime_by_address`, `veganic_all_blocks`.
- Snapshot counts: **266** rows, notes mostly `MISMATCH` / some `SITE_ONLY` / `CHAIN_ONLY`; local recon cross-check matches live API for veganic@966102 (45,011,468).
- **Owe vs UI (966102):** Not the ASIC solo-failover gap. Vel shares were in the window at find (2026-09-02 23:49Z); coinbase paid Vel **8,559,960**. Next day ops **deleted** live Vel `shares` (backup `shares_vel_zero_backup`) and **paid Vel manually from ops as solo**.
- **Under that policy, 966102 pool coinbase was wrong** (Vel should have been 0 in the split). Site history (no Vel) ≈ intended fair. Non-ops underpaid vs fair+finder ≈ **5,224,538 sats** total (Veganic **+1,288,090**). Vel’s on-chain slice 8.56M; ~3.3M of the redistribution would have been OPS window-weight. Script: `scripts/_diag_vel_policy_adjustments.py`.
- **After delete:** coinbasers on later finds look fine (dust-level); mild drift on #966466/#966522 (`…zgwl9`) — separate from Vel. Deleting shares does **not** rewrite the already-mined 966102 coinbase — top-ups needed if making miners whole.
- **Skip-first-3 owe pass (2026-09-04):** `scripts/_audit_owe_after_skip3.py`. After #961831/#961857/#962180: **most later finds OK~dust** (fair≈chain). Material clean owe = **#966102 Vel-as-solo only (~0.052 BTC non-ops)**. Mid-era #962658–829 huge “under” is mostly **reconstruction illusion** (orphan window rewrite + **Jifa `1D2Q…` CHAIN_ONLY** ~25M on chain / 0 work in today’s DB). **50k share cap** was real pre-2026-09-03 coinbaser (`list_shares_newest(50k)`); replaced by uncapped `list_shares_after_cutoff` + cache — do not treat mid-era site deltas as automatic debt. Post-fix recent blocks match aside from ~0.001 BTC zgwl9 noise.

### 2026-09-05 — NAS resource trim / review

- **Stopped** (restart=no): `bip110-datum-pow` (lab), `bip110-knots-pow` (lab), `bip110-datum-pool-fly-parked`. Freed ~0.4G RAM; host still tight (~2.6G avail / 23G, no swap).
- **Keep critical:** Knots main, CONVOY GW, Prime, Postgres, Web. Web spikes OK; protect Prime+Knots from web load (separate processes already; shared PG is the coupling).
- **TIDES PG (~122MB):** `shares` ~199k/63MB + `share_attempts` ~195k/51MB (nearly 1:1 with shares — every accept/reject logged). Lighten later: retain attempts N days / only rejects+blocks; optional web read-replica or statement timeout on web role. `shared_buffers=128MB` default.
- **Mempool (~0.9G RAM api+db):** already slim-ish (`STATISTICS` off, LN off, CPFP/summaries off, index 50 blocks). Further: `DATABASE.POOL_SIZE` 100→10–20; `MAXMIND` off if unused; `INDEXING_BLOCKS_AMOUNT` lower; confirm no Electrum/Esplora traffic; `PRICE_UPDATES` already 1/hr.

### 2026-09-05 — Multi-worker UI

- Coinbaser `name` = `rig1 · rig2` (+ hover breakdown); `workers[]` with proportional sats.
- Contributors: **+** expands per-worker shares / work / H/s / est payout.
- Miner page: per-worker chart lines + checkboxes; worker table under chart.
- Live `?v=20260905workers`. THEOSOFICA `bc1qy9ms6…` confirmed rig1+rig2 in data.

### 2026-09-04 — Manual finder pay Veganic #967331

- Owed **0.12500709 BTC** (4% of find #967331) to `bc1qk7zr…h7nh3g8l` while fee=0 (no in-coinbase bonus).
- Sent from local **`tides_pool`** wallet: txid **`75df2d8e1e59f794705964a10b379dd16e7fe3ae2ce29165e10e75271788f894`** (fee 822 sats).
- DB: `finder_credits` id=32 marked `paid_in_height` = tip at mark time; meta `manual_finder_pay_967331`.

### 2026-09-04 — Miner Est. next ≠ main coinbaser (fixed)

- Cause: `/api/user` used `_payout_window()` via **`list_shares_newest(50k)`** + proportional `reward_estimate × work/total`, while main “If we find a block now” uses **Prime coinbaser** (uncapped window + template value). Drifted badly once window &gt; 50k shares.
- Fix: `_payout_window` → `list_shares_after_cutoff`; `estimated_next_sats` = that address’s line from `_coinbaser_payload()`.

### 2026-09-04 — Health helper masks gateway IPs (web only)

- **Web** `:8088` `/health` + `/api/health`: mask `checks.gateway_uas` **and** nested `checks.coinbaser.gateway_uas` to **`*.*.*.last`**.
- **Prime** `:8089` / logs: **full IPs** (LAN-only). Mask only when `TIDES_ROLE != prime`.

### 2026-09-04 — Finder bonus off the page (manual ops)

- With fee 0%, site no longer shows ⛏️🏆 / `tides+finder` / “includes finder bonus”. Coinbaser API coerces `tides+finder`→`tides` when `fee_bps==0`. Footnote: bonuses paid manually by ops off-chain. Est. next = tides share only. Cache `?v=20260904nofinder`.

### 2026-09-04 — Fee 0% for current block + UI overlay footgun

- **Live now `TIDES_FEE_BPS=0`** (this open window / “this block”): miners get full template value; **no ops fee line**. Finder bonus not funded from fee — pay from ops manually if desired. `tides+finder` kind may still **label** the pending-finder address’s work line even when bonus sats weren’t added.
- Share rows already `fee_bps=0`; fee is **`miner_bps = 10000 - settings.fee_bps`**.
- **Recreate wipes docker-cp UI.** After any `compose … --force-recreate` of web/prime, re-apply `_tmp_live_contrib` overlay (`api.py`/`models`/`store` + `static/*`) or site falls back to image (`?v=20260904w`, text Kind, no OPERATION FEE). Script: `scripts/_nas_restore_ui_and_fee0.sh` pattern.

### 2026-09-04 — CONVOY pool_address → Private_wallet (ops fee unchanged)

- New Private receive: **`bc1qmtkp7haekhj6g5jw3cltnk6ptaeyrn4haccws4`** (`Private_wallet` / label `tides-ops-private`). Local RC4 Knots.
- Live **`bip110-datum-pool`** CONVOY `mining.pool_address` set to that addr (bak `config.json.bak.private-payout.*`). Script: `scripts/_nas_set_convoy_pool_addr_private.sh`.
- **`TIDES_POOL_OPS_ADDRESS` left `bc1q3l0q76…`** — do not switch ops fee line.
- Plan: `_tmp_private_transfer_plan.txt`. Pure-ops should-have ≈ **4.28 BTC**; miner-share to ops ≈ **51.5 BTC** lifetime.
- **Test send 2026-09-04:** `tides_pool` → Private **2.0 BTC** to fresh addr `bc1q8wfvqpnuzllxut050006lf8rd38wl7yux4vnep` (not mining payout addr). txid `1336eb003b2ceb1eb842d3f57d922da92a1b611df222a7f4a751ccba1aac813b` fee 686 sats. Mining payout stays `bc1qmtkp7…`.
- Verify: site contributors show `bc1qmtkp7…` for new shares; ops ⚙️ line still `bc1q3l0q76…`.

### 2026-09-04 — THEOSOFICA rejects (not DATUM version)

- User `theosofica` hammering Prime: **`share REJECT bad payout address user='theosofica'`** (66 in ~1h). Health `checks.bad_payout.top` + `ua_bad_payout_top`.
- Peer Gateway **`169.155.241.55`**, UA **`v0.4.1-beta/b9ea7dc3…`** (CONVOY — fine). Most rejects have **`coinbase_id=2`** (multi-out OK) → **not** an old-Gateway / reject-27 version issue.
- Cause: stratum username is the bare string `theosofica` (nickname), not a `bc1…` payout address. Classic **Pool Pass Full Users ON** / miner user = nick instead of address. Tell them: Pass Full Users **OFF**, miner user = `bc1…payout` (optional `.worker`), put THEOSOFICA in coinbase secondary / nickname path.

### 2026-09-04 — Contributors Luck % (hidden)

- **Total work** = payout window only (7 confirmed + current), **not** lifetime.
- Naive `N × current_diff / window_work` is wrong across difficulty changes / uneven intervals.
- **Proper pool luck (doable with current DB):** for each confirmed find, `work_interval = Σ share.work` with `prev_share_head < seq ≤ this.share_head_seq`, then `luck% = 100 × Σ block.difficulty / Σ work_interval`. We store per-block `difficulty` + `share_head_seq`; shares retained from seq 1 (full coverage for recent windows). Spot-check last 8 finds: proper ≈ **73.6%** vs naive ~115%. Per-interval luck ranged ~33%–508%. Diff did jump (≈17.8M → 71.3M around #966083).
- Per-addr finder luck still misleading in a short window. **UI hidden** (`?v=20260904noluck`).

### 2026-09-04 — Coinbaser Kind → icons

- “If we find a block now” Kind column: **⛏️** = tides work share; **⛏️🏆** = tides+finder (hover shows finder bonus BTC/sats); **⚙️** = ops. Cache `?v=20260904kind`. Web-only deploy.

### 2026-09-04 — UI clip long worker / nickname cells

- Long coinbase nicknames (e.g. `Paranoid Crypto Anarchist 0xfryps`, 33 chars) and workers that are full addresses blew out coinbaser / blocks / contributors tables.
- Fix: `clipCell()` + CSS `.clip-text` (ellipsis on inner span — **td max-width alone is ignored** under `table-layout:auto` for unbroken strings). Full value on `title=` hover.
- Applied on: coinbaser worker+nick, blocks finder worker+nick, contributors nick, address-page shares worker.
- Cache-bust `?v=20260904clip`. Deploy web-only: `scripts/_deploy_tides_clip_cells.sh` → Alexandria + `deploy-tides-web-1` (Prime untouched). Working copy: `_tmp_live_contrib/`.

## Template for new entries

```markdown
### YYYY-MM-DD — short title
- What we learned
- Commands / paths that worked
- What not to do again
```

---

## 2026-09-05 � Security review (web/API vs RPC/keys) � review only

**Question:** Can a website/API caller reach Knots RPC, read key files, or steal BTC?

### Direct HTTP surface (answer: no steal via RPC proxy)

- No `/api/rpc`, wallet, or send proxy. App RPC helpers only call: `getblockchaininfo`, `getmininginfo`, `getblock*`, `getblocktemplate`, `getnetworkhashps`.
- `pool_keys.json` / `config.json` under `/app/data` are **not** HTTP-served (`/static/../data/...` ? 404). `/api/info` exposes **pool_pubkey only** (DATUM), not sk / RPC password.
- Live data survived accidental `POST /api/admin/clear-lab?confirm=YES`: PG `clear_lab_data` deletes shares then `DELETE FROM users` ? FK violation ? **transaction rollback**. Do not "fix" that bug into a working wipe on live.

### Unauthenticated holes (integrity / ops, not direct wallet drain)

- Public (no auth): `POST /api/admin/clear-lab`, `/api/admin/resync-chain`, `/api/lab/share`, `/api/lab/block` on **web :8088 and prime :8089**. `/docs` + openapi list them.
- `/api/lab/share` accepts fake work (demonstrated; cleaned test row). Economic risk to coinbaser attribution if abused at scale.

### Where real BTC risk lives (not the public HTML forms)

- Knots `bip110-knots-main` RPC `datum` user can `sendtoaddress` and `listdescriptors(private=true)` on wallets **`datum_ops`** (~3.13 BTC) and **`tides_pool`** (~50.8 BTC trusted + immature). `dumpprivkey` blocked (descriptor wallets) but private descriptor export works.
- TIDES app code never calls those spend/export methods � but **web+prime env** have `TIDES_BITCOIN_RPC_*`, and shared volume `deploy/datum-pool/config.json` holds `bitcoind.rpcpassword`. Container compromise / volume read ? drain.
- `pool_keys.json` holds DATUM `ed25519_sk` + `x25519_sk` (not Bitcoin keys); theft ? pool impersonation / hostile coinbaser to Gateways.
- Host `INPUT` policy ACCEPT; `8332` published `0.0.0.0` on NAS � rely on **router not forwarding 8332** (docs: LAN-only). Confirm WAN ACL separately.

### Containers

- `deploy-tides-web-1` (:8088), `deploy-tides-prime-1` (:28916 + :8089). Shared mount `/mnt/Alexandria/local/tides-pool/deploy/datum-pool` ? `/app/data`.

### 2026-09-05 � Manual finder bonuses #967879 / #967892

- Fee=0 window: paid **4%** of each find from `tides_pool` (separate txs), same as Veganic #967331.
- **#967879 Bubble** `bc1qj6jfa�hk48j` **0.12502167 BTC** txid `a43bdc6b34aa4f4c38dd02cd4b3294e3765ae65619b294f36b6fbb92ff0499fa`
- **#967892 mhr6091** `bc1qvnhsh�mdtf9` **0.12502253 BTC** txid `e218b67fc2af111fca5aa3b0e3ff7547e2d9672d480a6375bdd2681a900a3c7d`
- DB: `finder_credits` id 33/34 `credit_sats` set + `paid_in_height=from_height`; meta `manual_finder_pay_967879` / `_967892`.

### 2026-09-05 � Lab/admin HTTP hard-disabled on live

- Default deny: `_require_lab_http()` on `/api/admin/clear-lab`, `/api/admin/resync-chain`, `/api/lab/share`, `/api/lab/block` (and `/lab/*`).
- Re-enable only with env `TIDES_ALLOW_LAB_HTTP=1` (lab). Live compose does **not** set it.
- `/docs`, `/redoc`, `/openapi.json` off unless that flag is set. `/api/info` ? `lab_http_enabled: false`, `docs: null`.
- Deploy note: web was ahead of prime on `models.py` (`WorkerBreak`); syncing web `models.py`+`store.py` into prime was required after copying newer `api.py`. Backup: `api.py.bak.nolab.*`.

### 2026-09-05 � NAS Knots wallets unloaded (files kept)

- Unloaded `datum_ops` + `tides_pool` on `bip110-knots-main` with `load_on_startup=false`.
- `listwallets` ? `[]`; `settings.json` `wallet: []` (bak `settings.json.bak.pre_unload.*`).
- `wallet.dat` dirs still under `/mnt/Alexandria/local/bip110-lab/knots-main-data/{tides_pool,datum_ops}/`.
- Spend path gone (`-18` wallet not loaded). Chain/GBT OK; TIDES health ok, gateways up.
- Full copies assumed on Windows `O:` � delete NAS wallet dirs only later if desired. Reload: `loadwallet tides_pool` / `datum_ops`.

### 2026-09-05 � allow_submitblock + CONVOY (our miner GW)

- DATUM design: **finder Gateway** submits via `submitblock` to **its** Knots; Prime never submits. External finds (e.g. Bubble/mhr6091) use **their** GWs.
- MaVeTh/pow flag `mining.allow_submitblock`: `false` = do **not** call bitcoind submitblock (`autoswitch gate`); shares may still accept locally � **block not broadcast from that GW**.
- Stock Ocean has no this flag (always submits). Our fork default is `true`.
- **NAS pool/CONVOY GW was `false`** (cutover leftover). Fine only if that GW has **no** real hash. **We mine on CONVOY like a normal miner ? should be `true`** or a local find can be lost.
- Next ops: set CONVOY/live `datum-pool` `allow_submitblock: true` + restart that GW when ready (not done yet this note).
- Prime security follow-ups (review only, do soon): share PoW verify; finder `is_block` freeze; confirm coinbase vs assigned split. See session 2026-09-05 scan.

### 2026-09-05 � Did false allow_submitblock eat our finds?

- Live GW `bip110-datum-pool` (image `datum_gateway:convoy`) mounts `datum-pool-convoy/config.json` with `allow_submitblock: false` + Private `bc1qmtkp7�`.
- **No** log lines `NOT submitting` in `datum-pool-logs/` (gate never fired a skip).
- Last **Maveth.ASIC** `is_block`: **2026-09-02 06:23** ? height **965224**, confirmed on-chain + saved submit JSON. Earlier Sep 1�2 Maveth finds also confirmed/submitted.
- Since then: Maveth still hashing (Private addr ~11k shares today) but **no** `is_block` � dry spell, not discarded finds.
- Pool still finding via others (e.g. 967879/967892 Bubble/mhr6091).
- Still flip CONVOY to `allow_submitblock: true` for production mining (risk going forward, not proven past loss).

### 2026-09-05 � allow_submitblock history (pre-CONVOY peace of mind)

- Config on disk: `true` through ~Aug 26 solo/TN4 era; flipped to `false` around **2026-08-29 go-live** (`bak.golive.*`, `bak-unknown`); stayed `false` through Fly pool + `bak-pre-convoy-cutover` + CONVOY (inherited, not a CONVOY-only change).
- `bak-nosubmit-20260829-214802` still has `true` (snapshot *before* the nosubmit/golive flip).
- Despite `false` in JSON from go-live onward, gateway logs show **FOUND == SUBMITTING** every day we found (Aug 25�26, 31, Sep 1�2) and **NOT submitting = 0** forever. Sep 1�2 Maveth finds confirmed on-chain.
- Interpretation: either the then-running binary ignored/didn't enforce the flag, or process hadn't reloaded config � **past finds were submitted**. Current CONVOY image *does* enforce the gate ? set `true` for live mining.

### 2026-09-05 � CONVOY allow_submitblock flipped true

- `datum-pool-convoy/config.json`: `allow_submitblock False -> True` (bak `*.bak.allow_submitblock_true.20260905T125301Z`).
- Restarted `bip110-datum-pool`; Prime health ok, shares on Private addr resumed.

### 2026-09-05 � Prime security scan triage (lab vs prod)

**Lab-scoped (park / already handled):**
- HTTP lab/admin: gated; live `TIDES_ALLOW_LAB_HTTP` unset ? 403. Keep off.
- `datum_prime` comment `not verifying client session sig for lab` � channel still NaCl-boxed; sig verify is hygiene, not the share-farm path.
- Config defaults in `config.py` are lab-shaped; live compose overrides.

**Prod integrity (do soon, review?fix):**
1. No share PoW check � work from `target_byte`.
2. `is_block` / pending finder overwrite races.
3. Confirm path: TIDES+ops enough; no match to assigned coinbaser split (partial reject-27 only). See `docs/TIDES_COINBASE_ENFORCE.md`.

### 2026-09-05 � Share PoT/work caps (Prime integrity #1)

**Balance (grows with pool):**
- **Every share (cheap):** `target_byte` must be in `[min_pot(min_share_diff), share_target_byte_max]` (blocks up to `share_target_byte_max_block`); credit clamped by `share_work_ceiling()`. Stops `2^40` work farming.
- **Sampled (`TIDES_POW_CHECK_EVERY`, default 16):** ntime skew check; **always** on `is_block`.
- Full blake2b header verify = later (needs TLV merkle/coinbase often absent).

**Live:** deployed `config.py` + `datum_prime.py`; `min_tb=11` (diff 2048), max 28, every 16. Prime up, gw ~18.
**Env knobs:** `TIDES_SHARE_TARGET_BYTE_MAX`, `TIDES_SHARE_TARGET_BYTE_MAX_BLOCK`, `TIDES_SHARE_WORK_MAX`, `TIDES_POW_CHECK_EVERY`, `TIDES_SHARE_NTIME_MAX_SKEW_SEC`.

### 2026-09-05 � Finder freeze + confirm-time intended vs chain

- Pending: first `finder_address` wins (SQL + memory); one `finder_credits` row per `from_height`.
- Find: when tip coinbase resolves, snapshot `intended_payout_json` for **all** modes (before opening this find's credit).
- Confirm: `apply_confirm_payout_checks` refreshes reward, ensures snapshot, `diff_payments` vs chain (dust 1000 sats); material mismatch ? `ops_manual` + note.
- Live: needs `payment_verify.py` on prime image too. Deployed; prime healthy.

### 2026-09-05 � Coinbase_id must be known (integrity #3 remainder)

- After multi-out assign: share `coinbase_id` must be in session `recent_coinbasers` (ring 24); else reject-27 `unknown coinbase_id`.
- Still rejects `id==0` / subsidy-only. Confirm-time intended?chain already shipped.
- Live deployed; Prime up.

### 2026-09-05 � DATUM cmd_len cap (DoS)

- Protocol allows ~4MiB `cmd_len`; Prime now drops sessions if `cmd_len > TIDES_DATUM_MAX_CMD_LEN` (default **262144**) before `readexactly`.
- Empty (0) still allowed for ping-style frames. Live deployed.

### 2026-09-05 � Prime security: done vs later

**Done (code + live + GH):** lab/admin HTTP gate; share PoT/work caps + sampled ntime; finder freeze + confirm intended?chain; known `coinbase_id` for multi-out; `cmd_len` cap 256KiB.

**Ops (not GH):** NAS wallets unloaded; CONVOY `allow_submitblock=true`.

**Later (do not start now):**
1. Verify client session signatures (drop `not verifying� for lab`).
2. Full blake2b share PoW when TLV merkle/coinbase present (sample).
3. Optional quarantine on repeated BAD_TARGET (like r27).
4. XSS: escape quarantine reason / nickname sinks in `app.js` / SSR.
5. Health recon: mask prime `:8089` gateway IPs; trim public `/api/info`.
6. Harden `config.py` defaults (fail closed if lab creds on main).
7. Cap concurrent DATUM sessions / per-IP connect rate (DoS beyond cmd_len).

### 2026-09-05 � Reverted unknown coinbase_id enforce

- `unknown coinbase_id` reject-27 false-quarantined real miners (sclite, BoxII/Veganic, THEOSOFICA, etc.) � session ring did not see their ids.
- Reverted to empty/subsidy-only check only; cleared today's `reject-27%` quarantines (4). Older Qs left.
- Note: `bad ntime` rejects also spiked (~119/3h) from sampled ntime check � watch; relax/disable if needed.

### 2026-09-05 � Disabled share ntime check

- `TIDES_SHARE_NTIME_CHECK` default **false** (was rejecting real shares as `bad ntime`). Re-enable only for experiments.

### 2026-09-05 � bc1qtest junk share / address guard

- One shares row ddress=bc1qtest work=1 (no worker/pow_hash) � classic **/api/lab/share** inject (default work=1), not DATUM. Lab HTTP is 403 now; share was from when lab was open or a one-shot.
- Purged: DELETE FROM shares/users WHERE address='bc1qtest'.
- Hardening: looks_like_payout_address (prefix+len=26) before checksum; **full** is_valid_payout_address stays on every real share (cheap). Naive `bc1*` alone is wrong (c1qtest matches).
- Lab inject now rejects invalid addresses even when TIDES_ALLOW_LAB_HTTP=1.
- Contributors API filters invalid addresses. Prime records share_attempts on bad-payout rejects.

### 2026-09-05 � Dashboard API TTL cache (web CPU)

- Soft UI refresh already 60s. Added process-local `_TtlCache` on web for:
  - `/api/stats` TTL **15s**
  - `/api/contributors` TTL **20s** (keyed by limit)
  - `/api/charts/pool` TTL **30s** (keyed by range)
- Single-flight lock per key; `Cache-Control: public, max-age=�` on responses.
- Observed: stats cold ~3.3s ? warm ~1ms; charts/contrib warm ~1ms.
- ZFS RAM ~95% is normal (ARC); web CPU was the real pressure from multi-tab polls.

### 2026-09-05 � Dash cache stale-while-revalidate + warmup

- `_TtlCache` now returns **last refresh** immediately when TTL expired, and refreshes in background (single-flight).
- Web lifespan warms stats / contrib:50 / charts:24h so first paint after restart isn't a multi-second blank.

### 2026-09-05 � bip110-datum-pool healthcheck false alarm

- `unhealthy` was **not** Knots: image HEALTHCHECK probed `localhost:23334`.
- CONVOY pool GW listens **stratum 23336** + **API 7155** (host network), Knots RPC `127.0.0.1:8332` = `bip110-knots-main` (main tip).
- Recreated container with `--health-cmd='nc -z 127.0.0.1 23336 && nc -z 127.0.0.1 7155'` ? **healthy**.
- Cutover script updated; see `deploy/datum-pool-convoy/HEALTHCHECK.md` on NAS.

### 2026-09-05 � Nick-from-POW restored on Prime

- Earlier staged nick-from-POW (`*.bak.nick-pow.*` / abw-stage) was lost across later Prime deploys.
- Restored: on throttled check (same as reject-27 auto-Q), if `users.last_nickname` blank and POW has section `0x02`, parse secondary after primary ? `set_address_nickname`.
- **Never rejects** a share for missing/invalid nick � id only.
- Credits: **iohzard** (prefetch method), **Taki** (call-out).
- Proven live: `nickname learned � bc1qz5kv� nick=hallais` within ~90s of Prime restart.

### 2026-09-05 — Quiet ABW dual-port on current Prime (staged, not activated)

- **Model:** one Prime process + **same Postgres**. Public `:28916` unchanged (CONVOY → v3-abw-off; others → v1). Quiet `:28926` → CONVOY v3-abw-on + ACTIVE `0xA8` + optional rotate.
- **Why not separate DB:** accepted shares on either port hit the same `append_share` / coinbaser path. ABW is wire-only; credit is identical once accepted.
- **Code:** `abw_prime_port` + sockname `listen_port` gate; lab `abw_enable` remains single-port override when `abw_prime_port=0`. Files: `datum_prime.py` / `config.py` / `api.py` / `abw.py`.
- **Staged on NAS (no restart yet):** Alexandria + `docker cp` into `deploy-tides-prime-1`; compose has `TIDES_ABW_PRIME_PORT=28926`, publish `28926:28926`, `TIDES_ABW_ENABLE=0`. Backups `*.bak.abwdual.*`.
- **Activate (needs Prime recreate):** `cd /mnt/Alexandria/local/tides-pool/deploy && docker compose -f docker-compose.yml -f docker-compose.split.yml up -d tides-prime`
- **Downtime:** web `:8088` untouched; DB untouched; ~19 GW TCP sessions drop and reconnect (typically seconds–~1 min). No zero-downtime path (Python import + new bind). Do **not** advertise `28926` on the site.
- **Not activated** until explicit go — live still serving old in-memory code on `:28916` only.


### 2026-09-05 — How-to-connect: Leo CONVOY StartOS `pow_0.4.1_20`

- Leo/Retropex release: https://github.com/Retropex/datum-gateway-startos/releases/tag/pow_0.4.1_20 (pre-release, \"Switch to DATUM Gateway from CONVOY\").
- Live How-to-connect lists it under **Experimental — Leo CONVOY StartOS package (please test)** + ask users to report working/errors **in the channel**.
- Preferred production still Leo `pow_0.4.1_18`. MaVeTh `_20` + CONVOY-from-source remain experimental too.
- Deploy: web-only restart `deploy-tides-web-1`; cache-bust `?v=20260905convoyleo`. Prime untouched (19 sessions stayed).

### 2026-09-06 — Health degraded dig (coinbaser p99)

- Badge **degraded** when `p99_reply_ms >= 2000` (last 100 coinbaser reply latencies in `CoinbaserCache._latency_ring`).
- **Cause class:** cache refresh spikes (logs showed **3.3s** / **2.2s** refreshes over ~176k window shares) + heavy concurrent `coinbaser value=` traffic + gateway churn (`172.16.13.3` unknown UA flapping; external reconnects). Typical `last_reply_ms` stays ~100–200ms; p99 is the tail.
- `reject27_by_ua` is a **warning only** (does not alone set degraded).
- Cleared on its own once slow samples aged out of the 100-ring (status back **ok**, p99 ~1993). Not a DB/RPC outage.

### 2026-09-06 — hallais find #968420 (manual 4% bonus paid)

- Finder `bc1qz5kvgqtfwsg3gql0ntlduw48r352amenwpm3gm` nick **hallais** (2nd promo find; also #968134).
- Reward `312545582` sats → **0.12501823 BTC** (`12501823` sats).
- Paid from Windows `tides_pool`: txid `5c6b45b7695c5ef8471bd09feb908da7df78cb1303953f668c8b467dd2bb96f4`.
- DB: `finder_credits` id **38** `credit_sats` + `paid_in_height=968420`; meta `manual_finder_pay_968420`.
- Banner **6 / 8** (`?v=20260906bonus6`). Web-only restart; Prime stayed up.
- Receipt: `payouts/968420_hallais_finder/payment.txt`.
- **False `ops_manual` again** (confirm-time coinbaser lag / dust deltas >1000 sats, zero-sum reshuffle). Cleared same day: `intended_payout_json` refreshed from chain (36 outs), `payout_mode=onchain_split`, `manual_payout_done=true`. Script: `scripts/_fix_968420_onchain_split.py`. Payment-history **amounts** still come from work-replay (may need workadj like 968112 if those must match chain %).

### 2026-09-06 — Confirm: `needs_review` (NO cross-user absorb)

- **Policy (CRITICAL):** same-user dust lag (per-addr ≤ dust_ignore) = OK. **Cross-user** value move (A unpaid / LISTED_ONLY, B or ops gains) is **not** lag — ops must **review + top up** unpaid.
- `classify_intended_vs_chain`: `ok` | **`mismatch`** only (no auto “drift absorb”). mismatch → `needs_review` badge. Ops-only coinbase still `ops_manual`.
- **Still needs Prime restart** to take effect (staged on disk).
- Files staged: `payment_verify.py`, `block_confirm.py`, (+ earlier UI `needs_review` badge).

### 2026-09-06 — #968456 Skeleton: LISTED_ONLY → ops top-up

- Finder `bc1q35wd3…` **Skeleton**. Intended 32 outs vs chain 17; totals matched; **~0.0665 BTC** (6,650,129 sats) for **15** snapshot payees never on coinbase → folded into **ops**.
- **Paid** from Windows `tides_pool`: 14 exact sendtoaddress + 1 dust-floor 5460 sats for `bc1qmk644…` (owed 1264; Core rejected as too small). Receipt: `payouts/968456_ops_topup_listed_only/`.
- DB: `manual_payout_done=true`; meta `ops_topup_968456_listed_only`.

### 2026-09-06 — Contributor cb-type icons + eras % fix

- **Hourly job** `/mnt/Alexandria/local/tides-pool/scripts/_hourly_cb_type_status.sh` (cron `:05`) writes meta `cb_type_status_v1` from accepted-share `cb_id`. **OK is sticky until a type-2 share**; type-2 → warn. Version allow/deny list stub in meta `policy`.
- Contributors UI: ✓ / ⚠ / ? next to address (`cbTypeBadge`), tooltip explains truncate risk. Cache `?v=20260906cbtype2`.
- **Blocks % was always 0:** `contributor_rows` never set `window_eras` / `eras_with_work` / `eras_with_work_pct`. Fixed in `store.py` (era id from confirmed heads + optional `cutoff_seq`).

### 2026-09-06 — CONVOY PR #10 deployed on NAS pool GW

- Built `datum_gateway:convoy-pr10` from local `b9ea7dc` + [CONVOYMining/datum_gateway#10](https://github.com/CONVOYMining/datum_gateway/pull/10).
- **Clean rebuild** (`--no-cache`) after first image wrongly advertised `eb14f71` (docker cache / bad buildinfo). Now banner **`git commit: 3e252be3…`** tags `convoy-pr10-clean` / `convoy-pr10-3e252be`.
- Cut over `bip110-datum-pool` (host net; config `/mnt/Alexandria/bitcoin/datum-convoy/config.json`). Old image kept as `datum_gateway:convoy-b9ea7dc`.
- **`peer=172.16.13.1` is NOT home LAN** — Docker bridge gateway for `deploy_default` (Prime=`172.16.13.2`). Host-net DATUM→`:28916` often SNATs as that IP.
- **Rejects (~28% pool-wide, 2h):** almost all r27 type-0. **Not caused by web/Prime split** (shares are Prime-only). Drivers: Gateway reconnect churn + **slow coinbaser refresh** (avg ~1.5s, p95 ~3.4s, max ~6.8s) → empty-job race. Top reject peer was NAS GW while it flapped/`eb14f71` UA.
- Rollback: recreate with `datum_gateway:convoy-b9ea7dc`. Remotes still need their own PR10 for type-2 truncate.

### 2026-09-06 — RCA: why #968456 coinbase mismatched (DATUM type-2 truncate)

**Not** a Prime coinbaser bug and **not** “never happened before.”

- **Who / what DATUM:** Live CONVOY gateway `bip110-datum-pool` image `datum_gateway:convoy-b9ea7dc`, config `/mnt/Alexandria/bitcoin/datum-convoy/config.json`, stratum **:28916** (ABW :28926). Finder share: Skeleton worker **`1`**, accepted `is_block=true` at **16:43:46Z**, tag `TIDES.Skeleton`. UA on peer rejects same era: `v0.4.1-beta/b9ea7dc…`.
- **Smoking gun:** Prime log `share OK user=bc1q35wd3….1 … cb_id=2` (winning nonce `6f24885b`). DATUM **coinbase type 2** = older Antminer-sized template (**~755B** body). On-chain coinbase tx **776 bytes**, **17 unique payees**, **two OPS outs** (split + leftover).
- **Mechanism:** Prime assigned **32** outs (healthy). Gateway builds size classes 0–5; ASIC only mined **type 2**, so `subtypebysize` fitted the first ~17 Prime outs then put remainder on Gateway `pool_address`. Misses are a **contiguous tail** of the sorted split (idx 17–31) — classic size-class truncate, not reshuffle.
- **pool_address note:** Convoy config still has `mining.pool_address=bc1qmtkp7…` (Maveth). **Old ops and Maveth were the same address before** the ops split — leftover landing on today’s ops addr (`bc1q3l0q76…`) is consistent with Prime’s split including ops + historical identity; not a separate misconfig cause of the truncate.
- **Same-day twin:** **#968159 THEOSOFICA** — chain also **17 unique / 778B / ops_n=2**. Was misread as “coinbaser lag” and absorbed via `onchain_split` (no LISTED_ONLY top-up). Snowman #968112 / hallais #968134+#968420 mined **larger** types (1171–1379B, ops_n=1, full 30–36 payees).
- **Why it feels new:** Window now regularly has **~30–36** payees (Prime `outs=31` steady). Type-2 finders **cannot fit** that; earlier finds either had fewer payees or were found by type-5+ hardware. As long as some hashrate is on type-2 boxes (Goldshell HS / older Antminer dialect), the next type-2 find will truncate again.
- **Ops takeaway:** Fingerprint truncate = `ops_n>=2` + `cb_bytes≈770–800` + contiguous LISTED_ONLY tail. Prefer **ops top-up** (968456) over absorbing truncated chain into `onchain_split` (968159). Longer-term: prefer/require type-5+ on finders, or Prime/Gateway policy so type-2 jobs are not stratum-published when outs≫17.

### 2026-09-06 — Skeleton finder bonus #968456 (manual 4%)

- Reward `312511881` → **0.12500475 BTC** (`12500475` sats).
- Paid `tides_pool`: txid `88e857423dd730c6bb7807f12b8444ddd6891d865f5f5e951350e8f9c3fe6023`.
- DB: `finder_credits` id **39** paid; meta `manual_finder_pay_968456`.
- Banner **7 / 8** (`?v=20260906bonus7`). Web-only restart. Receipt: `payouts/968456_skeleton_finder/`.

### 2026-09-06 — THEOSOFICA find #968159 (manual 4% bonus owed)

- Finder `bc1qy9ms6fd0fht9g624ap42wln7ut56pcrrksxhvt` nick **THEOSOFICA**. Coinbase `TIDES\\x0fTHEOSOFICA`.
- **Confirm mismatch:** was filed as coinbaser lag; **reclassified 2026-09-06 RCA** as same **DATUM type-2 truncate** as #968456 (chain **17 unique / 778B / ops_n=2**; THEOSOFICA shares are mostly `cb_id=2`). Totals matched `reward_sats=312531465`.
- **Fixed 2026-09-06 (partial, later corrected):** `intended_payout_json` refreshed from chain (17 merged addrs); `payout_mode=onchain_split` — **incorrect absorb** of type-2 truncate (no LISTED_ONLY top-up).
- **Paid finder:** `finder_credits` id **37** `credit_sats=12501258`; txid `7ae31cbcbc053f9e38bdc69fa3ef3737f7c7dc244850471dd9b4e9c6cd53bfd4` from Windows `tides_pool`. Receipt `payouts/968159_theosofica_finder/`.
- Banner **5 / 8** (`?v=20260906bonus5paid`). Optional later: share **workadj** like 968112 if Payment-history reconstruction must match chain %s.
- **Mempool “68”:** was RIPTIDE + merged THEOSOFICA solo heights. Trimmed to site-confirmed overlap → **riptide≈33**, theosofica legacy≈35. Site confirmed finds **36**.

### 2026-09-06 — #968159 LISTED_ONLY ops top-up (correct the absorb)

- Work snap **31 outs** vs chain **17**; contiguous tail miss **14** addrs / **13,222,096 sats (0.13222096 BTC)** held in ops leftover.
- **Paid** single `sendmany` from Windows `tides_pool`: txid `ce6f922111acdc1c633a3154e31531c346f30c9839fc95c364fd67e66cbfe205`.
- DB: `payout_mode=ops_manual`; meta `manual_adjustment_968159` + `ops_topup_968159_listed_only`. Receipt: `payouts/968159_ops_topup_listed_only/`.
- UI: same click-expand “manual adjustment” table as #968456.

### 2026-09-06 — hallais finder bonus #968134 (manual 4%)

- Finder `bc1qz5kv…pm3gm` nick **hallais** (new finder). Same intended≠chain zero-sum drift; chain multi-out OK (`ops_manual` → cleared after pay).
- Paid **0.12500972 BTC** (4% of 312524316) from Windows `tides_pool`.
- txid `1c0651364c78c74b9e46993687305e27aacb76bbcabb7611120a8bc932d97f23`
- DB: `finder_credits` id **36**; banner **4 / 8**. Receipt: `payouts/968134_hallais_finder/`.
- **Banner footgun:** bumping claimed to 4 left **6 open dots** (10 total) while label said 4/8. Fixed to **4 claimed + 4 open**. Cache `?v=20260906bonus4fix`. When editing spots, claimed+open must equal **8**.

### 2026-09-06 — Snowman finder bonus #968112 (manual 4%)

- Paid **0.12503639 BTC** (`12503639` sats = 4% of `312590986`) from Windows `tides_pool` wallet.
- txid `756ec7214d18cc044e5fc74d8cc213a8b6d760de3adb518fc1fa719cdb3ea851`
- DB: `finder_credits` id **35** `credit_sats` + `paid_in_height=968112`. Banner **3 / 8 claimed** (`?v=20260906bonus3`).
- Receipt: `payouts/968112_snowman_finder/payment.txt`. Windows RPC: `bitcoin-cli -datadir=O:\Users\memys\AppData\Roaming\Bitcoin` (cookie via datadir; not raw `-rpcuser` alone).

### 2026-09-06 — Block 968112 workadj (chain proportions)

- Confirm had `coinbase≠intended` (zero-sum ~99.8k sat reshuffle; totals matched). Mode briefly `ops_manual`.
- Phase 0/1: scale **closed window** work (`seq` 112434–290018) so per-addr % = **on-chain** %; carry `ops_adj_968112_carry` into current for under-weighted-vs-DB side (sum carry **645731** work ≈ 0.032% of window).
- Backup: `shares_bak_968112_workadj`. Intended snap refreshed from chain; `payout_mode=onchain_split`, `manual_payout_done=true`.
- Caveat: some over-weighted addrs lacked enough **current** shares to fully debit → closed still scaled; lifetime work slightly up for those. Scripts: `_nas_phase0_968112_work_vs_chain_v2.py`, `_nas_phase1_968112_apply_work_adj.py`.

### 2026-09-06 — ani-postgres ≠ pool DB

- **`ani-postgres`** is a **separate subsystem** (old recovery doc / non-TIDES). Do **not** confuse with **`deploy-postgres-1`** (TIDES pool DB on `:5433`).
- Pool agents: leave `ani-postgres` to the operator unless explicitly asked; starting/stopping it is unrelated to Prime/shares.
- **Live datadir:** `/mnt/Alexandria/randy/pg18_data` (PG17, ~559M). Image **`pgvector/pgvector:pg17`** (needs `vector`; AGE unused). Unit: `docker-ani-postgres.service`.
- **Do not** mount `/mnt/Alexandria/ani-postgres-data` (old ~152M copy, logs end 2025-10-17). Ani app files live under `/mnt/Alexandria/Ani` (logs/wiki).

### 2026-09-06 — Site sluggish; blocks TTL + kill stray pull

- Not caused by mempool NPM redirect or static cache-bust. Host load high post-reboot; leftover **`unpigz`** (dockerd child from aborted mempool image pull) still decompressing — killed.
- Soft refresh **60s** OK; dash TTLs already covered stats/contrib/charts. **`/api/blocks` was uncached** (~560ms) and polled every refresh (`limit=8`) → sticky UI.
- Live web-only: `_BLOCKS_TTL_SEC=20` via existing `_TtlCache` (stale-while-revalidate); warm `blocks:8` on startup. Cold ~0.14s → warm ~1–2ms. Web CPU dropped (~28% → idle). Scripts: `_nas_deploy_blocks_ttl_cache.sh`; bak `api.py.bak.blocks-ttl.*`. Prime untouched. NPM Asset Caching still **off** on tides host 21.

### 2026-09-06 — WHY hard reboot leaves Docker/Apps (and pool) down

Read-only dig after boot **2026-09-05 16:41** (scripts `_nas_why_docker_down_*.sh`). **Do not** confuse with taking the pool down — dig was query-only; pool was healthy when checked.

**Root causes (stacked):**

1. **`Live Restore Enabled: false`** — `/etc/docker/daemon.json` has no `live-restore`. Hard kill / dockerd stop = containers exit. TrueNAS manages this file; do not casually flip without understanding Apps interaction.
2. **Shared Docker root** — `data-root=/mnt/.ix-apps/docker` on ZFS `Apps/ix-apps`. Pool compose containers + TrueNAS Apps share one engine/overlay. Apps recovery (Unset/Choose Pool, overlay wipe) hits **pool images too**.
3. **Unclean shutdown → overlay inconsistency** — minutes after boot, dockerd logged `layer does not exist` for `bitcoinknots/bitcoin`, `jc21/nginx-proxy-manager`, `postgres:16-alpine`. Classic overlay2 metadata vs layers mismatch after crash.
4. **Docker bounced during recovery** — systemd stopped/started docker ~16:46 and ~16:53 (recovery oneshots). Graceful stop marks containers **`hasBeenManuallyStopped=true`** → `unless-stopped` **will not** auto-restart them (`ShouldRestart failed … restart canceled`).
5. **TrueNAS Apps are middleware-owned** — `midclt` `docker.config` pool=`Apps`, dataset=`Apps/ix-apps`. Media Apps stay STOPPED until UI/`midclt call app.start` (or manual `docker start`). Soft pool autorecover intentionally does **not** start Sonarr/Plex/etc.
6. **Soft autorecover was installed after this boot** — unit files stamped **20:58** local; boot was **16:41**. `WantedBy=multi-user.target`, enabled, but **no journal this boot** / no `/var/log/bip110-pool-autorecover.log`. Next clean reboot should fire it (15s sleep after docker). Nuclear overlay wipe stays **manual only**.

**Pool start times this boot (UTC):** postgres ~22:54, knots ~22:58, prime ~23:01, CONVOY ~00:27, NPM ~01:10, web ~02:53 — staggered manual/recovery, not instant auto.

**Hard rules from dig:**
- Never auto-run overlay2 wipe / Unset Pool as part of boot recover — it deletes shared image layers (Blake/pool).
- Soft path: `bip110-pool-autorecover.service` → `/mnt/Alexandria/local/bip110-lab/ops/bip110-pool-autorecover.sh` (postgres → knots → CONVOY → prime/web → optional NPM/ani). Fail-fast if critical images missing.
- Residual: `bitcoinknots/bitcoin:latest` inspect still broken; live knots uses `bip110-knots-pow:final` (OK).

**Future hardening (plan only until operator says go):** see **[`docs/POOL_POST_REBOOT_HARDENING_PLAN.md`](POOL_POST_REBOOT_HARDENING_PLAN.md)** (saved 2026-09-06, not for today). Phase A = soft autorecover retries/layer probe; Phase B = ZFS snaps + `docker save` image bak + ~20min tides `pg_dump`; nuclear wipe stays manual; live-restore / separate docker root deferred.

### 2026-09-06 — Mempool RIPTIDE pool + miner_(RIPTIDE) labels

- **Model:** pie/ranking = one pool **RIPTIDE**; block strip = **`Nick_(RIPTIDE)`** from coinbase secondary (was `Nick (TIDES)` overlay only).
- **DB:** merged tides/maveth family → `pools.slug=riptide` (id 555), **35** blocks retargeted; old rows kept as `(legacy)` with empty regexes. SQL: `scripts/_nas_mempool_riptide_pools.sql`.
- **customize.js:** `formatLabel` → `secondary_(RIPTIDE)`; mining `/pools/*` merge + pie floor **≥30 blocks** or RIPTIDE always ≥**0.55%** (desktop Other cutoff is 0.5%).
- Kilombino auto pool update may recreate tides-* rows — regexes emptied on legacy; watch after updater runs.

### 2026-09-06 — Local mempool thin chrome (main + mining only)

- LAN `:4080` `customize.js` (`/mnt/Alexandria/local/bip110-lab/mempool-tn4/custom/customize.js`, mirrors under `deploy/truenas-scale/mempool-tn4*/`).
- **Keep:** home + mining nav. **Hide/remove:** docs/API, about/info, graphs, lightning, enterprise, faucet; **strip `app-global-footer`** (API + FAQ/howto + language/theme selectors lived there).
- Junk routes (`/docs`, `/about`, `/graphs`, …) hard-redirect to `/`. Backend API container unchanged (UI still needs it).
- Deploy footgun: `install`/`cp` replacing the bind-mounted file creates a **new inode** — container keeps the old file until `docker restart bip110-mempool-web` or in-place `cat > file`. Prefer in-place overwrite.
- Verify: hard-refresh `http://192.168.0.143:4080/` (customize nginx `max-age=300`).


### 2026-09-06 � SC Lite temp/fan manager after reboot

- Code+secrets live at `/mnt/Alexandria/local/bip110-lab/sclite-temp-manager/` (miner `192.168.0.202`).
- **Durable path = Docker Compose** `restart: unless-stopped` (container `sclite-temp-manager`), not host systemd/venv. TrueNAS lacks `python3-venv`/`ensurepip`; a bare `.venv` of symlinks to `/bin/python3` will crash with `No module named Crypto`.
- Recipe in git: `deploy/truenas-scale/sclite-temp-manager/` (Dockerfile, compose, example steps JSON, optional `.service`).
- After reboot: `cd .../sclite-temp-manager && docker compose up -d` if container did not auto-start (same `hasBeenManuallyStopped` footgun as other compose services).
- Verify: `docker logs -f sclite-temp-manager` � stepped control (e.g. watched ~84C ? fan 80).

### 2026-09-06 � Blocks found card: 24h | 1wk | all

- Stats API: `blocks_all_time` / `orphans_all_time` (confirmed+pending; orphans separate).
- UI card uses triple `cardSplitValue` labels `24h` | `1wk` | `all` (CSS uppercases).
- SSR cache-bust is **hardcoded in `api.py` index()** � bump both `static/index.html` and the `api.py` rewrite strings together (`?v=20260906blocksall`).

### 2026-09-06 � Local CONVOY on quiet ABW port `:28926` (canary)

- Flipped `bip110-datum-pool` `datum.pool_port` **28916 ? 28926** (host `127.0.0.1`). Backup `config.json.bak.abwflip.20260906T050756Z`.
- Prime: `configure v3-abw-on` + `ABW assignment notice ACTIVE slot=0` on `listen=28926`. CONVOY: `DATUM Pool ABW: enabled`.
- Shares: Maveth.ASIC `share OK` + `ABW share ack+receipt+release` continuing; CONVOY `Share accepted`. Rest of pool still on `:28916` (unaffected).
- Rollback: `scripts/_convoy_flip_to_28916.sh` via `nas_run` (sets port 28916 + `docker restart bip110-datum-pool`). Do **not** advertise 28926 on site.
- Watch: Prime/CONVOY CPU+mem, reject spikes, coinbaser p99, ABW rotate every `TIDES_ABW_ROTATE_SECONDS=120`.

### 2026-09-06 � Health strip: coinbaser p99 threshold 2000 ? 3000 ms

- Degrade when `p99_reply_ms >= 3000` (was 2000). Modest only � reconnect/refresh tails were flipping the badge too often while typical `last_reply_ms` stayed ~100�400ms.
- `reject27_by_ua` remains warning-only (does not set degraded).
- Live `api.py` on Alexandria + `deploy-tides-prime-1` / `deploy-tides-web-1`.

### 2026-09-06 � Quarantine less aggressive (rehab 2 + looser auto-Q)

- Cause of Snowman flaps: mostly **Gateway job race** (`coinbase_id=0` / r27) while Prime coinbaser healthy (34 outs). ~7�11% r27 pool-wide; auto-Q at 10/20 was too tight ? set/clear thrash (29 Q / 30 clear in 2h).
- Live knobs on `tides-prime` compose: `TIDES_QUARANTINE_REHAB_SHARES=2` (was 5), window **40**, ratio **0.65**, min_samples **15**, check_every_n **25**.
- Cleared non-ops auto-Q rows; Vel ops-sticky left alone.

### 2026-09-06 � Coinbaser CONVOY-aligned: IO thread + stale-while-revalidate

- Prime `CoinbaserSplitCache`: DB window reload on **dedicated thread/loop + own Postgres pool**; `0x10` serves **last good snapshot** while refresh runs (never awaits refresh when cache exists).
- Logs: `coinbaser refresh IO thread ready` / `refresh #N � io=thread`.
- Deploy: `datum_prime.py` only into `deploy-tides-prime-1` (+ Alexandria copy); web/postgres not bounced for the code path (prime restart only).
- Non-CONVOY GWs unchanged on the wire � same faster multi-out replies.

### 2026-09-06 � Configure dialect: UNKNOWN sticky + known-v3 githash list

- Problem: StartOS CONVOY often UA `UNKNOWN_GIT_HASH` ? we sent v1 ? `Bad configuration version`; bare Linux CONVOY with `b9ea7dc` worked.
- Fix on Prime: `ConfigureDialectBook` � known markers (`b9ea7dc`, `convoy`, env `TIDES_CONFIGURE_V3_UA_SUBSTR`, **learned githashes** in meta `configure_v3_ua_markers`) ? immediate v3. Ambiguous UA: **v1 first**; if peer quick-fails after configure (0.5�12s, no coinbaser/share) ? sticky IP flips to v3 (and reverse). Successful v3 + real githash in UA ? **append to known-v3 list** (persisted).
- Leo adding StartOS githashes later: they land in UA ? match list (or env) ? instant v3, no probe.

### 2026-09-06 — Contributors: Blocks w/ shares (eras)

- New column **Blocks w/ shares**: `N/M (P%)` = distinct payout-window block-periods with any shares (CURRENT + completed finds still in window). e.g. hallais `4/8 (50%)`.
- **Not** payout % (still work-weighted). Cache `?v=20260906eras`.

### 2026-09-06 — Dialect settle + no auto-Q when healthy + p99 align

- **UNKNOWN dialect:** sticky probe capped at `TIDES_DIALECT_MAX_FLIPS` (default **2**), then **settle/lock** IP preference. Success (coinbaser/share) settles immediately. Stops v1↔v3 thrash on reconnect flaps.
- **Auto-Q:** if coinbaser `_last_outs >= 2` and no refresh error → **skip** reject-27 auto-Q (`skip auto-Q … coinbaser healthy`). Still auto-Q when coinbaser is weak/0-out. Cleared non-ops Q (`bc1q30ja3…`). Postgres container name: **`deploy-postgres-1`**.
- **Vel ops-sticky cleared (same day, later):** `bc1qrmj0…nasq8nvel` — Sep-3 hold so rehab wouldn't clear during 0-out coinbaser; no longer needed. SQL clear only (no Prime bounce). Quarantine table empty afterward.
- **Lesson (do not repeat):** ops-sticky is a **temporary** hold while a known footgun is live. The moment the root cause is fixed (here: coinbaser healthy / rehab no longer clears on 0-out), **clear the sticky same day**. Leaving Vel Q'd for days after the fix cost the miner and reputation. Hard rule below.

### Hard rule — ops-sticky quarantine

1. **Prefix `ops ` only while the incident is active.** Write the *why* in the reason (footgun + date).
2. **When the fix ships, clear sticky in the same session** (SQL on `deploy-postgres-1`). Do not “leave it alone” on later auto-Q sweeps.
3. **Never treat sticky as a ban.** If the miner is gone or quiet, still clear — Q does not need to outlive the bug.
4. Prefer **rehab + skip-auto-Q-when-healthy** over long sticky holds.
- **p99 map (all involved):**
  - **Producer:** `CoinbaserSplitCache.note_reply` → `_latency_ring` (maxlen 100) in `datum_prime.py` (Prime only).
  - **Snapshot:** `health_snapshot()["p99_reply_ms"]` (+ outs, cache age, UA reject tops). Persisted to meta `prime_health_json` for web.
  - **Consumers:** `api.py` `/api/health` on **Prime :8089** (in-process cache) and **web :8088** (meta snapshot). Degrade when `p99 >= 3000`. Site strip polls web.
  - **Fixed drift:** Prime was still `>= 2000`; now Prime / web / Alexandria all **`>= 3000`**.
- Deploy: `scripts/_deploy_dialect_settle_q_p99.sh` (Prime bounce). Bak: `/tmp/bip110-bak-dialect-settle-20260906T141311Z`.

### 2026-09-06 — ZFS: Alexandria/bitcoin/tides-pool (empty, grouped)

- Layout (no cutover, pool stayed up):
  - `Alexandria/bitcoin` → `/mnt/Alexandria/bitcoin` (group parent)
  - `Alexandria/bitcoin/tides-pool/{postgres,images,dumps}`
  - `Alexandria/bitcoin/knots` — eventual Knots datadir (live still `…/bip110-lab/knots-main-data` ~748G)
  - `Alexandria/bitcoin/datum-convoy` + `…/logs` — CONVOY GW config/logs homes (live still under `…/tides-pool/deploy/datum-pool-convoy` + `datum-pool-logs`)
- Each leaf is its own dataset for different snap rates later. Naming not `*-bak`.
- Live PG still: `/mnt/Alexandria/local/tides-pool/deploy/postgres-data`.
- **2026-09-06 insurance + CONVOY cutover (Prime/Knots stayed up):**
  - `pg_dump` → `/mnt/Alexandria/bitcoin/tides-pool/dumps/tides_20260906T164457Z.dump` (~10M)
  - `docker save` → `…/images/` (convoy, tides-pool, postgres:16, knots-pow)
  - CONVOY GW retargeted: config `/mnt/Alexandria/bitcoin/datum-convoy/config.json`, logs `…/datum-convoy/logs` (brief GW recreate only)
  - Autorecover paths updated to new CONVOY locations
  - Knots **seed rsync** started live→`/mnt/Alexandria/bitcoin/knots` (background; do **not** start 2nd node on dirty copy)
  - **2026-09-06 ~20:49Z:** hot uncapped rsync was load~10.5 / disk-bound; **stopped + restarted slow**: `nice -n 19 ionice -c3` + `--bwlimit=8192` (8 MiB/s). Script: `scripts/_knots_seed_rsync_background.sh` (`BWLIMIT_KB` override). Pause/restart: `scripts/_knots_seed_pause_slow.sh`. ~374G/748G at restart.
- Dual-node idea later: after consistent copy, can run Knots on alt RPC/P2P ports, then flip CONVOY `bitcoind.rpcurl` — still need one quiet final sync before trusting the copy.
- Scripts: `_zero_dt_insurance_and_convoy.sh`, `_knots_seed_rsync_background.sh`, plus zfs create/regroup scripts.

### Deploy downtime rule (ops)

- **Prefer zero downtime.** UI/static → `deploy-tides-web-1` only (Prime stays up).
- **Prime protocol/coinbaser/dialect/Q** needs process reload → brief Gateway reconnects. **Ask before bouncing Prime** when the change can wait; otherwise `docker cp` stage on Alexandria + container FS and take effect on next natural restart.
- Always `py_compile` / grep-verify **before** restart. Never restart web+prime together for a Prime-only patch.
- When Alexandria I/O is busy (e.g. Knots seed rsync), **docker cp from `/tmp/…` straight into the container** — skip Alexandria in the hot path; sync Alexandria after. Avoid concurrent `nas_run` oneshots (fixed `/tmp/bip110-oneshot.sh` races).

### 2026-09-06 — Coinbaser: frozen cache + era aggregates (fast)

**Problem:** every DATUM `0x10` re-ran `split_reward` over ~200k shares → reply p99 multi-second; full DB reload every 15s ~0.8–2.5s.

**Model now (live on `deploy-tides-prime-1`):**
- Timer every **5s** (`coinbaser_cache_seconds=5`): fold *new* shares into **current** era; recompute outs from **≤7 era weight maps + current** (O(payees), not O(shares)).
- `0x10` serves **frozen outs** — no rescale, no per-request split. Missing recent shares until next tick is OK.
- On pool find: **website/intended = cache snapshot**; `rotate_after_find` closes current into era ring; new shares = next-block work.
- Cold `full` ~1s once; steady `incr+N` ~**2ms**; `last_reply_ms` ~**0**.

Deploy: `scripts/_cb_eras_hot.sh` (tmp→docker cp→restart only). Bak not on Alexandria until post-rsync sync. Verify: `scripts/_cb_eras_verify.sh`.

### 2026-09-06 — Share ack-before-write (non-blocks)

- Gateway warn `No share acceptance for > 30 seconds` → reconnect → `resume declined` when Postgres was slow (hot Knots rsync). Cause: `_pow` awaited `append_share` / attempt row **before** `0x8F`.
- **Fix (live):** normal shares **ack first**, then persist. **`is_block` still persist-then-ack** (finder/window must land before ack). Persist failure after ack → WARNING only (no late reject).
- Deploy: `scripts/_deploy_ack_before_write.sh`. Marker: `ack first so Gateway never waits`.

### 2026-09-06 — Retro-credit r27 for current era only (after #968456)

- **Scope:** `share_attempts` with `reason_code=27` / `coinbase not multi-out` since last confirmed find (`968456`, head_seq `360464`, accounted_at `16:43:53Z`) — **not** older window eras.
- **Action:** inserted **4102** `shares` rows, worker `*.retro-r27`, work = per-addr median accepted work in era (fallback 16384). Credited work **+51,521,536**. Era shares 12463→16607 / work ~158M→~210M.
- Idempotent meta: `retro_r27_era_head` / `retro_r27_json`. Scripts: `_retro_r27_preview.sql`, `_retro_r27_apply.sql`.
- Coinbaser picked up via incr (`incr+3242` then remainder).

### CONVOY / PR10 reality check

- NAS pool GW image **`datum_gateway:convoy-pr10`** (rebuilt ~20:07Z) — but container was **unhealthy** at check time; only a minority of Prime handshakes show `3e252be…(convoy-pr10-clean)`.
- Most miners still speak older UAs (`b9ea7dc`, `671335d1`, `UNKNOWN_GIT_HASH`) from **their own** Gateways — PR10 on NAS does not upgrade remote GWs.

### 2026-09-06 — cb_type badge: % type-2 (v2)

- Was: **any** accepted `cb_id=2` → sticky ⚠.
- Now (`scripts/_hourly_cb_type_status.sh` v2): warn iff  
  `n2 / (n2+n3+n4+n5) ≥ CB_TYPE_WARN_PCT` (default **15**) and multi-out samples ≥ `CB_TYPE_MIN_SAMPLES` (default **20**).  
  `cb_id=0` ignored in ratio; too-few samples → keep previous sticky.
- Live Alexandria script updated + one run. Maveth still warn (~**74%** type-2 in 12h); several miners flipped to ✓ at 0% type-2.

### 2026-09-06 — cb_type badge sticky v3 (recent window clears)

- **Bug:** v2 still used **12h** lookback → old `cb_id=2` kept ⚠ even after hours of clean `cb_id=4`.
- **v3:** default `CB_TYPE_SINCE=30m`. Enough samples → rewrite status (warn or **clear to ok**). Too few → keep sticky. Same 15% / min 20.
- Reminder: `cb_id` is Prime rotating suggestion id, not true DATUM size-class; empty jobs are rejects (`id=0`).

### 2026-09-06 — How to connect: CONVOY PR #10 + ⚠ legend

- GitHub (still **open** PR as of check): [`CONVOYMining/datum_gateway#10`](https://github.com/CONVOYMining/datum_gateway/pull/10) — *Serve every miner the largest coinbase class on BLAKE2b work* (YUGE). Fork of luke-jr/datum_gateway.
- Site How-to-connect + Contributors legend updated: link PR #10, explain ✓/⚠/? vs reject-27 empties. Cache-bust `?v=20260906pr10howto`. Files: `static/index.html`, `static/app.js`, `api.py` SSR rewrite.

### 2026-09-06 — MaVeTh StartOS CONVOY+PR10 (`pow_0.4.1_23`)

- Gateway fork: [`Maveth/datum_gateway-convoy`](https://github.com/Maveth/datum_gateway-convoy) @ **`7491a50`** (CONVOY tip `b9ea7dc` + iohzrd PR #10).
- Packaging: [`Maveth/datum-gateway-startos`](https://github.com/Maveth/datum-gateway-startos) branch `maveth/pow-0.4.1-23-convoy-pr10` / version `#pow:0.4.1:23`.
- Release: https://github.com/Maveth/datum-gateway-startos/releases/tag/pow_0.4.1_23 (`datum_aarch64.s9pk` + `datum_x86_64.s9pk`).
- Leo’s `_20`/`_22` use CONVOY submodule **without** PR #10; this package is the YUGE fix for StartOS sideload.

### 2026-09-06 � #968625 aegyptian find (type-2 truncate; DUST_OK)

- Finder 158rptAe86LM9qSjygdjdVVDXAnVvHyv9o worker **MIISSBLUEE** (aegyptian). Reward 312518942. Status confirmed / payout_mode=needs_review.
- Intended: **33** outs from coinbaser_cache, sum 312501130. Chain: **n_vout=20** / **18** unique / **786B** coinbase. OPS listed 2802879 ? chain 10430934 (d�-7.63M).
- Fingerprint **LIKELY_TYPE2_TRUNCATE** (same class as #968159/#968456): ops_n leftover on ops + contiguous LISTED_ONLY tail.
- **LISTED_ONLY:** **16** addrs / **7,624,211 sats (0.07624211 BTC)** unpaid vs work snap � hold in OPS leftover. Do **not** absorb into onchain_split; prefer ops top-up like 968456/968159.
- **Dust:** intended dust_lt_1000 = **none**; chain dust_lt_1000 = **none** ? **DUST_OK**. One LISTED_ONLY c1qmk644� owed **2140** sats (below Core ~5460 send floor � bump to 5460 if topping up, same as 968456).
- Finder credit id **40** exists with credit_sats=0 / unpaid (promo window closed / bonus_next=0 at find).
- Scripts: _audit_968625.py, _check_latest_find_payouts.sh. Live verify FAIL expected until LISTED_ONLY topped up.

### 2026-09-06 � #968625 LISTED_ONLY top-up + 8th finder bonus (aegyptian)

- **UI:** tip badge is clickable **manual adjustment** (pending OR paid when manual_adjustment present); fallback label no longer " manual payout\. Cache \?v=20260906bonus8done\.
- **Bonus bubble:** **8 / 8 claimed** (promo complete); spot 8 = aegyptian #968625. Fee footnote / user tooltips no longer pitch open finder bonuses.
- **LISTED_ONLY sendmany:** 16 payees, owed 7624211 / paid 7627531 (mk644 floor 5460). txid \1d8c80d28090ee696a706bdac8dfa60ed52c1f7a48dbf12807379979782b27f8\. DB \ops_manual\ + \manual_payout_done\. Receipt \payouts/968625_ops_topup_listed_only/\.
- **Finder bonus (separate):** 4% of 312518942 = **12500757** sats to \158rpt�\. txid \7f24ca1f4866a91fe6282c5bad7a6a1a1cbd1e139af8bb1df5d44ec04f33bd6\. \inder_credits\ id 40; meta \manual_finder_pay_968625\. Receipt \payouts/968625_aegyptian_finder/\.

### 2026-09-06 � Pool SV1 Gateway (parallel CONVOY, full-pass)

- **New** container \ip110-datum-sv1\ image \datum_gateway:convoy-pr10\ � does **not** replace \ip110-datum-pool\ (23336/Maveth_tides).
- **Dataset:** \Alexandria/bitcoin/datum-convoy-sv1\ ? \/mnt/Alexandria/bitcoin/datum-convoy-sv1\ (config.json + logs).
- **Ports:** stratum **23337**, API **7156** (host net). Existing pool GW stays **23336** / **7155**.
- **Config:** \pool_pass_full_users=true\ (miner username = payout \c1�\ [.worker]); \pooled_mining_only=true\; secondary tag **\Stratum Endpoint\**; primary \TIDES\; fallback \pool_address\ = Private \c1qmtkp7�\; Prime \127.0.0.1:28916\.
- Scripts: \_nas_spin_datum_sv1_endpoint.sh\, \_fix_sv1_mountpoint.sh\.
- Miners: point SV1 at \	ides.maveth.ca:23337\ (once NPM/firewall publishes) with username = payout address.

### 2026-09-06 � Local GW 1% WORK skim (OPERATION FEE)

- **Not** \TIDES_FEE_BPS\ / coinbaser fee. **Work attribution** only for NAS-local DATUM Gateways.
- On accept: miner gets \99%\ work; OPS \c1q3l0q76�\ gets \1%\ work with worker **\OPERATION FEE\**.
- Peer gate: \127.0.0.1\, \::1\, \172.16.13.1\ (host-net GW via docker-proxy). Remote GWs keep public peer IPs ? **no skim**.
- Settings: \local_work_fee_bps=100\, \local_work_fee_worker\, \local_work_fee_peers\ in \config.py\; dual \ppend_share\ in \datum_prime.py\.
- Applies to **both** \ip110-datum-pool\ (23336) and \ip110-datum-sv1\ (23337) while they stay on the NAS. Move personal mining off-box to avoid the skim.
- Deploy: Prime-only restart; bak \*.bak.localworkfee.*\.

### 2026-09-07 — Maveth_tides nick + OPERATION FEE shares → Private

- `bc1qmtkp7haekhj6g5jw3cltnk6ptaeyrn4haccws4` nick set back to **Maveth_tides** (was Stratum Endpoint).
- Moved **533** `OPERATION FEE` share rows from OPS `bc1q3l0q76…` → that Private addr (script `scripts/_move_ops_fee_shares_to_priv.sh`). Did **not** move historical OPS `Maveth.ASIC` (~108k, pre-2026-09-04).
- New SV1 mining addr from same wallet: `bc1qj30fwc353ketu3nwm0lq0gzmmygu4qn4nh5h8m` (label tides-mine). Mine via `:23337` → GW secondary **Stratum Endpoint**.
- Future local 1% skim still credits OPS addr unless `local_work_fee` target changed.

### 2026-09-07 — Local GW skim: 50% OPS + 50% community (∝ window work)

- Still `local_work_fee_bps=100` (1% skim on local GW peers only).
- Of skim: `local_work_fee_ops_share_bps=5000` → OPS immediately (`OPERATION FEE`); remainder accrues and flushes on coinbaser tick as `STRATUM FEE`, proportional to non-OPS window work (largest-remainder).
- Hot path stays ~2 DB writes; community fan-out only on ~5s tick (approach A).
- Deploy: `scripts/_deploy_stratum_community_fee.sh` (Prime only). Verify: `scripts/_verify_stratum_community_fee.sh`.
- Live check: miner `bc1qj30fw…` / `maveth_svtest` produced `OPERATION FEE` + `STRATUM FEE` rows.

### 2026-09-07 — Stratum Endpoint group = nickname only

- Bug: `isStratumEndpointMember` also matched any address with worker `OPERATION FEE`, so after moving fee shares onto `bc1qmtkp7…` (nick Maveth_tides) it still appeared under Stratum and inflated group TOTAL work.
- Fix: nickname-only check. Cache `?v=20260907stratum4` (SSR in `api.py` + static). Scripts: `_deploy_stratum_nick_only_group.sh`, `_bust_stratum4_ssr.sh`.

### 2026-09-07 — OPS addr nick = Stratum Endpoint

- `bc1q3l0q76…` (OPERATION FEE sink) nick set to **Stratum Endpoint** so it groups with SV1 mining addr `bc1qj30fw…`.
- `bc1qmtkp7…` stays **Maveth_tides** (normal miner / old private payout).
- Script: `scripts/_set_ops_stratum_nick.sh`.

### 2026-09-07 — #968760 LISTED_ONLY top-up (type-2 truncate)

- Finder `158rpt…` / MIISSBLUEE. Intended 28 vs chain 18; LISTED_ONLY **10** = **0.05890245 BTC**; OPS leftover ~0.05886393.
- Top-up (DUST_OK mk644→5460): **0.05891277 BTC** sendmany txid `479f0b4301099ce13de60d82e234219bc5d654dea8d9fe29b1f42e984c2e4941` from `tides_pool`.
- Includes SV1 addr `bc1qj30fw…` (0.00213045). Receipt: `payouts/968760_ops_topup_listed_only/`.

### 2026-09-07 — STRATUM FEE only to green/live + this-block work

- Community half no longer splits across full-window offline payees (hit-and-run / dump-and-leave).
- Flush recipients: real share in last `local_work_fee_community_live_sec` (default 600s; excludes `STRATUM FEE`/`OPERATION FEE` workers) **and** work in `_current` (this unfinished block); weight = this-block work.
- Deploy: `scripts/_deploy_stratum_community_live.sh`.

### 2026-09-07 — #968770 Dragon LISTED_ONLY top-up

- Finder `bc1q8ptep…` / `hsboxii` (Dragon). Intended 29 vs chain 19; LISTED_ONLY **10** = **0.05527923 BTC**; OPS leftover ~0.0534 (short ~0.00186 vs LO from snap overpays).
- Top-up DUST_OK: **0.05528105 BTC** sendmany txid `7c3db1406328b26e6c8cb795c73429503c7a65d2f908ba8084b9bd4bd3b65692`.
- Includes SV1 `bc1qj30fw…` + `bc1q9g0mv…`. Receipt: `payouts/968770_ops_topup_listed_only/`.

### 2026-09-07 — Stopped non-Stratum pool GW (23336)

- `docker update --restart=no` + `docker stop bip110-datum-pool` (was unhealthy). Container left exited; not removed.
- Live stratum now **SV1 only**: `bip110-datum-sv1` **:23337** / UI **:7156**. Ports 23336/7155 closed.
- Reminder: external ASICs still on `tides.maveth.ca:23336` must move to **:23337** (or they go dark).

### 2026-09-07 — SV1 coinbaser fix (wait 15s + reuse + work_update 40)

- Patched `convoy-datum-pr10-clean`: `datum.coinbaser_fetch_timeout` default **15s** (was hardcoded 5); on miss/timeout **reuse last good multi-out if same prevhash**; **do not publish 0-out** (retry). Image `datum_gateway:convoy-pr10-cbreuse`.
- SV1 config: `work_update_seconds=40`, `coinbaser_fetch_timeout=15`. Recreated `bip110-datum-sv1` (miners reconnect ~minutes).
- Early check: `maveth_svtest` **0 rejects / 2m**; coinbaser **0 timeouts / 0 gen0 / multi-out OK**.

### 2026-09-07 — Prime accepts tip-window r27 / cb_id=0 (design empties)

- **Server-side only** (Prime). After real tip advance, GW empty-blasts by design; those shares have `coinbase_id=0`.
- `CoinbaserCache.note_chain_height` arms ~30s grace (`r27_new_tip_grace_sec`); `_coinbase_id_ok(..., is_block=)` accepts non-block `cid==0` during grace. **Block finds on empty still refused.**
- Deployed via `_tmp_live_datum_prime.py` → `deploy-tides-prime-1` (`scripts/_deploy_prime_r27_tip_grace.sh`).
- **SV1/GW same-tip no-empty patch not applied** — aborted rebuild; source reverted. Same-tip urgent empty-blast remains GW behavior; tip empties are accepted by Prime.

### 2026-09-07 — Confirm: match recent coinbaser cache snaps (+ dust)

- Coinbaser publishes ~every **5s** from cache to all GWs. Winning job may use **current or prior 1–2 ticks**, not a re-split of shares at find `share_head`.
- **Fix:** `CoinbaserSplitCache._snap_ring` (maxlen 8). On find, `snapshot_for_block(reward, chain=…)` matches chain→ring (rescale outs to reward, dust≤1000). Embeds `recent_candidates` for confirm.
- **Confirm:** try candidates (rescale+dust) → else **same-payee value drift** → refresh intended from chain → `onchain_split`. LISTED_ONLY still `needs_review`.
- **#968827 Snowman27:** was `needs_review` (28/28 same payees, value drift); retro-fixed → `onchain_split` + intended from chain. Deploy: `scripts/_deploy_confirm_cache_match.sh`.

### 2026-09-07 — Lock `share_head` to matched cache snap (drift → next block)

- On find/confirm match: set block `share_head_seq` to snap `max_seq` (not live `max_share_seq()`). Shares after that watermark stay in the **next** window automatically.
- `rotate_after_find(head=locked)` + log `drift_shares≈live_max-head`. Deploy: `scripts/_deploy_share_head_lock.sh`.

### 2026-09-07 — Missed SV1 find #968837 (Prime restart race)

- SV1 `BLOCK FOUND` @ 05:14:41Z (`TIDES`/`Stratum Endpoint`, full multi-out ~25 payees). `submitblock` → node `duplicate` (already had it).
- **~1s earlier** SV1→Prime DATUM handshake **`Connection refused`** (Prime bounce for share-head-lock deploy) → no `is_block` share → **no `blocks` row** → not on site.
- **Why not automatic (then):** chain_sync only confirmed/orphaned *existing* pending finds; no tip scan for unrecorded TIDES coinbases.
- Backfilled: `scripts/_backfill_968837.sh` → `confirmed`/`onchain_split`/intended from chain. Finder set to `maveth_svtest` (`bc1qj30fw…`).
- **Fix going forward:** `adopt_missed_tides_finds` in `reconcile_pool_blocks` — each chain sync, lookback ~16 tips; if TIDES+ops coinbase missing from `blocks`, **auto-adopt** + log `MISSED TIDES FIND ADOPTED`. Deploy: `scripts/_deploy_missed_find_scan.sh`.

### 2026-09-07 — Anti-spoof: no foreign coinbase payees

- Problem: explorers label `TIDES.*` (e.g. #968878 `TIDES.AlphaPool`) as us; payees were foreign, **no ops**.
- Rule: tag+ops still required; **every non-ops value payee must exist in shares/users**. Missing our miners from coinbase OK (truncate); **extras never OK**.
- Real finds always pay known share addresses (coinbaser built from DB). Site never auto-wallets; ops_manual / LISTED_ONLY stay manual/PSBT.
- `anti_spoof_known_payees` + `known_payout_addresses`; wired into tip adopt + BLOCK FOUND resolve. Deploy: `scripts/_deploy_anti_spoof_payees.sh`.

### 2026-09-07 � #969065 THEOSOFICA LISTED_ONLY top-up (type-2 truncate)

- Finder `bc1qy9ms6�` / THEOSOFICA `rig2`. Non-PR10 GW (cb_id=2). Intended 23 vs chain 18 (~766B); LISTED_ONLY **5** = **0.12274322 BTC**; OPS leftover ~0.12276080.
- Walkback not applicable (no right-size GW for this miner). Site: `payout_mode=ops_manual` + meta `manual_adjustment_969065` (`pays` array for UI table).
- Top-up sendmany from Windows `tides_pool`: **0.12274322 BTC** txid `d78a5b98c770162efc667b8b79ca9f3f84ad0a80d18d7729bad00fa9bda59f33` (fee 471 sats).
- DB `manual_payout_done=true`; meta `ops_topup_969065_listed_only`. Receipt: `payouts/969065_ops_topup_listed_only/`.
- Scripts: `_build_969065_listed_only.py`, `_apply_969065_manual_adj_pending.sh`, `_apply_969065_manual_adj.sh`.

### 2026-09-07 � Primary coinbase tag TIDES ? RIPTIDE

- **Pubkey unchanged** (still `b95abf4a�03d743`). Tag ? DATUM keys.
- Compose (`docker-compose.yml` + `.split.yml`): `TIDES_COINBASE_TAG_PRIMARY=RIPTIDE`, `TIDES_COINBASE_TAG_LEGACY=TIDES`.
- Code: `matched_pool_tag` / classify accepts primary **or** legacy so historical `TIDES` finds still attribute; new `0x99` configure stamps **RIPTIDE**.
- Deploy: recreate prime+web, docker cp `config/block_confirm/datum_prime/payment_verify/index.html` (image alone lacked newer `payment_verify` helpers).
- Verified: Prime logs `tag=RIPTIDE`; Gateways reconnecting; health ok.
- Follow-ups: mempool `customize.js` / pools regex still keyed on `TIDES` ASCII � update when explorer labels matter; optional GW local `coinbase_tag_primary` (pooled override already from Prime).

### 2026-09-07 � Mempool RIPTIDE labels + #969177 Skeleton type-2

- Mempool `customize.js`: detect primary `RIPTIDE|TIDES`; extract secondary even when glued (`RIPTIDESkeleton` ? `Skeleton_(RIPTIDE)`). Pool regexes for slug `riptide` now include `RIPTIDE`. Rematched tip #969177 ? riptide.
- **#969177 Skeleton** (worker 2): classic DATUM type-2 truncate � `cb_bytes=766`, intended 24 vs chain 18; LISTED_ONLY **6** = **0.13003160 BTC**; OPS leftover ~0.1297 BTC. `payout_mode=ops_manual` + pending meta `manual_adjustment_969177`. Receipt package `payouts/969177_ops_topup_listed_only/` (sendmany not broadcast yet).
- Side-effect of earlier Prime/web recreate: web lost docker-cp'd `api/models/app/store` adj helpers � restored from Alexandria so adjustment table shows again.

### 2026-09-07 � #969177 Skeleton LISTED_ONLY paid

- Top-up **0.13003160 BTC** (6 payees) sendmany txid `1b60031fe3a82450696992bba99226aec44f04481791e8b32db585285fe44ff6` (fee 773 sats) from Windows `tides_pool`.
- DB `manual_payout_done=true`; meta `manual_adjustment_969177` status=paid. Receipt `payouts/969177_ops_topup_listed_only/`.

### 2026-09-07 � Hot-reload local GW fee (`runtime_fees`)

- SV1/local 1% skim (`local_work_fee_bps`) is now overridable via Postgres meta `runtime_fees` JSON � **no Prime restart** to change.
- Module `tides_pool/runtime_fees.py`; skim path in `datum_prime` reads it (=3s TTL cache).
- Seeded `local_work_fee_bps=100`, `ops_share_bps=5000`.
- Ops: `scripts/_set_local_work_fee.sh 50` / `100` / `0` / `--show`. Global coinbaser `TIDES_FEE_BPS` still compose+recreate (not hot).

### IDEA (parked) � Solo-with-fee Stratum endpoint (custom GW)

**Ask later:** SV1-like endpoint for solo mining with a fee, not TIDES window pool.

**Product sketch**
- ASIC ? Stratum V1 ? **custom Gateway** (new port, not :23337) ? Knots GBT.
- Coinbase **per connection**: `[miner_addr : 100%-fee] + [ops : fee%]`.
- **Different tag** than main Prime (e.g. `RIPSOLO`) � do **not** use TIDES `0x99` / RIPTIDE.
- **No** DATUM multi-user / window split � never call live TIDES coinbaser.

**Why GW not Prime**
- Stock solo: stratum username **ignored**; only `mining.pool_address`.
- Stock pooled: Prime `0x10` coinbaser has **no username** ? pool-wide split, not per-client.
- So per-client templates need a **forked Gateway** (MVP). Optional tiny SoloFee Prime still needs GW changes to pass identity.

**MVP checklist**
1. Fork `convoy-pr10-cbreuse` (or equiv) ? `solo_fee` mode.
2. Build 2-out coinbase from `bc1�.worker` + `solo_fee_bps` + ops addr.
3. Own primary tag; `pooled_mining_only` off / no Prime coinbaser.
4. Separate listen port; smoke one ASIC find with fee split on-chain.

**Not in scope for MVP:** TIDES Prime, `runtime_fees` skim, window shares.

**Related live:** SV1 pooled = `bip110-datum-sv1` :23337; local work skim = `meta.runtime_fees` / `local_work_fee_bps` (attribution only, not this product).

### IDEA (parked) � Site dash as read-model cache (Convoy-style)

**Hold for now** (2026-09-07). Resume when ready.

- Web only reads materialized snapshots; updater fills on ~10 min timer **+ our new finds**.
- Recompute live picture = **7 confirmed + current** only; completed finds stay on frozen `intended_payout_json`.
- Contributors will scale to **1000s** ? pagination + slow refresh; expand is UI-cheap if soft refresh does not full-recompute.
- Must not touch Prime/GW coinbaser path; web-only / additive `meta` or dash tables.
- Convoy ref: leaderboard ~10 min; cards/blocks/graph ~60s; paged tables.

### 2026-09-07 � lab-website (cached dash prototype)

- Dataset: `Alexandria/bitcoin/lab-website` (TrueNAS may show mount as `/mnt/mnt/Alexandria/...`; use `/mnt/Alexandria/bitcoin/lab-website`).
- Container `lab-website-1`, compose project **`lab-website`** (not `deploy` — avoid orphaning live tides).
- Bind `0.0.0.0:8090` → Windows: `http://192.168.0.143:8090/`
- Live site unchanged `:8088`. Snapshots via `docker compose -p lab-website run --rm snapshot-builder`.
- Source also in repo `lab-website/`.
- **No live proxy.** Builder pulls verbatim JSON (pool + `/api/user/{addr}` for every window contributor / coinbaser payee) into `snapshots/users/<addr>/`; lab-web serves the same paths. Promote path = move builder→cache into prod web.
- Miner pages need: `user.json`, `payouts.json`, `shares.json`, `charts_{1h,24h,7d,window}.json`. Without those snaps, `/address?a=…` stuck on Loading….

### 2026-09-07 — Site UI: SV1 howto + fee copy, drop finder promo

- Lab `:8090` first, then promoted to live `:8088` (Alexandria static + docker cp; SSR `?v=` only in `api.py`).
- Removed finder-bonus promo banner (8/8 complete). Ledger untouched.
- Fee promo + footnote: DATUM **0%**; pool SV1 **variable work fee (currently 2%)**, half → miners (`STRATUM FEE`) / half → ops (`OPERATION FEE`).
- Howto lists `tides.maveth.ca:23337` as **not recommended**; header **DATUM preferred**.
- Cache `?v=20260907sv1fee1`. Backups `*.bak.sv1fee.*` on Alexandria.

### 2026-09-07 — Lab snapshot cadence 5 min + freshness badge

- `lab-website-snap-1` (`snapshot-refresher`) loops full pool+user snaps; target **300s** from loop start (build ~3–4m + short sleep).
- Lab UI shows `snapshot Xm ago · ~5 min` (tooltip: connect HTML always fresh; live `:8088` `/api/*` for fresher JSON).
- **CUTOVER:** Public `:8088` = snapshot site (`lab-website-1`); live real-time APIs on **`:8087`** (`deploy-tides-web-1`). NPM still → 8088.
- Lab **`:8090` unpublished** (code/dataset kept at `Alexandria/bitcoin/lab-website` for next upgrade).
- Contributors **10/page** + miner `/api/user/*` snaps are on public `:8088` (hard-refresh `?v=20260907snap5m2`).
- **Live overlays on snap site** (DB meta, ~15s TTL): contributor **gateway class** `cb_type_*`; blocks **`manual_adjustment`** (LISTED_ONLY payout table). Rest stays 5‑min snap.
- After any `tides-web` **force-recreate**, re-`docker cp` Alexandria `api.py` **and** `models.py` (else `manual_adjustment` stripped).
- `store.contributor_rows` must emit `eras_with_work*` — was dropped (`del cutoff_seq`); restored 2026-09-07. UI column **% Blocks w Shares**; **Last share** column removed (`?v=20260907eras1`).
