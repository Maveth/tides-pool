from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from tides_pool import __version__
from tides_pool.bitcoin_rpc import BitcoinRPC, BitcoinRPCError
from tides_pool.chain_sync import chain_sync_loop, sync_once
from tides_pool.config import Settings, finder_credit_bps, miner_reward_bps
from tides_pool.datum_prime import start_datum_prime

# Diff1 hashes (same convention as share / pool hashrate math). Never expose RPC.
_DIFF1_HASHES = float(1 << 32)

from tides_pool.models import (
    BlockOut,
    CoinbaseOutput,
    WorkerBreak,
    CoinbaserResponse,
    Contributor,
    HealthResponse,
    PoolStats,
    ShareOut,
    UserPayoutOut,
    UserStats,
)
from tides_pool.store import (
    MemoryStore,
    PostgresStore,
    Store,
    contributor_rows,
    estimate_hashrate_hs,
)
from tides_pool.tides import coinbase_suggestion, split_reward, window_since_seq, window_size

settings = Settings()
STATIC_DIR = Path(__file__).resolve().parent / "static"
log = logging.getLogger("tides_pool.api")

store: Store = MemoryStore()
_stop_sync = asyncio.Event()
_sync_task: asyncio.Task | None = None
_rpc_ok = False
_prime_server = None
_pool_pubkey_hex = ""
_coinbaser_cache = None  # CoinbaserSplitCache | None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store, _sync_task, _rpc_ok, _prime_server, _pool_pubkey_hex, _coinbaser_cache
    logging.basicConfig(level=logging.INFO)
    role = settings.normalized_role()
    log.info("tides-pool starting role=%s", role)

    dsn = settings.database_url
    if dsn.startswith("postgresql"):
        try:
            pg = PostgresStore(dsn)
            await pg.ensure_ready()
            store = pg
        except Exception as exc:  # noqa: BLE001 — fall back for local/dev
            print(f"postgres unavailable ({exc}); using MemoryStore")
            store = MemoryStore()
            await store.ensure_ready()
    else:
        store = MemoryStore()
        await store.ensure_ready()

    if settings.runs_chain_sync():
        try:
            await sync_once(store, settings)
            _rpc_ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("initial chain sync failed: %s", exc)
            _rpc_ok = False
            if await store.get_meta("block_difficulty") is None:
                await store.set_meta("block_difficulty", "1")
            if await store.get_meta("reward_estimate") is None:
                await store.set_meta("reward_estimate", str(50 * 100_000_000))
    else:
        try:
            _rpc_ok = (await store.get_meta("chain_height")) is not None
        except Exception:  # noqa: BLE001
            _rpc_ok = False

    keys_path = Path(os.environ.get("TIDES_POOL_KEYS_PATH", "/app/data/pool_keys.json"))
    _prime_server = None
    _coinbaser_cache = None
    _pool_pubkey_hex = ""
    if settings.runs_prime():
        try:
            _prime_server, keys, _coinbaser_cache = await start_datum_prime(
                settings, store, keys_path
            )
            _pool_pubkey_hex = keys.pubkey_hex
        except Exception as exc:  # noqa: BLE001
            log.exception("DATUM Prime failed to start: %s", exc)
            _prime_server = None
            _coinbaser_cache = None
    else:
        # Web role: read pubkey from keys file (field is pool_pubkey) or Prime /api/info.
        try:
            if keys_path.is_file():
                raw = json.loads(keys_path.read_text(encoding="utf-8"))
                _pool_pubkey_hex = str(
                    raw.get("pool_pubkey")
                    or raw.get("pubkey_hex")
                    or raw.get("pubkey")
                    or ""
                ).strip()
        except Exception as exc:  # noqa: BLE001
            log.warning("web role: could not read pool keys for pubkey: %s", exc)
        if not _pool_pubkey_hex:
            try:
                import urllib.request

                # Compose: tides-web → tides-prime:8080 (host maps that to :8089).
                prime_http = int(os.environ.get("TIDES_PRIME_HTTP_PORT", "8080"))
                with urllib.request.urlopen(
                    f"http://{settings.prime_host}:{prime_http}/api/info",
                    timeout=5,
                ) as resp:
                    info = json.loads(resp.read().decode())
                _pool_pubkey_hex = str(info.get("pool_pubkey") or "").strip()
                if _pool_pubkey_hex:
                    log.info("web role: loaded pool_pubkey from prime /api/info")
            except Exception as exc:  # noqa: BLE001
                log.warning("web role: prime pubkey fetch failed: %s", exc)

    _stop_sync.clear()
    _sync_task = None
    if settings.runs_chain_sync():
        _sync_task = asyncio.create_task(chain_sync_loop(store, settings, _stop_sync))
    yield
    _stop_sync.set()
    if _sync_task:
        await _sync_task
    if _prime_server is not None:
        _prime_server.close()
        await _prime_server.wait_closed()
    await store.close()


