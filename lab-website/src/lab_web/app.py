"""Public/lab RIPTIDE UI: mostly snapshot-backed APIs + a few live overlays.

Snapshots (≈5 min): coinbaser, contrib work, charts, miner pages, stats base.
Live (short timeout, snap fallback):
  - /api/blocks — new finds must show immediately
  - /api/stats — last_pool_block_* / find counters overlaid from live
Live overlays (DB meta, short TTL):
  - contributors: cb_type_status / cb_type_tip (gateway coinbase class ✓/⚠/?)
  - blocks: manual_adjustment (LISTED_ONLY payout table)
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

SNAP_DIR = Path(os.environ.get("LAB_SNAP_DIR", "/app/snapshots"))
STATIC_DIR = Path(os.environ.get("LAB_STATIC_DIR", "/app/static"))
DATABASE_URL = os.environ.get("TIDES_DATABASE_URL", "").strip()
LIVE_WEB = os.environ.get("LAB_LIVE_WEB", "http://deploy-tides-web-1:8080").rstrip("/")
LIVE_HTTP_TIMEOUT = float(os.environ.get("LAB_LIVE_HTTP_TIMEOUT_SEC", "6"))

_ADDR_RE = re.compile(r"^[a-zA-Z0-9]{8,128}$")

app = FastAPI(title="RIPTIDE lab-website", version="0.4.1")

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_live_lock = threading.Lock()
_cb_type_cache: tuple[float, dict[str, dict]] = (0.0, {})
_CB_TYPE_TTL = float(os.environ.get("LAB_CB_TYPE_TTL_SEC", "15"))


def _load(name: str) -> Any:
    path = SNAP_DIR / name
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"snapshot missing: {name} — run snapshot-builder",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _json_response(data: Any, *, max_age: int = 30) -> Response:
    body = json.dumps(data, separators=(",", ":"), default=str)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Cache-Control": f"public, max-age={max_age}"},
    )


def _check_addr(address: str) -> str:
    a = (address or "").strip()
    if not _ADDR_RE.match(a):
        raise HTTPException(status_code=400, detail="invalid address")
    return a


def _user_dir(address: str) -> Path:
    return SNAP_DIR / "users" / _check_addr(address)


def _load_user_file(address: str, name: str) -> Any:
    path = _user_dir(address) / name
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail=f"no snapshot for user {address} ({name}) — re-run snapshot-builder",
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_live_json(path: str, *, timeout: float | None = None) -> Any | None:
    """Best-effort live tides-web JSON. None on any failure (use snap)."""
    if not LIVE_WEB:
        return None
    url = LIVE_WEB + path
    try:
        with urllib.request.urlopen(url, timeout=timeout or LIVE_HTTP_TIMEOUT) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


_STATS_FIND_KEYS = (
    "last_pool_block_height",
    "last_pool_block_at",
    "last_pool_block_age_sec",
    "blocks_last_24h",
    "blocks_last_7d",
    "chain_height",
)


def _db_fetch_meta(key: str) -> Any | None:
    if not DATABASE_URL:
        return None
    try:
        import psycopg
    except ImportError:
        return None
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM meta WHERE key = %s", (key,))
                row = cur.fetchone()
        if not row:
            return None
        val = row[0]
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str):
            return json.loads(val)
        # jsonb may already be adapted
        return val
    except Exception:
        return None


def _db_fetch_meta_keys(keys: list[str]) -> dict[str, Any]:
    if not DATABASE_URL or not keys:
        return {}
    try:
        import psycopg
    except ImportError:
        return {}
    out: dict[str, Any] = {}
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT key, value FROM meta WHERE key = ANY(%s)",
                    (keys,),
                )
                for key, val in cur.fetchall():
                    if isinstance(val, (dict, list)):
                        out[str(key)] = val
                    elif isinstance(val, str):
                        try:
                            out[str(key)] = json.loads(val)
                        except json.JSONDecodeError:
                            continue
                    else:
                        out[str(key)] = val
    except Exception:
        return {}
    return out


def _live_cb_type_by_address() -> dict[str, dict]:
    """Gateway coinbase-class map — live from meta, short TTL (not 5‑min snap)."""
    global _cb_type_cache
    now = time.time()
    with _live_lock:
        ts, cached = _cb_type_cache
        if cached and (now - ts) < _CB_TYPE_TTL:
            return cached
    blob = _db_fetch_meta("cb_type_status_v1")
    by: dict[str, dict] = {}
    if isinstance(blob, dict):
        raw = blob.get("by_address") or {}
        if isinstance(raw, dict):
            for addr, st in raw.items():
                if isinstance(st, dict):
                    by[str(addr)] = st
    with _live_lock:
        _cb_type_cache = (now, by)
    return by


def _overlay_cb_type(rows: list[Any]) -> list[Any]:
    by = _live_cb_type_by_address()
    if not by:
        return rows
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        r = dict(row)
        addr = str(r.get("address") or "")
        st = by.get(addr)
        if isinstance(st, dict):
            r["cb_type_status"] = st.get("status")
            r["cb_type_tip"] = st.get("tip")
        out.append(r)
    return out


def _overlay_manual_adjustments(rows: list[Any]) -> list[Any]:
    """Attach live manual_adjustment from meta so payout tables work on snap site."""
    heights: list[int] = []
    for row in rows:
        if isinstance(row, dict) and row.get("height") is not None:
            try:
                heights.append(int(row["height"]))
            except (TypeError, ValueError):
                pass
    if not heights:
        return rows
    keys = [f"manual_adjustment_{h}" for h in heights]
    meta = _db_fetch_meta_keys(keys)
    if not meta:
        return rows
    out: list[Any] = []
    for row in rows:
        if not isinstance(row, dict):
            out.append(row)
            continue
        r = dict(row)
        try:
            h = int(r["height"])
        except (TypeError, ValueError, KeyError):
            out.append(r)
            continue
        adj = meta.get(f"manual_adjustment_{h}")
        if adj is not None:
            r["manual_adjustment"] = adj
        out.append(r)
    return out


@app.get("/health")
@app.get("/api/health")
def health() -> Any:
    try:
        h = _load("health.json")
    except HTTPException:
        h = {"status": "degraded", "checks": {}}
    if isinstance(h, dict):
        h = dict(h)
        h["lab_website"] = True
        try:
            h["snapshot_as_of"] = _load("meta.json").get("as_of")
        except HTTPException:
            pass
        h["live_overlays"] = {
            "cb_type": bool(DATABASE_URL),
            "manual_adjustment": bool(DATABASE_URL),
            "blocks_live": bool(LIVE_WEB),
            "stats_find_live": bool(LIVE_WEB),
            "cb_type_ttl_sec": _CB_TYPE_TTL,
            "live_web": LIVE_WEB or None,
        }
    return h


@app.get("/api/meta")
def api_meta() -> Any:
    return _load("meta.json")


@app.get("/api/stats")
def api_stats() -> Any:
    snap = _load("stats.json")
    if not isinstance(snap, dict):
        return _json_response(snap, max_age=15)
    live = _fetch_live_json("/api/stats")
    if isinstance(live, dict):
        out = dict(snap)
        for k in _STATS_FIND_KEYS:
            if k in live:
                out[k] = live[k]
        # hashrate / window fill also feel stale after a find — prefer live when present
        for k in (
            "hashrate_hs",
            "hashrate_hs_1h",
            "window_work_filled",
            "window_work_target",
            "addresses_in_window",
            "pool_network_share_pct",
            "est_block_time_sec",
            "network_hashrate_hs",
        ):
            if k in live:
                out[k] = live[k]
        return _json_response(out, max_age=5)
    return _json_response(snap, max_age=30)


@app.get("/api/info")
def api_info() -> Any:
    return _json_response(_load("info.json"))


@app.get("/api/coinbaser")
def api_coinbaser() -> Any:
    # Prefer live after finds so "if we find now" split matches tip
    live = _fetch_live_json("/api/coinbaser", timeout=max(LIVE_HTTP_TIMEOUT, 12.0))
    if live is not None:
        return _json_response(live, max_age=5)
    return _json_response(_load("coinbaser.json"))


@app.get("/api/blocks")
def api_blocks(limit: int = Query(8, ge=1, le=200)) -> Any:
    # New finds must not wait for the 5-min snapper
    live = _fetch_live_json(f"/api/blocks?limit={int(limit)}")
    if isinstance(live, list) and live:
        rows = _overlay_manual_adjustments(live)
        return _json_response(rows, max_age=5)
    rows = _load("blocks.json")
    if not isinstance(rows, list):
        rows = rows.get("rows") if isinstance(rows, dict) else []
    rows = rows[: int(limit)]
    rows = _overlay_manual_adjustments(rows)
    return _json_response(rows, max_age=15)


@app.get("/api/contributors")
def api_contributors(
    response: Response,
    limit: int = Query(10, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Any:
    """Snapshotted work rows; gateway class (cb_type_*) overlaid live from meta."""
    rows = _load("contributors.json")
    if not isinstance(rows, list):
        rows = rows.get("rows") if isinstance(rows, dict) else []
    total = len(rows)
    chunk = rows[int(offset) : int(offset) + int(limit)]
    chunk = _overlay_cb_type(chunk)
    response.headers["X-Total-Count"] = str(total)
    # Don't let browsers cache overlaid live badges for long
    response.headers["Cache-Control"] = "public, max-age=15"
    return chunk


@app.get("/api/charts/pool")
def api_charts_pool(range: str = Query("24h")) -> Any:
    key = "charts_pool_24h.json" if range in ("24h", "1d") else "charts_pool_7d.json"
    if range in ("7d", "1w"):
        key = "charts_pool_7d.json"
    try:
        return _json_response(_load(key))
    except HTTPException:
        return _json_response(_load("charts_pool_24h.json"))


@app.get("/api/user/{address}")
def api_user(address: str) -> Any:
    return _json_response(_load_user_file(address, "user.json"))


@app.get("/api/user/{address}/payouts")
def api_user_payouts(
    address: str,
    limit: int = Query(100, ge=1, le=500),
) -> Any:
    rows = _load_user_file(address, "payouts.json")
    if not isinstance(rows, list):
        rows = []
    return _json_response(rows[: int(limit)])


@app.get("/api/user/{address}/shares")
def api_user_shares(
    address: str,
    limit: int = Query(25, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Any:
    rows = _load_user_file(address, "shares.json")
    if not isinstance(rows, list):
        rows = []
    start = int(offset)
    end = start + int(limit)
    return _json_response(rows[start:end])


@app.get("/api/user/{address}/charts")
def api_user_charts(
    address: str,
    range: str = Query("24h"),
) -> Any:
    rng = (range or "24h").strip()
    if rng in ("1w",):
        rng = "7d"
    if rng not in ("1h", "24h", "7d", "window"):
        rng = "24h"
    try:
        return _json_response(_load_user_file(address, f"charts_{rng}.json"))
    except HTTPException:
        if rng != "24h":
            return _json_response(_load_user_file(address, "charts_24h.json"))
        raise


@app.get("/")
def index() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h1>lab-website</h1><p>missing static/index.html</p>")
    html = index_path.read_text(encoding="utf-8")
    show_banner = os.environ.get("LAB_SHOW_BANNER", "0").strip() in ("1", "true", "yes")
    if show_banner and "lab-banner" not in html:
        html = html.replace(
            "<body>",
            '<body>\n  <div class="lab-banner" style="background:#1a3a4a;color:#9ad;text-align:center;padding:0.35rem;font-size:0.85rem">'
            "LAB snapshot dash (~5 min) · "
            'live APIs <a href="http://192.168.0.143:8087/" style="color:#4dd0e1">:8087</a>'
            "</div>\n",
            1,
        )
    return HTMLResponse(html)


@app.get("/blocks")
@app.get("/address")
def spa_routes() -> HTMLResponse:
    return index()
