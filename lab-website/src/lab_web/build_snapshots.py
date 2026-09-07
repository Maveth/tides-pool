"""Snapshot live tides-web API JSON verbatim — lab serves the same shapes.

When this stack is ready, the same snapshot/cache approach can move into
production tides-web with almost no UI change.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

SNAP_DIR = Path(os.environ.get("LAB_SNAP_DIR", "/app/snapshots"))
BASE = os.environ.get("LAB_LIVE_WEB", "http://deploy-tides-web-1:8080").rstrip("/")
USER_CONCURRENCY = int(os.environ.get("LAB_USER_CONCURRENCY", "6"))
USER_SHARE_LIMIT = int(os.environ.get("LAB_USER_SHARE_LIMIT", "200"))
USER_PAYOUT_LIMIT = int(os.environ.get("LAB_USER_PAYOUT_LIMIT", "100"))
# Cap how many miner pages we pre-warm (window contributors first).
USER_ADDR_LIMIT = int(os.environ.get("LAB_USER_ADDR_LIMIT", "500"))

ENDPOINTS = [
    ("/api/stats", "stats.json"),
    ("/api/info", "info.json"),
    ("/api/coinbaser", "coinbaser.json"),
    ("/api/blocks?limit=100", "blocks.json"),
    ("/api/contributors?limit=500", "contributors.json"),
    ("/api/charts/pool?range=24h", "charts_pool_24h.json"),
    ("/api/charts/pool?range=7d", "charts_pool_7d.json"),
    ("/api/health", "health.json"),
]

CHART_RANGES = ("1h", "24h", "7d", "window")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch(path: str, *, timeout: float = 90.0) -> object:
    url = BASE + path
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _safe_addr(addr: str) -> str:
    # Keep filesystem / URL-safe; live addrs are bc1… / 1… / 3…
    a = (addr or "").strip()
    if not a or ".." in a or "/" in a or "\\" in a:
        raise ValueError(f"bad address: {addr!r}")
    return a


def _addrs_from_pool(contrib: object, coinbaser: object) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(a: object) -> None:
        if not isinstance(a, str):
            return
        a = a.strip()
        if not a or a in seen:
            return
        seen.add(a)
        out.append(a)

    if isinstance(contrib, list):
        for row in contrib:
            if isinstance(row, dict):
                add(row.get("address"))
    if isinstance(coinbaser, dict):
        for o in coinbaser.get("outputs") or []:
            if isinstance(o, dict):
                add(o.get("address"))
    return out[:USER_ADDR_LIMIT]


def _snap_one_user(addr: str) -> tuple[str, list[str], list[str]]:
    """Returns (addr, ok_files, fail_msgs)."""
    ok: list[str] = []
    fail: list[str] = []
    try:
        safe = _safe_addr(addr)
    except ValueError as exc:
        return addr, [], [str(exc)]

    udir = SNAP_DIR / "users" / safe
    enc = urllib.parse.quote(safe, safe="")

    jobs = [
        (f"/api/user/{enc}", udir / "user.json"),
        (
            f"/api/user/{enc}/payouts?limit={USER_PAYOUT_LIMIT}",
            udir / "payouts.json",
        ),
        (
            f"/api/user/{enc}/shares?limit={USER_SHARE_LIMIT}&offset=0",
            udir / "shares.json",
        ),
    ]
    for rng in CHART_RANGES:
        jobs.append(
            (
                f"/api/user/{enc}/charts?range={urllib.parse.quote(rng)}",
                udir / f"charts_{rng}.json",
            )
        )

    for path, dest in jobs:
        try:
            data = _fetch(path, timeout=120.0)
            _write(dest, data)
            ok.append(dest.name)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            fail.append(f"{dest.name}:{exc}")
    return safe, ok, fail


def main() -> int:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    as_of = _utc()
    ok: list[str] = []
    fail: list[str] = []

    # --- pool-level ---
    loaded: dict[str, object] = {}
    for path, name in ENDPOINTS:
        try:
            data = _fetch(path)
            _write(SNAP_DIR / name, data)
            loaded[name] = data
            n = (
                len(data)
                if isinstance(data, list)
                else (
                    len(data.get("outputs") or [])
                    if isinstance(data, dict)
                    else "?"
                )
            )
            ok.append(f"{name}:{n}")
            print(f"ok {path} -> {name}", flush=True)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            fail.append(f"{name}:{exc}")
            print(f"FAIL {path}: {exc}", flush=True)

    # --- per-miner pages (dress rehearsal: same shapes as live) ---
    addrs = _addrs_from_pool(
        loaded.get("contributors.json"),
        loaded.get("coinbaser.json"),
    )
    print(
        f"user snaps: {len(addrs)} addrs concurrency={USER_CONCURRENCY}",
        flush=True,
    )
    user_ok = 0
    user_fail = 0
    if addrs:
        with ThreadPoolExecutor(max_workers=max(1, USER_CONCURRENCY)) as pool:
            futs = {pool.submit(_snap_one_user, a): a for a in addrs}
            for fut in as_completed(futs):
                addr, uok, ufail = fut.result()
                if uok and not ufail:
                    user_ok += 1
                    print(f"ok user {addr} ({len(uok)} files)", flush=True)
                elif uok and ufail:
                    user_ok += 1
                    user_fail += 1
                    print(f"PARTIAL user {addr} ok={uok} fail={ufail}", flush=True)
                    fail.extend(f"user/{addr}/{x}" for x in ufail)
                else:
                    user_fail += 1
                    print(f"FAIL user {addr}: {ufail}", flush=True)
                    fail.extend(f"user/{addr}/{x}" for x in ufail)

    ok.append(f"users_ok:{user_ok}")
    if user_fail:
        fail.append(f"users_fail:{user_fail}")

    index = {
        "as_of": as_of,
        "addresses": addrs,
        "count": len(addrs),
        "chart_ranges": list(CHART_RANGES),
        "share_limit": USER_SHARE_LIMIT,
        "payout_limit": USER_PAYOUT_LIMIT,
    }
    _write(SNAP_DIR / "users_index.json", index)

    meta = {
        "as_of": as_of,
        "builder": "lab_web.build_snapshots",
        "source": BASE,
        "ok": ok,
        "fail": fail,
        "mode": "verbatim-live-api",
        "users": {"count": len(addrs), "ok": user_ok, "fail": user_fail},
    }
    _write(SNAP_DIR / "meta.json", meta)
    print(
        f"done as_of={as_of} ok={len(ok)} fail={len(fail)} users_ok={user_ok}",
        flush=True,
    )
    # Soft-fail: pool snaps must succeed; partial user snaps still usable.
    pool_fail = [f for f in fail if not f.startswith("user/") and not f.startswith("users_fail")]
    return 0 if not pool_fail else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print("FAILED", exc, file=sys.stderr, flush=True)
        raise