def _probe_prime_tcp(host: str, port: int, *, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


# Lab/admin HTTP is off by default on live. Set TIDES_ALLOW_LAB_HTTP=1 only on lab.
_ALLOW_LAB_HTTP = os.environ.get("TIDES_ALLOW_LAB_HTTP", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def _require_lab_http() -> None:
    """Gate destructive/dev-only write endpoints. Default deny on production."""
    if not _ALLOW_LAB_HTTP:
        raise HTTPException(403, "lab/admin HTTP disabled")


app = FastAPI(
    title="tides-pool",
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if _ALLOW_LAB_HTTP else None,
    redoc_url="/redoc" if _ALLOW_LAB_HTTP else None,
    openapi_url="/openapi.json" if _ALLOW_LAB_HTTP else None,
)

if STATIC_DIR.is_dir() and settings.normalized_role() != "prime":
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


async def _difficulty() -> int:
    raw = await store.get_meta("block_difficulty", "1") or "1"
    try:
        return max(int(float(raw)), 1)
    except ValueError:
        return 1


async def _difficulty_float() -> float:
    raw = await store.get_meta("block_difficulty", "1") or "1"
    try:
        return max(float(raw), 1.0)
    except ValueError:
        return 1.0


async def _network_hashrate_hs() -> float:
    """Server-side only: Knots getnetworkhashps. Never proxied to clients."""
    try:
        rpc = BitcoinRPC(settings)
        val = await asyncio.to_thread(rpc.call, "getnetworkhashps", [120])
        return float(val or 0)
    except (BitcoinRPCError, TypeError, ValueError) as exc:
        log.warning("network hashrate unavailable: %s", exc)
        return 0.0


async def _reward_estimate() -> int:
    raw = await store.get_meta("reward_estimate", str(50 * 100_000_000)) or "0"
    try:
        return max(int(raw), 0)
    except ValueError:
        return 0


async def _last_pool_find() -> tuple[int | None, datetime | None]:
    """Latest non-orphaned pool find (height, accounted_at) for dashboard cards."""
    for b in await store.list_blocks(limit=30):
        if b.status in ("pending", "confirmed"):
            return b.height, b.accounted_at
    raw = await store.get_meta("last_height")
    if raw is None:
        return None, None
    try:
        return int(raw), None
    except ValueError:
        return None, None


async def _last_height() -> int | None:
    h, _ = await _last_pool_find()
    return h


async def _payout_window():
    """Shares in the live payout window: (N-1) confirmed finds + current.

    Cutoff is the share head of the Nth-last confirmed find; orphans do not count.
    Use uncapped list_shares_after_cutoff — same source Prime coinbaser uses.
    (list_shares_newest(50k) drifted vs coinbaser once the window exceeded 50k rows.)
    """
    cutoff = await store.payout_window_cutoff_seq(settings.window_blocks)
    window = await store.list_shares_after_cutoff(cutoff)
    # Newest-first for callers that expect that order (split_reward does not require it
    # when cutoff_seq is applied separately, but keep consistent with prior API).
    shares = sorted(window, key=lambda s: int(s.seq), reverse=True)
    confirmed = await store.list_confirmed_blocks(limit=settings.window_blocks)
    return shares, window, cutoff, len(confirmed)


async def _blocks_since(hours: float, *, limit: int = 500) -> tuple[int, int]:
    """Return (confirmed_or_pending, orphaned) finds accounted in the last `hours`.

    Orphans are excluded from the main count but returned separately for the UI.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    good = 0
    orphans = 0
    for b in await store.list_blocks(limit=limit):
        if str(b.block_hash).startswith("lab-"):
            continue
        ts = b.accounted_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            # list_blocks is newest-first; older than window → stop
            break
        st = getattr(b, "status", None) or "confirmed"
        if st in ("orphaned", "misattributed"):
            orphans += 1
        else:
            good += 1
    return good, orphans


async def _blocks_last_24h() -> tuple[int, int]:
    return await _blocks_since(24, limit=100)


def _fmt_btc_html(sats: int) -> str:
    """User-facing amounts are always BTC (trim trailing zeros)."""
    n = int(sats or 0)
    btc = n / 1e8
    text = f"{btc:.8f}".rstrip("0").rstrip(".")
    if text == "-0":
        text = "0"
    return f"{text} BTC"


def _short_addr_html(addr: str) -> str:
    if not addr:
        return "—"
    if len(addr) <= 16:
        return addr
    return f"{addr[:8]}…{addr[-6:]}"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    if settings.normalized_role() == "prime":
        return HTMLResponse(
            "<h1>tides-prime</h1><p>DATUM Prime process — UI is tides-web.</p>",
            status_code=200,
        )
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h1>tides-pool</h1><p>static UI missing</p>", status_code=500)
    html = index_path.read_text(encoding="utf-8")
    # Cache-bust UI JS so browsers pick up render fixes.
    # Keep in sync with static/index.html script tag when bumping UI.
    import re as _re
    html = _re.sub(
        r'src="/static/app\.js(?:\?v=[^"]*)?"',
        'src="/static/app.js?v=20260905vis"',
        html,
        count=1,
    )
    html = _re.sub(
        r'href="/static/style\.css(?:\?v=[^"]*)?"',
        'href="/static/style.css?v=20260905vis"',
        html,
        count=1,
    )
    # Server-render coinbaser so payout breakdown is visible even if client JS fails.
    try:
        cb = await _coinbaser_payload()
        outs = cb.outputs or []
        note = (
            f"~{_fmt_btc_html(cb.reward_sats_estimate)} total · {len(outs)} payout line(s) "
            f"· window work {cb.window_work:,}"
            if outs
            else f"No miner lines yet (~{_fmt_btc_html(cb.reward_sats_estimate)}) — empty window pays ops only"
        )
        if outs:
            rows = []
            for o in outs:
                nm = (o.name or "—").replace("<", "&lt;")
                rows.append(
                    "<tr>"
                    f"<td>{o.kind or '—'}</td>"
                    f"<td>{nm}</td>"
                    f'<td class="mono"><a href="/address?a={o.address}" title="{o.address}">'
                    f"{_short_addr_html(o.address)}</a></td>"
                    f"<td title=\"{int(o.sats or 0):,} sats\">{_fmt_btc_html(o.sats)}</td>"
                    "</tr>"
                )
            body = "\n".join(rows)
        else:
            body = '<tr><td colspan="4" class="muted">No coinbaser outputs (empty window → ops only)</td></tr>'
        # Match both empty note and legacy "Loading…" placeholders.
        html = _re.sub(
            r'id="coinbaserNote">[^<]*</p>',
            f'id="coinbaserNote">{note}</p>',
            html,
            count=1,
        )
        html = html.replace(
            '<tbody id="coinbaserBody"><tr><td colspan="3">Loading…</td></tr></tbody>',
            f'<tbody id="coinbaserBody">{body}</tbody>',
            1,
        )
        html = html.replace(
            '<tbody id="coinbaserBody"><tr><td colspan="4">Loading…</td></tr></tbody>',
            f'<tbody id="coinbaserBody">{body}</tbody>',
            1,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("SSR coinbaser failed: %s", exc)
    return HTMLResponse(html)


@app.get("/address", response_class=HTMLResponse)
async def address_page() -> HTMLResponse:
    # same SPA shell; client JS reads ?a=
    return await index()


@app.get("/blocks", response_class=HTMLResponse)
async def blocks_page() -> HTMLResponse:
    # same SPA shell; client JS shows the full blocks list
    return await index()


def _mask_ip(ip: str | None) -> str:
    """Public health helper: keep last octet/hextet only (*.*.*.x / *:*:*:x)."""
    s = (ip or "").strip()
    if not s:
        return ""
    # strip :port if present on IPv4 host:port
    if s.count(":") == 1 and "." in s:
        s = s.split(":", 1)[0]
    if "." in s and ":" not in s:
        parts = s.split(".")
        if len(parts) == 4:
            return f"*.*.*.{parts[3]}"
        return "*.*.*.*"
    if ":" in s:
        # IPv6 — keep last hextet
        core = s.split("%", 1)[0]
        if core.startswith("[") and "]" in core:
            core = core[1 : core.index("]")]
        parts = [p for p in core.split(":") if p != ""]
        last = parts[-1] if parts else "*"
        return f"*:*:*:{last}"
    return "*"


def _mask_gateway_uas(rows: list) -> list:
    out: list = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        item = dict(r)
        if "ip" in item:
            item["ip"] = _mask_ip(str(item.get("ip") or ""))
        out.append(item)
    return out


@app.get("/health", response_model=HealthResponse)
@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Real ops health — not a hard-coded ok.

    down: Prime not listening or DB unreachable
    degraded: RPC bad, stale coinbaser cache, or recent outs≤1 while window active
    ok: otherwise
    """
    warnings: list[str] = []
    checks: dict = {
        "prime_listening": False,
        "gateway_sessions": 0,
        "rpc_ok": bool(_rpc_ok),
        "db_ok": False,
        "coinbaser": {},
        "manual_payouts_pending": 0,
    }
    status = "ok"

    role = settings.normalized_role()
    checks["role"] = role

    if settings.runs_prime():
        prime_ok = bool(
            _prime_server is not None and getattr(_prime_server, "sockets", None)
        )
        checks["prime_listening"] = prime_ok
        checks["prime_probe"] = "in_process"
        if not prime_ok:
            status = "down"
            warnings.append("prime_not_listening")
    else:
        host = (settings.prime_host or "127.0.0.1").strip() or "127.0.0.1"
        prime_ok = await asyncio.to_thread(
            _probe_prime_tcp, host, int(settings.datum_prime_port)
        )
        checks["prime_listening"] = prime_ok
        checks["prime_probe"] = f"tcp:{host}:{settings.datum_prime_port}"
        if not prime_ok:
            if status != "down":
                status = "degraded"
            warnings.append("prime_peer_unreachable")

    # DB
    try:
        await store.max_share_seq()
        checks["db_ok"] = True
        try:
            checks["manual_payouts_pending"] = int(
                await store.count_manual_payouts_pending()
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"manual_payout_count_failed:{exc}")
    except Exception as exc:  # noqa: BLE001
        checks["db_ok"] = False
        status = "down"
        warnings.append(f"db_unreachable:{exc}")

    # Coinbaser cache / reply metrics (local or Prime meta snapshot)
    cb = {}
    if _coinbaser_cache is not None:
        try:
            cb = dict(_coinbaser_cache.health_snapshot())
            checks["gateway_sessions"] = int(cb.get("gateway_sessions") or 0)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"coinbaser_metrics_failed:{exc}")
            cb = {}
    else:
        try:
            raw = await store.get_meta("prime_health_json")
            if raw:
                cb = json.loads(raw)
                checks["gateway_sessions"] = int(cb.get("gateway_sessions") or 0)
                checks["coinbaser_source"] = "meta_snapshot"
            else:
                warnings.append("coinbaser_cache_missing")
                if status != "down" and role == "all":
                    status = "degraded"
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"coinbaser_meta_failed:{exc}")
            if status != "down" and role == "all":
                status = "degraded"
    # Public web helper only — Prime (:8089 / logs) keeps full peer IPs.
    if role != "prime":
        if isinstance(cb.get("gateway_uas"), list):
            cb["gateway_uas"] = _mask_gateway_uas(cb.get("gateway_uas") or [])
    checks["coinbaser"] = cb

    # RPC
    if not checks["rpc_ok"] and status != "down":
        status = "degraded"
        warnings.append("rpc_not_ok")

    # Stale / weak coinbaser
    age = cb.get("cache_age_s")
    ttl = float(cb.get("cache_ttl_s") or 15.0)
    if age is not None and age > max(ttl * 2.5, 45.0):
        if status != "down":
            status = "degraded"
        warnings.append(f"coinbaser_cache_stale:{age}s")
    if cb.get("last_refresh_error"):
        if status != "down":
            status = "degraded"
        warnings.append("coinbaser_refresh_error")

    # Recent ops-only-ish replies while window has work
    outs1 = int(cb.get("outs1_recent") or 0)
    recent_n = int(cb.get("outs_recent_n") or 0)
    last_outs = cb.get("last_outs")
    shares = int(cb.get("cache_shares") or 0)
    if shares > 0 and last_outs is not None and int(last_outs) <= 1:
        if status != "down":
            status = "degraded"
        warnings.append("coinbaser_last_outs_le_1")
    if recent_n >= 10 and outs1 / float(recent_n) >= 0.2:
        if status != "down":
            status = "degraded"
        warnings.append(f"coinbaser_outs1_rate:{outs1}/{recent_n}")

    p99 = cb.get("p99_reply_ms")
    if p99 is not None and float(p99) >= 2000.0:
        if status != "down":
            status = "degraded"
        warnings.append(f"coinbaser_p99_ms:{p99}")

    # pool_pass_full_users misuse: miner username not a bc1… (reject 14)
    bad_1h = int(cb.get("bad_payout_rejects_1h") or 0)
    bad_total = int(cb.get("bad_payout_rejects_total") or 0)
    checks["bad_payout"] = {
        "rejects_total": bad_total,
        "rejects_1h": bad_1h,
        "distinct": int(cb.get("bad_payout_distinct") or 0),
        "top": cb.get("bad_payout_top") or [],
    }
    if role != "prime":
        checks["gateway_uas"] = _mask_gateway_uas(cb.get("gateway_uas") or [])
    else:
        checks["gateway_uas"] = cb.get("gateway_uas") or []
    checks["ua_handshakes_top"] = cb.get("ua_handshakes_top") or []
    checks["ua_reject27_top"] = cb.get("ua_reject27_top") or []
    checks["ua_bad_payout_top"] = cb.get("ua_bad_payout_top") or []
    if bad_1h > 0:
        top = cb.get("bad_payout_top") or []
        sample = ",".join(
            str(x.get("user") or "")[:24] for x in top[:3] if isinstance(x, dict)
        )
        warnings.append(f"bad_payout_username_1h:{bad_1h}:{sample}")
    r27_ua = cb.get("ua_reject27_top") or []
    if r27_ua:
        sample = ",".join(
            f"{x.get('ua','')[:28]}:{x.get('n')}" for x in r27_ua[:3] if isinstance(x, dict)
        )
        warnings.append(f"reject27_by_ua:{sample}")

    if checks["manual_payouts_pending"]:
        warnings.append(f"manual_payouts_pending:{checks['manual_payouts_pending']}")

    # Always 200 so the strip can render degraded without treating it as outage.
    # Use status field for severity; monitors can alert on status!=ok.
    return HealthResponse(
        status=status,
        version=__version__,
        network=settings.network,
        checks=checks,
        warnings=warnings,
    )


