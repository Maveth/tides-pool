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
MODE = (os.environ.get("LAB_SNAP_MODE") or "all").strip().lower()  # pool|users|all

ENDPOINTS = [
    ("/api/stats", "stats.json"),
    ("/api/info", "info.json"),
    ("/api/coinbaser", "coinbaser.json"),
    ("/api/blocks?limit=100", "blocks.json"),
    ("/api/contributors?limit=500", "contributors.json"),
    # All pool chart ranges the UI offers (must not collapse 1h/window→7d).
    ("/api/charts/pool?range=1h", "charts_pool_1h.json"),
    ("/api/charts/pool?range=24h", "charts_pool_24h.json"),
    ("/api/charts/pool?range=7d", "charts_pool_7d.json"),
    ("/api/charts/pool?range=window", "charts_pool_window.json"),
    ("/api/health", "health.json"),
]

# Per-miner chart ranges (snapshotted under users/<addr>/)
CHART_RANGES = ("1h", "24h", "7d", "window")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fetch(path: str, *, timeout: float | None = None) -> object:
    url = BASE + path
    # Live chart builds (esp. 1h / window) can be slow; give them headroom.
    if timeout is None:
        timeout = 180.0 if "/charts/" in path else 90.0
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
    mode = MODE if MODE in ("pool", "users", "all") else "all"
    print(f"build_snapshots mode={mode}", flush=True)

    loaded: dict[str, object] = {}
    addrs: list[str] = []
    user_ok = 0
    user_fail = 0

    # --- pool-level (main page) ---
    if mode in ("pool", "all"):
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

        # SV1 bad-auth warning list (invalid stratum usernames). Best-effort.
        try:
            from lab_web.sv1_bad_auth import write_bad_auth_snaps

            static_data = Path(os.environ.get("LAB_STATIC_DIR", "/app/static")) / "data"
            bad = write_bad_auth_snaps(SNAP_DIR, static_data)
            nbad = int(bad.get("count") or 0) if bad.get("ok") else 0
            ok.append(f"sv1_bad_auth:{nbad}")
            if bad.get("ok"):
                print(f"ok sv1_bad_auth -> {nbad} unknown username(s)", flush=True)
            else:
                print(f"WARN sv1_bad_auth: {bad.get('error')}", flush=True)
                fail.append(f"sv1_bad_auth:{bad.get('error')}")
        except Exception as exc:  # noqa: BLE001
            fail.append(f"sv1_bad_auth:{exc}")
            print(f"WARN sv1_bad_auth: {exc}", flush=True)

    # --- per-miner pages ---
    if mode in ("users", "all"):
        if not loaded:
            # users-only: reuse last pool snap on disk for address list
            for name in ("contributors.json", "coinbaser.json"):
                fp = SNAP_DIR / name
                if fp.is_file():
                    try:
                        loaded[name] = json.loads(fp.read_text(encoding="utf-8"))
                    except Exception as exc:  # noqa: BLE001
                        print(f"WARN load {name}: {exc}", flush=True)
        addrs = _addrs_from_pool(
            loaded.get("contributors.json"),
            loaded.get("coinbaser.json"),
        )
        print(
            f"user snaps: {len(addrs)} addrs concurrency={USER_CONCURRENCY}",
            flush=True,
        )
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

    # meta.as_of: pool runs drive the main-page freshness badge
    prev_meta = {}
    meta_path = SNAP_DIR / "meta.json"
    if mode == "users" and meta_path.is_file():
        try:
            prev_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            prev_meta = {}
    meta_as_of = as_of if mode in ("pool", "all") else (prev_meta.get("as_of") or as_of)
    meta = {
        "as_of": meta_as_of,
        "users_as_of": as_of if mode in ("users", "all") else prev_meta.get("users_as_of"),
        "builder": "lab_web.build_snapshots",
        "source": BASE,
        "ok": ok if mode != "users" else (prev_meta.get("ok") or []) + ok,
        "fail": fail,
        "mode": f"verbatim-live-api/{mode}",
        "users": {
            "count": len(addrs) if mode in ("users", "all") else (prev_meta.get("users") or {}).get("count", 0),
            "ok": user_ok if mode in ("users", "all") else (prev_meta.get("users") or {}).get("ok", 0),
            "fail": user_fail if mode in ("users", "all") else (prev_meta.get("users") or {}).get("fail", 0),
        },
    }
    _write(meta_path, meta)
    print(
        f"done mode={mode} as_of={meta_as_of} ok={len(ok)} fail={len(fail)} users_ok={user_ok}",
        flush=True,
    )
    if mode == "users":
        return 0 if user_fail == 0 or user_ok > 0 else 1
    pool_fail = [f for f in fail if not f.startswith("user/") and not f.startswith("users_fail")]
    return 0 if not pool_fail else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print("FAILED", exc, file=sys.stderr, flush=True)
        raise
