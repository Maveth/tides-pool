#!/bin/bash
# Coinbase-class badge for contributors (meta cb_type_status_v1).
#
# Sticky state machine (v3):
#   - Look at a RECENT window only (default 45m), not a 12h pile of history.
#   - If enough multi-out accepts in that window:
#       warn  if type-2 share >= WARN_PCT
#       ok    if type-2 share <  WARN_PCT  (clears a prior warn)
#   - If too few samples this window → keep previous sticky status.
#   - cb_id=0 (empty) ignored in the ratio.
#
# Note: wire cb_id is Prime's rotating suggestion id (1..250), a proxy — not a
# perfect DATUM size-class. Empty jobs are rejects (id 0), not this badge.
set -euo pipefail
PG=deploy-postgres-1
# Recent check window — short so a fix clears the badge without waiting out 12h.
SINCE="${CB_TYPE_SINCE:-30m}"
WARN_PCT="${CB_TYPE_WARN_PCT:-15}"
MIN_SAMPLES="${CB_TYPE_MIN_SAMPLES:-20}"
LOG=/tmp/prime_cb_type_shares.log
META_KEY=cb_type_status_v1

docker logs --since "$SINCE" deploy-tides-prime-1 2>&1 \
  | grep -E 'share OK user=' \
  > "$LOG" || true

docker exec "$PG" psql -U tides -d tides -tAc \
  "SELECT value FROM meta WHERE key='$META_KEY';" > /tmp/cb_type_prev.json || true

export CB_TYPE_WARN_PCT="$WARN_PCT"
export CB_TYPE_MIN_SAMPLES="$MIN_SAMPLES"
export CB_TYPE_SINCE_LABEL="$SINCE"

python3 <<'PY'
import json, os, re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

log = Path("/tmp/prime_cb_type_shares.log").read_text(errors="replace").splitlines()
prev_raw = Path("/tmp/cb_type_prev.json").read_text().strip()
prev = {}
if prev_raw:
    try:
        prev = json.loads(prev_raw)
    except Exception:
        prev = {}
prev_by = (prev.get("by_address") or {}) if isinstance(prev, dict) else {}

warn_pct = float(os.environ.get("CB_TYPE_WARN_PCT", "15") or 15)
min_samples = int(os.environ.get("CB_TYPE_MIN_SAMPLES", "20") or 20)
since_label = os.environ.get("CB_TYPE_SINCE_LABEL", "45m")

counts = defaultdict(lambda: defaultdict(int))
for line in log:
    m = re.search(r"share OK user=([^\s]+).*\bcb_id=(\d+)", line)
    if not m:
        continue
    addr = m.group(1).split(".", 1)[0]
    if not addr.startswith(("bc1", "1", "3")):
        continue
    counts[addr][m.group(2)] += 1

now = datetime.now(timezone.utc).isoformat()
TIP_OK = (
    f"Recent multi-out accepts look healthy "
    f"(type-2 share below {warn_pct:.0f}% in last {since_label}). "
    f"Sticky ✓ until a new bad window."
)
TIP_WARN = (
    f"Recent multi-out accepts are heavy on cb_id=2 "
    f"(≥{warn_pct:.0f}% in last {since_label}). "
    f"Sticky ⚠ until a later check sees a clean/good window."
)
TIP_UNK = "No recent accepted multi-out share type yet — status unknown."

# Start from previous sticky; only rewrite when this window has enough signal.
by_address = {a: dict(v) for a, v in prev_by.items()}

flipped_ok = []
flipped_warn = []
held = []