@app.get("/stats", response_model=PoolStats)
@app.get("/api/stats", response_model=PoolStats)
async def stats() -> PoolStats:
    diff = await _difficulty()
    _shares, window, cutoff, n_conf = await _payout_window()
    filled = sum(s.work for s in window)
    addrs = {s.address for s in window}
    finder, credit = await store.pending_finder_credit()
    chain_raw = await store.get_meta("chain_height")
    chain_h = int(chain_raw) if chain_raw and chain_raw.isdigit() else None
    hr_window = 600
    recent = await store.list_share_rows_since(hr_window, limit=50_000)
    recent_work = sum(r.work for r in recent)
    # Target work is informational only now (Ocean-sized); fill is find-window work.
    ocean_target = window_size(diff, settings.window_blocks)
    blocks_24h, orphans_24h = await _blocks_since(24, limit=200)
    blocks_7d, orphans_7d = await _blocks_since(24 * 7, limit=500)
    last_h, last_at = await _last_pool_find()
    age_sec: int | None = None
    if last_at is not None:
        ts = last_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_sec = max(0, int((datetime.now(timezone.utc) - ts).total_seconds()))
    pool_hs = estimate_hashrate_hs(recent_work, hr_window)
    diff_f = await _difficulty_float()
    # Derived public metrics only — no raw RPC payloads / no RPC proxy.
    net_hs = await _network_hashrate_hs()
    share_pct = (100.0 * pool_hs / net_hs) if net_hs > 0 and pool_hs > 0 else 0.0
    est_block: float | None = None
    if pool_hs > 0 and diff_f > 0:
        est_block = (diff_f * _DIFF1_HASHES) / pool_hs
    # Window luck: finds after cutoff × network_diff / window_work.
    # Expected blocks ≈ work/diff (Diff1 work units). 100% = expected.
    confirmed_for_luck = await store.list_confirmed_blocks(limit=settings.window_blocks)
    if cutoff is None:
        luck_finds = len(confirmed_for_luck)
    else:
        luck_finds = sum(
            1
            for b in confirmed_for_luck
            if b.share_head_seq is not None and int(b.share_head_seq) > int(cutoff)
        )
    window_luck: float | None = None
    if filled > 0 and diff > 0 and luck_finds > 0:
        window_luck = round(100.0 * luck_finds * float(diff) / float(filled), 2)
    return PoolStats(
        share_log_work=await store.total_work(),
        share_count=await store.share_count(),
        window_work_target=ocean_target,
        window_work_filled=filled,
        addresses_in_window=len(addrs),
        last_pool_block_height=last_h,
        last_pool_block_at=last_at,
        last_pool_block_age_sec=age_sec,
        blocks_last_24h=blocks_24h,
        orphans_last_24h=orphans_24h,
        blocks_last_7d=blocks_7d,
        orphans_last_7d=orphans_7d,
        chain_height=chain_h,
        block_difficulty=diff,
        reward_estimate_sats=await _reward_estimate(),
        pending_finder_address=finder,
        pending_finder_credit_sats=credit,
        fee_bps=settings.fee_bps,
        finder_fee_share_bps=settings.finder_fee_share_bps,
        window_blocks=settings.window_blocks,
        window_mode="pool_finds",
        window_confirmed_finds=n_conf,
        window_cutoff_seq=cutoff,
        window_luck_pct=window_luck,
        window_luck_finds=luck_finds,
        block_confirmations=settings.block_confirmations,
        network=settings.network,
        pool_name=f"{settings.coinbase_tag_primary}/{settings.coinbase_tag_secondary}",
        pool_ops_address=settings.pool_ops_address,
        rpc_ok=_rpc_ok or chain_h is not None,
        address_work_cap=settings.address_work_cap(),
        address_work_cap_window_sec=settings.address_work_cap_window_sec,
        gpu_baseline_hs=settings.gpu_baseline_hashrate_hs,
        hashrate_hs=pool_hs,
        hashrate_window_sec=hr_window,
        hashrate_work=recent_work,
        hashrate_shares=len(recent),
        network_hashrate_hs=net_hs,
        pool_network_share_pct=round(share_pct, 6),
        est_block_time_sec=est_block,
    )


