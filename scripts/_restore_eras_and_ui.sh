#!/bin/bash
# Restore eras_with_work* in store.contributor_rows; update public UI columns.
set -euo pipefail
ALX=/mnt/Alexandria/local/tides-pool/src/tides_pool
WEB=deploy-tides-web-1
APP=/app/src/tides_pool
ROOT=/mnt/Alexandria/bitcoin/lab-website
SRC=/tmp/bip110-lab-website
TS=$(date -u +%Y%m%dT%H%M%SZ)

cp -a "$ALX/store.py" "$ALX/store.py.bak.pre-eras.$TS"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("/mnt/Alexandria/local/tides-pool/src/tides_pool/store.py")
t = p.read_text(encoding="utf-8")

new_fn = r'''def contributor_rows(
    window: list[Share],
    *,
    recent: list[ShareRow] | None = None,
    hashrate_window_sec: int = 600,
    current_since_seq: int | None = None,
    confirmed_heads: list[tuple[int, int]] | None = None,
    cutoff_seq: int | None = None,
) -> list[dict[str, Any]]:
    """Build contributor rows for the payout window.

    work / shares = full window (prior confirmed finds in window + current).
    work_current / shares_current = shares newer than current_since_seq
    (typically the share_head_seq of the latest confirmed pool find = this block only).
    current_since_seq=None → treat the whole window as current (no confirmed finds yet).

    confirmed_heads: newest-first list of (height, share_head_seq) for confirmed finds
    in the payout window. Used to label last_share_blocks_ago / height.

    eras_with_work / window_eras: how many distinct block-periods in the window
    this address has any shares in (e.g. 4/8 = 50%). Not the same as payout % —
    payout stays work-weighted.
    """
    work: dict[str, int] = {}
    shares: dict[str, int] = {}
    work_cur: dict[str, int] = {}
    shares_cur: dict[str, int] = {}
    max_seq: dict[str, int] = {}
    eras_hit: dict[str, set[int]] = {}
    for s in window:
        work[s.address] = work.get(s.address, 0) + s.work
        shares[s.address] = shares.get(s.address, 0) + 1
        if s.address not in max_seq or s.seq > max_seq[s.address]:
            max_seq[s.address] = int(s.seq)
        if current_since_seq is None or s.seq > current_since_seq:
            work_cur[s.address] = work_cur.get(s.address, 0) + s.work
            shares_cur[s.address] = shares_cur.get(s.address, 0) + 1
    total = sum(work.values()) or 1

    recent_work: dict[str, int] = {}
    if recent:
        for r in recent:
            recent_work[r.address] = recent_work.get(r.address, 0) + r.work

    heads = list(confirmed_heads or [])

    def _era_for_seq(seq: int) -> int:
        """Map share seq → era index (0=CURRENT, 1=1 ago, …). Same rules as last-share label."""
        if not heads:
            return 0
        newest_head = int(heads[0][1])
        if seq > newest_head:
            return 0  # CURRENT
        for i, (_height, _head) in enumerate(heads):
            prev_head = int(heads[i + 1][1]) if i + 1 < len(heads) else -1
            if seq > prev_head:
                return i + 1
        return len(heads)

    # Denominator: with N confirmed heads + current unfinished → N eras when cutoff
    # aligns to oldest head; else CURRENT + each listed find.
    if not heads:
        window_eras = 1
    elif cutoff_seq is None:
        window_eras = len(heads) + 1
    else:
        window_eras = max(len(heads), 1)

    for s in window:
        eras_hit.setdefault(s.address, set()).add(_era_for_seq(int(s.seq)))

    def _last_share_label(addr: str) -> tuple[int, int | None]:
        """Return (blocks_ago, height|None). ago=0 → CURRENT (unfinished block)."""
        ms = max_seq.get(addr)
        if ms is None:
            return 0, None
        if not heads:
            return 0, None
        newest_head = int(heads[0][1])
        if ms > newest_head:
            return 0, None  # CURRENT
        for i, (height, head) in enumerate(heads):
            prev_head = int(heads[i + 1][1]) if i + 1 < len(heads) else -1
            if ms > prev_head:
                return i + 1, int(height)
        return len(heads), int(heads[-1][0])

    rows = []
    for addr, w in work.items():
        rw = recent_work.get(addr, 0)
        hs = estimate_hashrate_hs(rw, hashrate_window_sec) if rw else 0.0
        ago, last_h = _last_share_label(addr)
        n_eras = len(eras_hit.get(addr) or ())
        eras_pct = (
            round(100.0 * n_eras / float(window_eras), 1) if window_eras > 0 else 0.0
        )
        rows.append(
            {
                "address": addr,
                "work": w,
                "work_current": work_cur.get(addr, 0),
                "share_pct": round(100.0 * w / total, 4),
                "shares": shares.get(addr, 0),
                "shares_current": shares_cur.get(addr, 0),
                "hashrate_hs": hs,
                "activity": (
                    "live"
                    if hs > 0
                    else ("idle" if work_cur.get(addr, 0) > 0 else "offline")
                ),
                "last_share_blocks_ago": int(ago),
                "last_share_block_height": last_h,
                "window_eras": int(window_eras),
                "eras_with_work": int(n_eras),
                "eras_with_work_pct": float(eras_pct),
            }
        )
    rows.sort(key=lambda r: (-r["work"], r["address"]))
    return rows
'''

