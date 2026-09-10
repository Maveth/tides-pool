"""Public RIPTIDE UI: snap-first paint + background live refresh.

Request path is always local (snapshot files / in-memory live cache) so the
dash paints like the lab test — never waits on tides-web during a browser hit.

Background thread pulls live tides-web into memory (~every 10s):
  - /api/stats — top cards
  - /api/blocks — recent finds (+ manual_adjustment overlay)
  - /api/coinbaser — suggested split
Snapshots (≈5 min, or immediately on new pool find):
  - contributors (work / this-block / % Blocks w Shares), charts, miner pages
Live overlays (DB meta, short TTL, also cached):
  - contributors: cb_type_* (gateway class)
  - blocks: manual_adjustment (LISTED_ONLY table)
POST /api/snap/refresh — ask snapshot-refresher to rebuild now.
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
# How often the background thread refreshes live cards/blocks/coinbaser.
LIVE_BG_INTERVAL = float(os.environ.get("LAB_LIVE_BG_INTERVAL_SEC", "10"))

_ADDR_RE = re.compile(r"^[a-zA-Z0-9]{8,128}$")

app = FastAPI(title="RIPTIDE lab-website", version="0.4.2")

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

_live_lock = threading.Lock()
_cb_type_cache: tuple[float, dict[str, dict]] = (0.0, {})
_CB_TYPE_TTL = float(os.environ.get("LAB_CB_TYPE_TTL_SEC", "15"))
# In-memory live payloads (filled by background thread). Request handlers
# never block on HTTP — they return these or fall back to snap files.
_live_payloads: dict[str, Any] = {
    "stats": None,
    "coinbaser": None,
    "blocks": None,  # list already overlay-applied
    "as_of": 0.0,
    "last_err": None,
}
_bg_started = False
_bg_lock = threading.Lock()


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


def _cached_live(key: str) -> Any | None:
    with _live_lock:
        return _live_payloads.get(key)


def _set_live(key: str, value: Any) -> None:
    with _live_lock:
        _live_payloads[key] = value
        _live_payloads["as_of"] = time.time()


def _refresh_live_once() -> None:
    """Pull live tides-web into memory. Safe to call from a background thread."""
    if not LIVE_WEB:
        return
    err: str | None = None
    try:
        stats = _fetch_live_json("/api/stats")
        if isinstance(stats, dict) and stats:
            _set_live("stats", stats)
    except Exception as e:  # noqa: BLE001 — keep loop alive
        err = f"stats: {e}"
    try:
        cb = _fetch_live_json(
            "/api/coinbaser", timeout=max(LIVE_HTTP_TIMEOUT, 12.0)
        )
        if cb is not None:
            _set_live("coinbaser", cb)
    except Exception as e:  # noqa: BLE001
        err = f"coinbaser: {e}"
    try:
        blocks = _fetch_live_json("/api/blocks?limit=100", timeout=max(LIVE_HTTP_TIMEOUT, 15.0))
        if isinstance(blocks, list) and blocks:
            _set_live("blocks", _overlay_manual_adjustments(blocks))
    except Exception as e:  # noqa: BLE001
        err = f"blocks: {e}"
    # Warm cb_type cache so contributor overlays stay fast.
    try:
        _live_cb_type_by_address()
    except Exception as e:  # noqa: BLE001
        err = f"cb_type: {e}"
    with _live_lock:
        _live_payloads["last_err"] = err


def _live_bg_loop() -> None:
    # First pull ASAP so cold start still upgrades quickly after snap paint.
    while True:
        try:
            _refresh_live_once()
        except Exception as e:  # noqa: BLE001
            with _live_lock:
                _live_payloads["last_err"] = str(e)
        time.sleep(max(3.0, LIVE_BG_INTERVAL))


def _ensure_live_bg() -> None:
    global _bg_started
    if not LIVE_WEB:
        return
    with _bg_lock:
        if _bg_started:
            return
        t = threading.Thread(target=_live_bg_loop, name="live-bg", daemon=True)
        t.start()
        _bg_started = True


@app.on_event("startup")
def _on_startup() -> None:
    _ensure_live_bg()


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


def _db_fetch_blocks_payout_fields(heights: list[int]) -> dict[int, dict[str, Any]]:
    """height → {status, payout_mode, manual_payout_done, manual_payout_note}."""
    if not DATABASE_URL or not heights:
        return {}
    try:
        import psycopg
    except ImportError:
        return {}
    out: dict[int, dict[str, Any]] = {}
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT height, status, payout_mode, manual_payout_done, "
                    "manual_payout_note FROM blocks WHERE height = ANY(%s)",
                    (list(heights),),
                )
                for h, st, mode, done, note in cur.fetchall():
                    out[int(h)] = {
                        "status": st,
                        "payout_mode": mode,
                        "manual_payout_done": bool(done),
                        "manual_payout_note": note,
                    }
    except Exception:
        return {}
    return out


def _overlay_user_payout_block_status(rows: list[Any]) -> list[Any]:
    """Miner payout history: same block payout badges as Recent pool blocks."""
    heights: list[int] = []
    for row in rows:
        if isinstance(row, dict) and row.get("height") is not None:
            try:
                heights.append(int(row["height"]))
            except (TypeError, ValueError):
                pass
    if not heights:
        return rows
    by = _db_fetch_blocks_payout_fields(heights)
    meta = _db_fetch_meta_keys([f"manual_adjustment_{h}" for h in heights])
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
        info = by.get(h)
        if info:
            # Keep finder "unpaid" — that is bonus state, not block chain status.
            if not (r.get("kind") == "finder" and r.get("status") == "unpaid"):
                if info.get("status"):
                    r["status"] = info["status"]
            r["payout_mode"] = info.get("payout_mode") or r.get("payout_mode")
            r["manual_payout_done"] = bool(info.get("manual_payout_done"))
            if info.get("manual_payout_note") is not None:
                r["manual_payout_note"] = info.get("manual_payout_note")
        adj = meta.get(f"manual_adjustment_{h}")
        if adj is not None:
            r["manual_adjustment"] = adj
        out.append(r)
    return out


@app.get("/health")
@app.get("/api/health")
def health() -> Any:
    _ensure_live_bg()
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
        with _live_lock:
            live_as_of = _live_payloads.get("as_of") or 0.0
            has_stats = _live_payloads.get("stats") is not None
            has_cb = _live_payloads.get("coinbaser") is not None
            has_blocks = _live_payloads.get("blocks") is not None
            last_err = _live_payloads.get("last_err")
        age = (time.time() - float(live_as_of)) if live_as_of else None
        h["live_overlays"] = {
            "mode": "snap_first_bg_live",
            "cb_type": bool(DATABASE_URL),
            "manual_adjustment": bool(DATABASE_URL),
            "blocks_live": bool(LIVE_WEB),
            "stats_live": bool(LIVE_WEB),
            "coinbaser_live": bool(LIVE_WEB),
            "cb_type_ttl_sec": _CB_TYPE_TTL,
            "live_bg_interval_sec": LIVE_BG_INTERVAL,
            "live_cache_age_sec": round(age, 1) if age is not None else None,
            "live_cache_stats": has_stats,
            "live_cache_coinbaser": has_cb,
            "live_cache_blocks": has_blocks,
            "live_cache_err": last_err,
            "live_web": LIVE_WEB or None,
        }
    return h


@app.get("/api/meta")
def api_meta() -> Any:
    return _load("meta.json")


@app.post("/api/snap/refresh")
def api_snap_refresh() -> Any:
    """Ask snapshot-refresher to rebuild now (e.g. after a new pool find)."""
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    marker = SNAP_DIR / "trigger_refresh"
    marker.write_text(f"{time.time()}\n", encoding="utf-8")
    # Also nudge live cache so cards/blocks catch a new find quickly.
    threading.Thread(target=_refresh_live_once, name="live-nudge", daemon=True).start()
    return {
        "ok": True,
        "trigger": str(marker),
        "note": "snapshot-refresher will rebuild within find_poll (~15s); live cache nudged",
    }


@app.get("/api/stats")
def api_stats() -> Any:
    """Top cards — in-memory live cache if warm, else snapshot (never blocks on HTTP)."""
    _ensure_live_bg()
    live = _cached_live("stats")
    if isinstance(live, dict) and live:
        return _json_response(live, max_age=5)
    return _json_response(_load("stats.json"), max_age=30)


@app.get("/api/info")
def api_info() -> Any:
    return _json_response(_load("info.json"))


@app.get("/api/coinbaser")
def api_coinbaser() -> Any:
    """Suggested split — live cache if warm, else snapshot (instant)."""
    _ensure_live_bg()
    live = _cached_live("coinbaser")
    if live is not None:
        return _json_response(live, max_age=5)
    return _json_response(_load("coinbaser.json"))


@app.get("/api/blocks")
def api_blocks(limit: int = Query(8, ge=1, le=200)) -> Any:
    """Recent finds — live cache if warm, else snapshot + overlay (instant)."""
    _ensure_live_bg()
    live = _cached_live("blocks")
    if isinstance(live, list) and live:
        return _json_response(live[: int(limit)], max_age=5)
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
    """Serve snapshotted pool charts only (live chart builds are slow)."""
    rng = (range or "24h").strip().lower()
    if rng in ("1w",):
        rng = "7d"
    if rng in ("pw", "payout", "inwindow", "in_window"):
        rng = "window"
    if rng not in ("1h", "24h", "1d", "7d", "window"):
        rng = "24h"
    if rng == "1d":
        rng = "24h"
    key = f"charts_pool_{rng}.json"
    try:
        return _json_response(_load(key), max_age=60)
    except HTTPException:
        # Older snaps may lack 1h/window — never silently serve the wrong span.
        raise HTTPException(
            status_code=503,
            detail=f"chart snapshot missing: {key} — wait for snapshot-builder",
        )


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
    rows = _overlay_user_payout_block_status(rows[: int(limit)])
    # Short TTL — manual/pending badges should not lag a paid sendmany.
    return _json_response(rows, max_age=15)

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