@app.get("/api/contributors", response_model=list[Contributor])
async def contributors(limit: int = Query(50, ge=1, le=500)) -> list[Contributor]:
    _shares, window, cutoff, _n = await _payout_window()
    hr_window = 600
    recent = await store.list_share_rows_since(hr_window, limit=50_000)
    # "This block" = shares after the latest confirmed find's share_head_seq.
    n_win = max(int(settings.window_blocks or 8), 1)
    confirmed = await store.list_confirmed_blocks(limit=n_win)
    current_since = None
    if confirmed and confirmed[0].share_head_seq is not None:
        current_since = int(confirmed[0].share_head_seq)
    # Newest-first (height, share_head_seq) for "last share N ago" labels.
    confirmed_heads = [
        (int(b.height), int(b.share_head_seq))
        for b in confirmed
        if b.share_head_seq is not None
    ]
    rows = contributor_rows(
        window,
        recent=recent,
        hashrate_window_sec=hr_window,
        current_since_seq=current_since,
        confirmed_heads=confirmed_heads,
    )[:limit]
    addrs = [r["address"] for r in rows]
    qmap = await store.list_quarantines(addrs)
    nmap = await store.nicknames_for_addresses(addrs)
    # Finds strictly inside the payout window (share_head after cutoff).
    if cutoff is None:
        in_window_finds = list(confirmed)
    else:
        in_window_finds = [
            b
            for b in confirmed
            if b.share_head_seq is not None and int(b.share_head_seq) > int(cutoff)
        ]
    finds_by_addr: dict[str, int] = {}
    for b in in_window_finds:
        fa = (b.finder_address or "").strip()
        if fa:
            finds_by_addr[fa] = finds_by_addr.get(fa, 0) + 1
    diff = await _difficulty()
    wbreak = await store.worker_breakdown_after_cutoff(
        cutoff, addresses=addrs, recent_sec=hr_window
    )
    sats_by_addr: dict[str, int] = {}
    try:
        cb = await _coinbaser_payload()
        for o in cb.outputs or []:
            if o.address:
                sats_by_addr[o.address] = int(o.sats or 0)
    except Exception as exc:  # noqa: BLE001
        log.warning("contrib coinbaser sats attach failed: %s", exc)
    out: list[Contributor] = []
    for r in rows:
        q = qmap.get(r["address"])
        addr = r["address"]
        n_finds = int(finds_by_addr.get(addr, 0))
        work = int(r.get("work") or 0)
        luck: float | None = None
        if n_finds > 0 and work > 0 and diff > 0:
            luck = round(100.0 * n_finds * float(diff) / float(work), 2)
        workers = _worker_breaks_for_addr(
            addr, wbreak, sats_total=sats_by_addr.get(addr)
        )
        out.append(
            Contributor(
                **r,
                quarantined=bool(q),
                quarantine_reason=(q or {}).get("reason") if q else None,
                nickname=nmap.get(addr),
                luck_pct=luck,
                luck_finds=n_finds,
                workers=workers,
            )
        )
    return out


# --- Charts (Ocean-lite hashrate + finds) ---------------------------------

_CHART_RANGES: dict[str, tuple[int, int]] = {
    # range_key -> (range_sec, bucket_sec)
    "1h": (3600, 60),
    "24h": (86400, 600),
    "7d": (7 * 86400, 3600),
    "1w": (7 * 86400, 3600),
    # "window" is dynamic (payout period) — resolved in _chart_window()
}


def _bucket_for_span(range_sec: int) -> int:
    """Pick a chart bucket so a variable-length payout window stays readable."""
    s = max(int(range_sec), 1)
    if s <= 3 * 3600:
        return 60
    if s <= 24 * 3600:
        return 600
    if s <= 3 * 86400:
        return 1800
    if s <= 14 * 86400:
        return 3600
    return 7200


async def _payout_window_chart_start(end: datetime) -> tuple[datetime, dict | None]:
    """Start time for the live payout window chart range + window_meta stub."""
    n_win = max(int(settings.window_blocks or 8), 1)
    confirmed = await store.list_confirmed_blocks(limit=n_win)
    if len(confirmed) >= n_win:
        cutoff_block = confirmed[n_win - 1]
        newer = confirmed[: n_win - 1]
        cts = cutoff_block.accounted_at
        if cts is None:
            return end - timedelta(seconds=86400), None
        if cts.tzinfo is None:
            cts = cts.replace(tzinfo=timezone.utc)
        meta = {
            "start_t": int(cts.timestamp()),
            "end_t": int(end.timestamp()),
            "cutoff_height": cutoff_block.height,
            "finds_in_window": len(newer),
            "window_blocks": n_win,
            "label": f"{max(n_win - 1, 0)} confirmed + current",
        }
        return cts, meta
    if confirmed:
        oldest = confirmed[-1]
        ots = oldest.accounted_at
        if ots is None:
            return end - timedelta(seconds=86400), None
        if ots.tzinfo is None:
            ots = ots.replace(tzinfo=timezone.utc)
        meta = {
            "start_t": int(ots.timestamp()),
            "end_t": int(end.timestamp()),
            "cutoff_height": None,
            "finds_in_window": len(confirmed),
            "window_blocks": n_win,
            "label": f"{len(confirmed)} finds (building to {n_win})",
        }
        return ots, meta
    return end - timedelta(seconds=86400), None