pat = re.compile(
    r"def contributor_rows\([\s\S]*?\n    return rows\n",
    re.M,
)
m = pat.search(t)
if not m:
    raise SystemExit("contributor_rows not found")
t2 = pat.sub(new_fn + "\n", t, count=1)
if "eras_with_work_pct" not in t2:
    raise SystemExit("patch failed — eras not present")
p.write_text(t2, encoding="utf-8")
print("patched store.py contributor_rows with eras")
PY

docker cp "$ALX/store.py" "$WEB:$APP/store.py"
docker restart "$WEB"

echo "=== wait live ==="
for i in $(seq 1 40); do
  code=$(curl -sS -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:8087/api/stats || echo 000)
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo "8087 $code"

echo "=== verify eras on live contrib ==="
python3 - <<'PY'
import json, urllib.request
raw = urllib.request.urlopen("http://127.0.0.1:8087/api/contributors?limit=5", timeout=180).read()
rows = json.loads(raw)
for c in rows:
    print(c.get("nickname"), "eras", c.get("eras_with_work"), "/", c.get("window_eras"), "pct", c.get("eras_with_work_pct"))
if not any(c.get("eras_with_work_pct") for c in rows):
    raise SystemExit("still all-zero eras")
PY

echo "=== sync UI static + rebuild public (quick contrib snap) ==="
rsync -a --delete "$SRC/static/" "$ROOT/static/"
rsync -a "$SRC/src/" "$ROOT/src/"
rsync -a "$SRC/deploy/" "$ROOT/deploy/"
# Refresh contributors.json from live without full user snap
python3 - <<'PY'
import json, urllib.request
from pathlib import Path
from datetime import datetime, timezone
snap = Path("/mnt/Alexandria/bitcoin/lab-website/snapshots")
url = "http://127.0.0.1:8087/api/contributors?limit=500"
raw = urllib.request.urlopen(url, timeout=180).read()
rows = json.loads(raw)
(snap / "contributors.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
print("wrote contributors", len(rows), "sample eras", rows[0].get("eras_with_work_pct") if rows else None)
# touch meta as_of lightly
meta_path = snap / "meta.json"
meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
meta["contributors_refreshed_at"] = datetime.now(timezone.utc).isoformat()
meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
PY

cd "$ROOT/deploy"
docker compose -p lab-website up -d --force-recreate lab-web
sleep 2
python3 - <<'PY'
import json, urllib.request
raw = urllib.request.urlopen("http://127.0.0.1:8088/api/contributors?limit=5", timeout=30).read()
rows = json.loads(raw)
for c in rows:
    print("8088", c.get("nickname"), c.get("eras_with_work"), c.get("window_eras"), c.get("eras_with_work_pct"))
html = urllib.request.urlopen("http://127.0.0.1:8088/", timeout=15).read().decode()
assert "% Blocks w Shares" in html
assert ">Last share<" not in html
print("UI ok eras1")
PY
echo ERAS_UI_OK
