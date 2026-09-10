# datum-pool deploy dir (Prime data mount)

This directory is still bind-mounted into **Prime/web** as `/app/data`
(`pool_keys.json`, etc.).

**Convoy gateway config (personal mining GW) lives at:**
`/mnt/Alexandria/bitcoin/datum-convoy/`

Do not put the Convoy `config.json` here anymore.
Archived old gateway configs: `archive-20260908T031853Z/`

## configure_v3_ua_markers.json

Known DATUM Gateway UA githashes / substrings that should force configure **v3**.
Prime hot-reloads this file on mtime (no restart) once that code is running.
Also auto-learns successful v3 sessions into Postgres meta.