async def _chart_window(
    range_key: str,
) -> tuple[str, int, int, datetime, datetime, dict | None]:
    """Return (key, range_sec, bucket_sec, start, end, precomputed_window_meta|None)."""
    key = (range_key or "24h").strip().lower()
    end = datetime.now(timezone.utc)
    if key in ("window", "pw", "payout", "inwindow", "in_window"):
        key = "window"
        start, win_meta = await _payout_window_chart_start(end)
        range_sec = max(60, int((end - start).total_seconds()))
        bucket_sec = _bucket_for_span(range_sec)
        return key, range_sec, bucket_sec, start, end, win_meta
    if key not in _CHART_RANGES:
        raise HTTPException(
            status_code=400, detail="range must be 1h, 24h, 7d, or window"
        )
    range_sec, bucket_sec = _CHART_RANGES[key]
    start = end - timedelta(seconds=range_sec)
    return key, range_sec, bucket_sec, start, end, None


def _fill_hs_series(
    buckets: list[tuple[datetime, int]],
    *,
    start: datetime,
    end: datetime,
    bucket_sec: int,
) -> list[dict]:
    """Dense series (zeros for empty buckets) so the chart x-axis is even."""
    bsec = max(int(bucket_sec), 1)
    by_ts = {
        int(ts.timestamp()) // bsec * bsec: int(work)
        for ts, work in buckets
    }
    start_i = int(start.timestamp()) // bsec * bsec
    end_i = int(end.timestamp()) // bsec * bsec
    out: list[dict] = []
    t = start_i
    while t < end_i:
        work = by_ts.get(t, 0)
        out.append(
            {
                "t": t,
                "hs": estimate_hashrate_hs(work, bsec),
            }
        )
        t += bsec
    return out


async def _network_hs_series(
    *,
    start: datetime,
    end: datetime,
    bucket_sec: int,
) -> tuple[list[dict], str]:
    """Network H/s series for charts.

    Prefer persisted ``network_hashrate_samples`` (sampled ~60s from Knots
    getnetworkhashps). Until that history exists, fall back to a flat tip line.
    """
    tip_hs = await _network_hashrate_hs()
    bsec = max(int(bucket_sec), 1)
    start_i = int(start.timestamp()) // bsec * bsec
    # Include the in-progress bucket so a just-taken sample is visible.
    end_i = int(end.timestamp()) // bsec * bsec

    samples = await store.list_network_hashrate(start=start, end=end)
    if samples:
        # Last sample in each bucket (forward-fill empty buckets with prior hs).
        by_bucket: dict[int, float] = {}
        for ts, hs in samples:
            b = int(ts.timestamp()) // bsec * bsec
            by_bucket[b] = float(hs)
        out: list[dict] = []
        last_hs = float(samples[0][1])
        first_b = int(samples[0][0].timestamp()) // bsec * bsec
        t = start_i
        while t <= end_i:
            if t in by_bucket:
                last_hs = by_bucket[t]
            # Before first sample: leave 0 so the line starts when tracking began.
            hs = last_hs if t >= first_b else 0.0
            out.append({"t": t, "hs": hs})
            t += bsec
        return out, "samples"

    # No history yet — flat tip so the axis still has a reference.
    out = []
    t = start_i
    while t <= end_i:
        out.append({"t": t, "hs": float(tip_hs or 0.0)})
        t += bsec
    return out, "tip"


@app.get("/api/charts/pool")
async def charts_pool(range: str = Query("24h")) -> dict:
    key, range_sec, bucket_sec, start, end, win_pre = await _chart_window(range)
    buckets = await store.share_work_buckets(
        start=start, end=end, bucket_sec=bucket_sec, address=None
    )
    pool = _fill_hs_series(buckets, start=start, end=end, bucket_sec=bucket_sec)
    network, network_source = await _network_hs_series(
        start=start, end=end, bucket_sec=bucket_sec
    )
    brows = await store.list_blocks_between(start=start, end=end, limit=500)
    workers = await store.finder_workers_for_blocks(brows)
    nmap = await store.nicknames_for_addresses(
        [b.finder_address for b in brows if b.finder_address]
    )
    # Payout window: shares after Nth-last confirmed find → (N-1) confirmed + current.
    n_win = max(int(settings.window_blocks or 8), 1)
    confirmed = await store.list_confirmed_blocks(limit=n_win)
    window_meta: dict | None = win_pre
    in_window_heights: set[int] = set()
    if len(confirmed) >= n_win:
        cutoff_block = confirmed[n_win - 1]  # Nth-last confirmed
        newer = confirmed[: n_win - 1]  # up to 7 finds after cutoff
        in_window_heights = {b.height for b in newer}
        if window_meta is None:
            cts = cutoff_block.accounted_at
            if cts is not None:
                if cts.tzinfo is None:
                    cts = cts.replace(tzinfo=timezone.utc)
                window_meta = {
                    "start_t": int(cts.timestamp()),
                    "end_t": int(end.timestamp()),
                    "cutoff_height": cutoff_block.height,
                    "finds_in_window": len(newer),
                    "window_blocks": n_win,
                    "label": f"{max(n_win - 1, 0)} confirmed + current",
                }
    elif confirmed:
        # Fewer than N finds: whole history is the window
        in_window_heights = {b.height for b in confirmed}
        if window_meta is None:
            oldest = confirmed[-1]
            ots = oldest.accounted_at
            if ots is not None:
                if ots.tzinfo is None:
                    ots = ots.replace(tzinfo=timezone.utc)
                window_meta = {
                    "start_t": int(ots.timestamp()),
                    "end_t": int(end.timestamp()),
                    "cutoff_height": None,
                    "finds_in_window": len(confirmed),
                    "window_blocks": n_win,
                    "label": f"{len(confirmed)} finds (building to {n_win})",
                }

    blocks_out = []
    for b in brows:
        if str(b.block_hash).startswith(("lab-", "pool-")):
            continue
        ts = b.accounted_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        blocks_out.append(
            {
                "t": int(ts.timestamp()),
                "height": b.height,
                "block_hash": b.block_hash,
                "worker": _display_worker(
                    b.finder_address or "", workers.get(b.height)
                ),
                "nickname": nmap.get(b.finder_address or "") or None,
                "status": b.status,
                "in_window": b.height in in_window_heights,
            }
        )
    out_range = "7d" if key == "1w" else key
    return {
        "range": out_range,
        "range_sec": range_sec,
        "bucket_sec": bucket_sec,
        "pool": pool,
        "network": network,
        "network_source": network_source,
        "blocks": blocks_out,
        "window": window_meta,
    }