for addr, ctr in counts.items():
    n0 = int(ctr.get("0", 0))
    n2 = int(ctr.get("2", 0))
    n3 = int(ctr.get("3", 0))
    n4 = int(ctr.get("4", 0))
    n5 = int(ctr.get("5", 0))
    n_multi = n2 + n3 + n4 + n5
    n_large = n3 + n4 + n5
    pct2 = (100.0 * n2 / n_multi) if n_multi else 0.0
    old = by_address.get(addr) or {}
    old_st = (old.get("status") or "unknown").lower()

    if n_multi < min_samples:
        # Keep sticky — not enough new evidence to rewrite.
        status = old_st if old_st in ("ok", "warn", "unknown") else "unknown"
        tip = old.get("tip") or TIP_UNK
        sticky_ok_since = old.get("sticky_ok_since")
        held.append(addr)
    elif pct2 >= warn_pct:
        status = "warn"
        tip = TIP_WARN + f" This miner: {pct2:.1f}% type-2 ({n2}/{n_multi})."
        sticky_ok_since = None
        if old_st != "warn":
            flipped_warn.append(addr)
    else:
        # Good recent window → clear warn / set ok (sticky until new bad window).
        status = "ok"
        tip = TIP_OK + f" This miner: {pct2:.1f}% type-2 ({n2}/{n_multi})."
        sticky_ok_since = old.get("sticky_ok_since") or now
        if old_st != "ok":
            flipped_ok.append(addr)

    by_address[addr] = {
        "status": status,
        "tip": tip,
        "types": {k: int(v) for k, v in sorted(ctr.items())},
        "pct_type2": round(pct2, 2),
        "n_multi": n_multi,
        "n0": n0,
        "updated_at": now,
        "sticky_ok_since": sticky_ok_since,
        "window": f"docker logs --since {since_label}",
        "policy": {"warn_pct": warn_pct, "min_samples": min_samples},
    }

out = {
    "version": 3,
    "updated_at": now,
    "policy": {
        "warn_pct": warn_pct,
        "min_samples": min_samples,
        "since": since_label,
        "known_safe_ua_tips": [],
        "known_bad_ua_tips": ["b9ea7dc"],
        "notes": (
            "v3 sticky: recent window only. "
            f"If multi-out samples>={min_samples}: "
            f"warn when cb_id=2 share>={warn_pct}%, else ok (clears prior warn). "
            "Too few samples → keep previous sticky. "
            "cb_id=0 ignored. cb_id is a rotating Prime suggestion id (proxy)."
        ),
    },
    "by_address": by_address,
    "stats": {
        "addrs": len(by_address),
        "ok": sum(1 for v in by_address.values() if v.get("status") == "ok"),
        "warn": sum(1 for v in by_address.values() if v.get("status") == "warn"),
        "unknown": sum(1 for v in by_address.values() if v.get("status") == "unknown"),
        "log_lines": len(log),
        "touched_this_run": len(counts),
        "flipped_to_ok": len(flipped_ok),
        "flipped_to_warn": len(flipped_warn),
        "held_sticky_low_samples": len(held),
        "warn_pct": warn_pct,
        "min_samples": min_samples,
        "since": since_label,
    },
}
Path("/tmp/cb_type_status_v1.json").write_text(json.dumps(out, indent=2))
print(json.dumps(out["stats"]))
print("flipped_to_ok_sample", flipped_ok[:8])
print("flipped_to_warn_sample", flipped_warn[:8])
PY

python3 <<'PY'
import json
from pathlib import Path
raw = Path("/tmp/cb_type_status_v1.json").read_text()
json.loads(raw)
sql = (
    "INSERT INTO meta(key, value) VALUES ("
    "'cb_type_status_v1', $cb$" + raw + "$cb$"
    ") ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value;\n"
    "SELECT left(value::json->>'updated_at',30), value::json->'stats' FROM meta WHERE key='cb_type_status_v1';\n"
)
Path("/tmp/ins_cb_type.sql").write_text(sql)
PY
docker cp /tmp/ins_cb_type.sql "$PG":/tmp/ins_cb_type.sql
docker exec "$PG" psql -U tides -d tides -v ON_ERROR_STOP=1 -f /tmp/ins_cb_type.sql
echo HOURLY_CB_TYPE_OK
