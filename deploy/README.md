# Deploy tides-pool on TrueNAS Scale

## Paths (Alexandria — not “tank”)

| What | Path |
|------|------|
| Repo / compose | `/mnt/Alexandria/local/tides-pool` |
| Sibling lab (RC2, DATUM, mempool) | `/mnt/Alexandria/local/bip110-lab` |
| NAS LAN | `192.168.0.143` (`ssh bip110-nas`) |

## Dockge / Compose

```bash
cd /mnt/Alexandria/local/tides-pool/deploy
# set TIDES_POOL_OPS_ADDRESS to a NEW TN4 address (dedicated Knots wallet — not MaVeTh solo)
docker compose up -d --build
curl http://127.0.0.1:8088/health
curl http://192.168.0.143:8088/stats
```

- UI / API host port **8088** (8080 is already SABnzbd on this NAS)
- Postgres host port **5433**


## Do not

- Reuse the live solo DATUM wallet / MaVeTh payout for pool ops.
- Point ASICs directly at this service (DATUM Gateway only).
- Treat `O:\tides-pool` as production.

## Later

Dedicated `knots-pool` container + datadir under e.g.  
`/mnt/Alexandria/local/tides-pool/knots-data` (Phase 4/5).