@app.get("/api/user/{address}/charts")
async def charts_user(address: str, range: str = Query("24h")) -> dict:
    key, range_sec, bucket_sec, start, end, _win_pre = await _chart_window(range)
    buckets = await store.share_work_buckets(
        start=start, end=end, bucket_sec=bucket_sec, address=address
    )
    hashrate = _fill_hs_series(buckets, start=start, end=end, bucket_sec=bucket_sec)
    # Per-worker series for miner chart toggles
    cutoff = await store.payout_window_cutoff_seq(settings.window_blocks)
    wbreak = await store.worker_breakdown_after_cutoff(
        cutoff, addresses=[address], recent_sec=600
    )
    worker_names = [str(w.get("worker") or "") for w in (wbreak.get(address) or [])]
    # Also include any workers seen in the chart window (not only payout window)
    hashrate_by_worker: dict[str, list] = {}
    for wname in worker_names:
        label = _display_worker(address, wname) or wname
        wb = await store.share_work_buckets(
            start=start,
            end=end,
            bucket_sec=bucket_sec,
            address=address,
            worker=wname,
        )
        hashrate_by_worker[label] = _fill_hs_series(
            wb, start=start, end=end, bucket_sec=bucket_sec
        )
    brows = await store.list_blocks_between(start=start, end=end, limit=500)
    mine = [b for b in brows if (b.finder_address or "") == address]
    workers = await store.finder_workers_for_blocks(mine)
    nmap = await store.nicknames_for_addresses([address] if address else [])
    blocks_out = []
    for b in mine:
        if str(b.block_hash).startswith(("lab-", "pool-")):
            continue
        ts = b.accounted_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        blocks_out.append(
            {
                "t": int(ts.timestamp()),
                "height": b.height,
                "block_hash": b.block_hash,
                "worker": _display_worker(address, workers.get(b.height)),
                "nickname": nmap.get(address) or None,
                "status": b.status,
            }
        )
    return {
        "range": "7d" if key == "1w" else key,
        "range_sec": range_sec,
        "bucket_sec": bucket_sec,
        "hashrate": hashrate,
        "hashrate_by_worker": hashrate_by_worker,
        "workers": list(hashrate_by_worker.keys()),
        "blocks": blocks_out,
        "window": _win_pre,
    }


@app.get("/api/blocks", response_model=list[BlockOut])
async def blocks(limit: int = Query(20, ge=1, le=100)) -> list[BlockOut]:
    rows = await store.list_blocks(limit=limit * 3)
    # Hide synthetic hashes (lab-/pool- placeholders) from the public site
    real = [
        b
        for b in rows
        if not str(b.block_hash).startswith(("lab-", "pool-"))
    ][:limit]
    workers = await store.finder_workers_for_blocks(real)
    nmap = await store.nicknames_for_addresses(
        [b.finder_address for b in real if b.finder_address]
    )
    out: list[BlockOut] = []
    for b in real:
        intended = None
        raw = getattr(b, "intended_payout_json", None)
        if raw:
            try:
                parsed = json.loads(raw)
                intended = parsed.get("outputs", parsed) if isinstance(parsed, dict) else parsed
            except Exception:  # noqa: BLE001
                intended = None
        out.append(
            BlockOut(
                height=b.height,
                block_hash=b.block_hash,
                difficulty=b.difficulty,
                reward_sats=b.reward_sats,
                finder_address=b.finder_address,
                finder_worker=_display_worker(
                    b.finder_address or "", workers.get(b.height)
                ),
                finder_nickname=nmap.get(b.finder_address or ""),
                accounted_at=b.accounted_at,
                status=b.status,
                orphan_reason=b.orphan_reason,
                share_head_seq=b.share_head_seq,
                payout_mode=getattr(b, "payout_mode", None) or "onchain_split",
                manual_payout_done=bool(getattr(b, "manual_payout_done", False)),
                manual_payout_note=getattr(b, "manual_payout_note", None),
                intended_payout=intended,
            )
        )
    return out


async def _lifetime_tides_share_lines(address: str) -> list[UserPayoutOut]:
    """Replay each confirmed find's coinbaser window; return this address's TIDES lines.

    Window at find H = shares with cutoff_seq < seq <= share_head_seq(H), where
    cutoff is the Nth-last confirmed find *before* H (same rule as live payouts).
    """
    confirmed = await store.list_confirmed_blocks(limit=500)
    if not confirmed:
        return []
    # oldest → newest
    blocks = list(reversed(confirmed))
    shares = await store.list_shares_newest(limit=200_000)
    n = max(int(settings.window_blocks or 8), 1)
    miner_bps = miner_reward_bps(settings)
    out: list[UserPayoutOut] = []
    for i, b in enumerate(blocks):
        if str(b.block_hash).startswith(("lab-", "pool-")):
            continue
        before = blocks[:i]
        if len(before) >= n:
            cut_blk = before[-n]
            cutoff_seq = (
                int(cut_blk.share_head_seq)
                if cut_blk.share_head_seq is not None
                else 0
            )
        else:
            cutoff_seq = None
        head = b.share_head_seq
        window = []
        for s in shares:
            if head is not None and s.seq > int(head):
                continue
            if cutoff_seq is not None and s.seq <= int(cutoff_seq):
                continue
            window.append(s)
        if not window:
            continue
        tides = split_reward(
            window,
            reward_sats=int(b.reward_sats or 0),
            block_difficulty=int(b.difficulty or 1),
            window_blocks=n,
            miner_bps=miner_bps,
            pool_ops_address=settings.pool_ops_address or "",
            cutoff_seq=None,  # already filtered
            window_mode="pool_finds",
        )
        for ln in tides.lines:
            if ln.address == address:
                out.append(
                    UserPayoutOut(
                        height=int(b.height),
                        block_hash=b.block_hash,
                        kind="tides",
                        sats=int(ln.sats),
                        status=b.status or "confirmed",
                        accounted_at=b.accounted_at,
                    )
                )
                break
    return out


async def _lifetime_tides_share_sats(address: str) -> int:
    return sum(p.sats for p in await _lifetime_tides_share_lines(address))


