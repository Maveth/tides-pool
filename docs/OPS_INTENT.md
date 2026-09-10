# How the pool is *meant* to work (ops intent)

Product / ops story — not a code walkthrough. Update when intent changes.

**Status:** refined 2026-09-08 from live ops talk. UA markers are secondary.

---

## Two ways to mine on RIPTIDE

| Path | Miner setup | Work skim | Nickname cohort |
|------|-------------|-----------|-----------------|
| **Own DATUM Gateway** (preferred) | Their Knots + DATUM → Prime `:28916` | **None** | Their own `coinbase_tag_secondary` |
| **Pool SV1** `:23337` | ASIC → our SV1 Gateway only | **Yes** (variable; meta `runtime_fees`) | **`Stratum Endpoint`** |

Never point ASICs at Prime `:28916`.

---

## Fees (SV1 path) — intended

1. Every share that arrives via the **SV1 Gateway peer** is **skimmed** (not by nickname, by path).
2. **Half of the skim → ops payout** (`OPERATION FEE` on the ops address).
3. **Rest of the skim → `STRATUM FEE`**, split **proportionally among currently active non-SV1 miners** (own-Gateway / live green). That redistribute code already exists; do not casually rework it.
4. Outside SV1 → **no** that work skim.

Display nick does **not** turn skim on/off. Skim is path-based. Nick is how Contributors **folders** the SV1 cohort.

---

## Contributors layout — intended

**Website (no Prime restart):** Contributors default view is two sections —
**Pool SV1 — Stratum Endpoint** vs **DATUM — own Gateway**. Membership is still
nickname `Stratum Endpoint`. Frontend labels: `OPERATION FEE`→Ops fee,
`STRATUM FEE`→Stratum fee share.


- Basic layout auto-groups nickname **`Stratum Endpoint`** into one folder (expand for child payout addresses).
- **Every SV1 client payout address** (current **and** past) should sit under that nick — rentals included.
- **Do not** key membership by IP or by “same address” (rentals / SNAT break that).
- **Do not** treat “has a `STRATUM FEE` worker row” as Stratum membership — those rows are **fee credits to non-SV1 live miners**.
- SV1 **Clients** UI (TCP sessions) ≠ Contributors (payout addresses with window work).

---

## Identity

- Payout identity = Stratum/DATUM username before `.worker` (must be `bc1…` / `1…`).
- SV1 should pass full users (`pool_pass_full_users`) so each customer address is visible.
- Secondary tag on SV1 templates is `Stratum Endpoint` (cohort label in POW).

---

## Restart / blast radius

| Piece | Blast |
|-------|-------|
| Website static (`bitcoin/website/static`) / snap | Low |
| `tides-web` recreate with new static | Low–medium |
| `tides-prime` | **High** — Gateways drop |
| `bip110-datum-sv1` | **High** — Stratum clients drop |

Prefer one minimal change-set; never bounce SV1+Prime “just in case.”

---

## Related

- Site grouping: `static/app.js` `isStratumEndpointMember` / `AUTO_NICK_GROUP_NAMES`
- Skim: `is_local_work_fee_peer`, `runtime_fees`, `_flush_community_fee`
- Nick learn today: POW secondary tag only when nick blank (`_try_learn_nickname_from_pow`)
