# Prime / SV1 restart — miner drop risk

**Critical (ops):** Restarting Prime (`deploy-tides-prime-1`) or SV1 (`bip110-datum-sv1`)
kicks **all** connected miners. Some never come back. Target downtime **< 5 seconds**.

## Before any restart

1. Tell the operator and get **explicit** approval.
2. Run import preflight against the live container package:
   `scripts/prime-preflight-import.sh [files...]`
3. Deploy only with confirmation:
   `CONFIRM_PRIME_RESTART=1 scripts/deploy-sv1-stratum-nick-prime.sh`
4. Prefer no-restart paths (web/static/snap/DB) when possible.
5. Never take Prime and SV1 down together unless explicitly requested.

## After restart

- `/health` OK, listen ports up, `share OK` flowing within seconds.

## SV1 healthcheck port (saved for next recreate)

- Stratum listen is **23337** (`datum-convoy-sv1/config.json`).
- Old image `HEALTHCHECK` probed **23334** → Docker showed `unhealthy` forever.
  That was **noise only** (restart policy does not kill on unhealthy; sends still worked).
- Fixed for next bounce:
  - Compose: `/mnt/Alexandria/bitcoin/datum-convoy-sv1/docker-compose.yml` healthcheck → `23337`
  - Dockerfile (next image build): default probe/EXPOSE → `23337` / `7156`
- Next intentional recreate (no rush):
  ```bash
  cd /mnt/Alexandria/bitcoin/datum-convoy-sv1
  sudo docker compose up -d --force-recreate
  ```
  Expect ~2–5s miner reconnects. Do **not** bounce Prime at the same time.