@app.get("/user/{address}", response_model=UserStats)
@app.get("/api/user/{address}", response_model=UserStats)
async def user_stats(address: str) -> UserStats:
    _shares, window, _cutoff, _n = await _payout_window()
    work = sum(s.work for s in window if s.address == address)
    total = sum(s.work for s in window) or 1
    pct = 100.0 * work / total
    # Match main-page "If we find a block now" exactly (Prime coinbaser outs),
    # not a separate proportional estimate on a possibly different window/reward.
    try:
        cb = await _coinbaser_payload()
        est = sum(
            int(o.sats or 0)
            for o in (cb.outputs or [])
            if (o.address or "") == address
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("user est_next from coinbaser failed: %s", exc)
        miner_budget = (await _reward_estimate()) * miner_reward_bps(settings) // 10_000
        est = miner_budget * work // total
    finder, credit = await store.pending_finder_credit()
    pending = credit if finder == address else 0
    recent = await store.list_shares_for_address(address, limit=100)
    workers = sorted({r.worker for r in recent if r.worker})
    wbreak = await store.worker_breakdown_after_cutoff(
        _cutoff, addresses=[address], recent_sec=600
    )
    worker_breakdown = _worker_breaks_for_addr(
        address, wbreak, sats_total=int(est or 0)
    )
    q = await store.get_quarantine(address)
    rej, attempts = await store.recent_attempt_stats(address, limit=20)
    paid_finder, unpaid_finder = await store.finder_credit_totals(address)
    # Unpaid = open finder bonuses only (est. next tides share is a separate card).
    tides_earned = await _lifetime_tides_share_sats(address)
    total_earned = int(tides_earned) + int(paid_finder)
    unpaid_pending = int(unpaid_finder)
    # Latest find by this address (as block finder), newest first.
    last_find_height = None
    last_find_at = None
    last_find_age_sec = None
    for b in await store.list_blocks(limit=500):
        if (b.finder_address or "") != address:
            continue
        st = (b.status or "confirmed").lower()
        if st in ("orphaned", "misattributed"):
            continue
        if str(b.block_hash).startswith(("lab-", "pool-")):
            continue
        last_find_height = int(b.height)
        last_find_at = b.accounted_at
        if last_find_at is not None:
            ts = last_find_at
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            last_find_age_sec = max(
                0, int((datetime.now(timezone.utc) - ts).total_seconds())
            )
        break
    return UserStats(
        address=address,
        work_in_window=work,
        share_pct=round(pct, 4),
        estimated_next_sats=est,
        pending_finder_credit_sats=pending,
        total_earned_sats=total_earned,
        unpaid_pending_sats=unpaid_pending,
        share_count_shown=len(recent),
        workers=workers,
        worker_breakdown=worker_breakdown,
        quarantined=bool(q),
        quarantine_reason=(q or {}).get("reason") if q else None,
        reject27_recent=rej,
        attempt_recent=attempts,
        last_find_height=last_find_height,
        last_find_at=last_find_at,
        last_find_age_sec=last_find_age_sec,
    )


@app.get("/api/user/{address}/shares", response_model=list[ShareOut])
async def user_shares(
    address: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[ShareOut]:
    rows = await store.list_shares_for_address(address, limit=limit, offset=offset)
    return [
        ShareOut(
            seq=r.seq,
            address=r.address,
            worker=r.worker,
            work=r.work,
            fee_bps=r.fee_bps,
            accepted_at=r.accepted_at,
        )
        for r in rows
    ]


@app.get("/api/user/{address}/payouts", response_model=list[UserPayoutOut])
async def user_payouts(
    address: str,
    limit: int = Query(100, ge=1, le=500),
) -> list[UserPayoutOut]:
    """Reconstructed coinbase credits: TIDES share lines + finder bonuses."""
    tides = await _lifetime_tides_share_lines(address)
    # Map height → block meta for finder rows
    confirmed = await store.list_confirmed_blocks(limit=500)
    by_h = {int(b.height): b for b in confirmed}
    # Also peek recent (incl. pending) for hash/time when finder from_height is pending
    recent = await store.list_blocks(limit=200)
    for b in recent:
        by_h.setdefault(int(b.height), b)

    finder_rows: list[UserPayoutOut] = []
    for from_h, sats, paid_h in await store.list_finder_credits_for_address(
        address, limit=limit
    ):
        blk = by_h.get(int(from_h))
        status = "unpaid" if paid_h is None else "confirmed"
        finder_rows.append(
            UserPayoutOut(
                height=int(from_h),
                block_hash=blk.block_hash if blk else None,
                kind="finder",
                sats=int(sats),
                status=status,
                accounted_at=blk.accounted_at if blk else None,
                paid_in_height=int(paid_h) if paid_h is not None else None,
            )
        )

    merged = list(tides) + finder_rows
    merged.sort(
        key=lambda p: (
            p.accounted_at.timestamp() if p.accounted_at else 0,
            p.height,
            0 if p.kind == "finder" else 1,
        ),
        reverse=True,
    )
    return merged[:limit]


def _display_worker(address: str, worker: str | None) -> str:
    """Short worker label; strip address prefixes like addr.GPU0 → GPU0."""
    if not worker:
        return ""
    w = str(worker).strip()
    if not w:
        return ""
    if "." in w:
        left, right = w.split(".", 1)
        # username was address.worker
        if left.startswith(("1", "3", "bc1", "tb1", "bcrt")) or left == address:
            w = right or left
    return w[:48]


async def _worker_name_for_address(address: str) -> str:
    rows = await store.list_shares_for_address(address, limit=30)
    for r in rows:
        label = _display_worker(address, getattr(r, "worker", None))
        if label:
            return label
    return ""


def _worker_breaks_for_addr(
    addr: str,
    breakdown: dict[str, list[dict]],
    *,
    sats_total: int | None = None,
) -> list[WorkerBreak]:
    rows = breakdown.get(addr) or []
    addr_work = sum(int(r.get("work") or 0) for r in rows) or 1
    out: list[WorkerBreak] = []
    for r in rows:
        w = int(r.get("work") or 0)
        label = _display_worker(addr, str(r.get("worker") or "")) or str(r.get("worker") or "")
        if not label:
            label = "(unknown)"
        sats = None
        if sats_total is not None and sats_total > 0:
            sats = int(sats_total) * w // addr_work
        out.append(
            WorkerBreak(
                worker=label,
                shares=int(r.get("shares") or 0),
                work=w,
                share_pct=round(100.0 * w / addr_work, 4),
                hashrate_hs=float(r.get("hashrate_hs") or 0.0),
                sats=sats,
            )
        )
    return out


async def _coinbaser_payload() -> CoinbaserResponse:
    """Same split Gateways get from Prime — prefer CoinbaserSplitCache.

    Previously the website recalculated via _payout_window + reward_estimate
    while DATUM Gateways used coinbaser_cache.build_outs(template_value).
    That made address order/amounts drift. Now the site uses the Prime cache
    (and the last template value when available).
    """
    reward_est = await _reward_estimate()
    raw: list[dict] = []
    value = reward_est
    window_work = 0
    head: int | None = None

    if _coinbaser_cache is not None:
        last_v = _coinbaser_cache.last_prime_value()
        if last_v and int(last_v) > 0:
            value = int(last_v)
        # Rebuild from the same cached window Prime uses (rescaled to value).
        raw = await _coinbaser_cache.build_outs(int(value))
        window_work = int(_coinbaser_cache.window_work())
        c = getattr(_coinbaser_cache, "_cached", None)
        if c is not None and getattr(c, "max_seq", None) is not None:
            head = int(c.max_seq)
    else:
        try:
            snap_raw = await store.get_meta("coinbaser_last_json")
            if snap_raw:
                snap = json.loads(snap_raw)
                raw = list(snap.get("outputs") or [])
                if snap.get("value"):
                    value = int(snap["value"])
                window_work = int(snap.get("window_work") or 0)
                if snap.get("share_log_head_seq") is not None:
                    head = int(snap["share_log_head_seq"])
        except Exception as exc:  # noqa: BLE001
            log.warning("coinbaser snapshot read failed: %s", exc)
            raw = []
        if not raw:
            shares, _window, cutoff, _n = await _payout_window()
            tides = split_reward(
                shares,
                reward_sats=reward_est,
                block_difficulty=await _difficulty(),
                window_blocks=settings.window_blocks,
                miner_bps=miner_reward_bps(settings),
                min_output_sats=settings.min_output_sats,
                pool_ops_address=settings.pool_ops_address,
                cutoff_seq=cutoff,
                window_mode="pool_finds",
            )
            finder, credit = await store.pending_finder_credit()
            raw = coinbase_suggestion(
                tides,
                pool_ops_address=settings.pool_ops_address or "ops-unconfigured",
                finder_address=finder or "",
                finder_credit_sats=credit,
                min_output_sats=settings.min_output_sats,
            )
            window_work = int(tides.window_work)
            head = shares[0].seq if shares else None
            value = reward_est

    out_addrs = [str(o.get("address") or "") for o in raw if o.get("address")]
    nmap = await store.nicknames_for_addresses(out_addrs)
    cutoff = await store.payout_window_cutoff_seq(settings.window_blocks)
    wbreak = await store.worker_breakdown_after_cutoff(
        cutoff, addresses=out_addrs, recent_sec=600
    )
    # Fee 0%: no in-coinbase finder bonus — don't surface tides+finder on the site.
    hide_finder_kind = int(getattr(settings, "fee_bps", 0) or 0) <= 0
    outputs: list[CoinbaseOutput] = []
    for o in raw:
        kind = o.get("kind") or "tides"
        if hide_finder_kind and kind == "tides+finder":
            kind = "tides"
        addr = str(o.get("address") or "")
        sats = int(o.get("sats") or 0)
        workers: list[WorkerBreak] = []
        if kind == "ops":
            name = "ops"
            # Ops fee line shares the pool_ops address with miner shares when
            # hashrate also mined there — don't show that miner nickname here.
            nick = "OPERATION FEE"
        else:
            workers = _worker_breaks_for_addr(addr, wbreak, sats_total=sats)
            if workers:
                if len(workers) == 1:
                    name = workers[0].worker
                else:
                    name = " · ".join(w.worker for w in workers[:4])
                    if len(workers) > 4:
                        name += f" +{len(workers) - 4}"
            else:
                name = await _worker_name_for_address(addr)
            nick = nmap.get(addr)
        outputs.append(
            CoinbaseOutput(
                address=addr,
                sats=sats,
                kind=kind,
                name=name,
                nickname=nick,
                workers=workers,
            )
        )
    return CoinbaserResponse(
        reward_sats_estimate=int(value),
        outputs=outputs,
        window_work=window_work,
        share_log_head_seq=head,
    )


@app.get("/coinbaser", response_model=CoinbaserResponse)
@app.get("/api/coinbaser", response_model=CoinbaserResponse)
async def coinbaser() -> CoinbaserResponse:
    return await _coinbaser_payload()


@app.post("/api/admin/clear-lab")
async def clear_lab(confirm: str = Query("")) -> dict:
    """Wipe share log / fake pool blocks. Keeps RC3 chain meta. confirm=YES"""
    _require_lab_http()
    if confirm != "YES":
        raise HTTPException(400, "pass confirm=YES")
    result = await store.clear_lab_data()
    try:
        await sync_once(store, settings)
    except Exception as exc:  # noqa: BLE001
        result["resync_error"] = str(exc)
    return {"ok": True, **result}


@app.post("/api/admin/resync-chain")
async def resync_chain() -> dict:
    _require_lab_http()
    global _rpc_ok
    data = await sync_once(store, settings)
    _rpc_ok = True
    return {"ok": True, **data}


@app.post("/lab/share")
@app.post("/api/lab/share")
async def lab_inject_share(
    address: str,
    work: int = 1,
    worker: str | None = None,
) -> dict:
    """Dev-only share inject (not DATUM). Prefer real Gateway once Prime is live."""
    _require_lab_http()
    if work < 1:
        raise HTTPException(400, "work must be >= 1")
    if not address.strip():
        raise HTTPException(400, "address required")
    row = await store.append_share(address.strip(), work, worker=worker, fee_bps=0)
    return {
        "seq": row.seq,
        "address": row.address,
        "worker": row.worker,
        "work": row.work,
    }


@app.post("/lab/block")
@app.post("/api/lab/block")
async def lab_simulate_block(
    finder: str,
    height: int,
    difficulty: int = 1,
    reward_sats: int | None = None,
) -> dict:
    _require_lab_http()
    reward = reward_sats if reward_sats is not None else await _reward_estimate()
    diff = max(difficulty, 1)
    block_hash = f"lab-{height}-{finder[:8]}"
    await store.record_block(
        height=height,
        block_hash=block_hash,
        difficulty=float(diff),
        reward_sats=reward,
        finder_address=finder,
        status="pending",
        share_head_seq=await store.max_share_seq(),
    )
    paid_n = await store.mark_finder_credits_paid(height)
    credit = reward * finder_credit_bps(settings) // 10_000
    await store.open_finder_credit(height, finder, credit)
    return {
        "height": height,
        "finder": finder,
        "pending_credit_sats": credit,
        "ops_keep_sats": reward * (settings.fee_bps - finder_credit_bps(settings)) // 10_000,
        "marked_prior_credits_paid": paid_n,
        "note": "Finder bonus (80% of the 5% fee = 4% of block) applies on the next coinbaser after a find; ops keep 1%; not this block if none pending",
    }


@app.get("/api/pool_pubkey")
async def pool_pubkey_endpoint():
    """Plain or JSON-friendly pubkey for Gateway auto-fetch / copy-paste."""
    if not _pool_pubkey_hex:
        raise HTTPException(503, "pool pubkey not ready")
    # Clients may scrape any hex; keep body as raw 128-hex for simple parsers
    from fastapi.responses import PlainTextResponse

    return PlainTextResponse(_pool_pubkey_hex + "\n")


@app.get("/api/info")
async def info(request: Request) -> dict:
    role = settings.normalized_role()
    prime_up = bool(
        _prime_server is not None and getattr(_prime_server, "sockets", None)
    )
    if role == "web" and not prime_up:
        prime_up = await asyncio.to_thread(
            _probe_prime_tcp,
            (settings.prime_host or "127.0.0.1").strip() or "127.0.0.1",
            int(settings.datum_prime_port),
        )
    return {
        "name": "tides-pool",
        "version": __version__,
        "role": role,
        "network": settings.network,
        "mempool_explorer_url": settings.mempool_explorer_url,
        "docs": (str(request.base_url) + "docs") if _ALLOW_LAB_HTTP else None,
        "lab_http_enabled": _ALLOW_LAB_HTTP,
        "ui": str(request.base_url),
        "datum_prime_port": settings.datum_prime_port,
        "pool_host_hint": "tides.maveth.ca",
        "pool_port": settings.datum_prime_port,
        "pool_pubkey": _pool_pubkey_hex,
        "pool_pubkey_url": str(request.base_url) + "api/pool_pubkey",
        "datum_prime_up": prime_up,
        "join": {
            "pool_host": "tides.maveth.ca",
            "pool_port": settings.datum_prime_port,
            "pool_pubkey": _pool_pubkey_hex,
            "pool_pubkey_optional_if_autofetch": True,
            "note": "REQUIRED: run your own Knots Blake node and point DATUM bitcoind RPC at it — without that, DATUM stays not ready even if Prime connects. Prefer Leo StartOS pow_0.4.1_18+ / Umbrel Bitcoin-store DATUM (blake2b); experimental MaVeTh pow_0.4.1_20 also works. Then point DATUM pool_host here (miners → your DATUM Stratum, not :28916). Paste pool_pubkey if your build does not auto-fetch. Set mining.pool_address to your mainnet bc1…/1… payout.",
        },
    }

