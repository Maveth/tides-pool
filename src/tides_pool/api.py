from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from tides_pool import __version__
from tides_pool.chain_sync import chain_sync_loop, sync_once
from tides_pool.config import Settings, finder_credit_bps, miner_reward_bps
from tides_pool.datum_prime import start_datum_prime

from tides_pool.models import (
    BlockOut,
    CoinbaseOutput,
    CoinbaserResponse,
    Contributor,
    HealthResponse,
    PoolStats,
    ShareOut,
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


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global store, _sync_task, _rpc_ok, _prime_server, _pool_pubkey_hex
    logging.basicConfig(level=logging.INFO)
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

    # Pull live RC3 tip — do not invent difficulty=1 / fake reward forever
    try:
        await sync_once(store, settings)
        _rpc_ok = True
    except Exception as exc:  # noqa: BLE001
        log.warning("initial RC3 sync failed: %s", exc)
        _rpc_ok = False
        if await store.get_meta("block_difficulty") is None:
            await store.set_meta("block_difficulty", "1")
        if await store.get_meta("reward_estimate") is None:
            await store.set_meta("reward_estimate", str(50 * 100_000_000))

    keys_path = Path(os.environ.get("TIDES_POOL_KEYS_PATH", "/app/data/pool_keys.json"))
    try:
        _prime_server, keys = await start_datum_prime(settings, store, keys_path)
        _pool_pubkey_hex = keys.pubkey_hex
    except Exception as exc:  # noqa: BLE001
        log.exception("DATUM Prime failed to start: %s", exc)
        _prime_server = None

    _stop_sync.clear()
    _sync_task = asyncio.create_task(chain_sync_loop(store, settings, _stop_sync))
    yield
    _stop_sync.set()
    if _sync_task:
        await _sync_task
    if _prime_server is not None:
        _prime_server.close()
        await _prime_server.wait_closed()
    await store.close()



app = FastAPI(title="tides-pool", version=__version__, lifespan=lifespan)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


async def _difficulty() -> int:
    raw = await store.get_meta("block_difficulty", "1") or "1"
    try:
        return max(int(float(raw)), 1)
    except ValueError:
        return 1


async def _reward_estimate() -> int:
    raw = await store.get_meta("reward_estimate", str(50 * 100_000_000)) or "0"
    try:
        return max(int(raw), 0)
    except ValueError:
        return 0


async def _last_height() -> int | None:
    # Latest non-orphaned pool find for the "Last pool block" card.
    for b in await store.list_blocks(limit=30):
        if b.status in ("pending", "confirmed"):
            return b.height
    raw = await store.get_meta("last_height")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _payout_window():
    """Shares in the live payout window: (N-1) confirmed finds + current.

    Cutoff is the share head of the Nth-last confirmed find; orphans do not count.
    """
    shares = await store.list_shares_newest(limit=50_000)
    cutoff = await store.payout_window_cutoff_seq(settings.window_blocks)
    window = window_since_seq(shares, cutoff)
    confirmed = await store.list_confirmed_blocks(limit=settings.window_blocks)
    return shares, window, cutoff, len(confirmed)


async def _blocks_last_24h() -> tuple[int, int]:
    """Return (confirmed_or_pending, orphaned) finds accounted in the last 24 hours.

    Orphans are excluded from the main count but returned separately for the UI.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    good = 0
    orphans = 0
    for b in await store.list_blocks(limit=100):
        if str(b.block_hash).startswith("lab-"):
            continue
        ts = b.accounted_at
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        st = getattr(b, "status", None) or "confirmed"
        if st in ("orphaned", "misattributed"):
            orphans += 1
        else:
            good += 1
    return good, orphans


def _fmt_sats_html(sats: int) -> str:
    n = int(sats or 0)
    if n >= 100_000_000:
        return f"{n / 1e8:.4f} BTC"
    return f"{n:,} sats"


def _short_addr_html(addr: str) -> str:
    if not addr:
        return "—"
    if len(addr) <= 16:
        return addr
    return f"{addr[:8]}…{addr[-6:]}"


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    index_path = STATIC_DIR / "index.html"
    if not index_path.is_file():
        return HTMLResponse("<h1>tides-pool</h1><p>static UI missing</p>", status_code=500)
    html = index_path.read_text(encoding="utf-8")
    # Cache-bust UI JS so browsers pick up render fixes.
    # Keep in sync with static/index.html script tag when bumping UI.
    import re as _re
    html = _re.sub(
        r'src="/static/app\.js(?:\?v=[^"]*)?"',
        'src="/static/app.js?v=20260901i"',
        html,
        count=1,
    )
    html = _re.sub(
        r'href="/static/style\.css(?:\?v=[^"]*)?"',
        'href="/static/style.css?v=20260901i"',
        html,
        count=1,
    )
    # Server-render coinbaser so payout breakdown is visible even if client JS fails.
    try:
        cb = await _coinbaser_payload()
        outs = cb.outputs or []
        note = (
            f"~{_fmt_sats_html(cb.reward_sats_estimate)} total · {len(outs)} payout line(s) "
            f"· window work {cb.window_work:,}"
            if outs
            else f"No miner lines yet (~{_fmt_sats_html(cb.reward_sats_estimate)}) — empty window pays ops only"
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
                    f"<td>{_fmt_sats_html(o.sats)}</td>"
                    "</tr>"
                )
            body = "\n".join(rows)
        else:
            body = '<tr><td colspan="4" class="muted">No coinbaser outputs (empty window → ops only)</td></tr>'
        html = html.replace(
            'id="coinbaserNote">Loading split…</p>',
            f'id="coinbaserNote">{note}</p>',
            1,
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


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", version=__version__, network=settings.network)


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
    blocks_24h, orphans_24h = await _blocks_last_24h()
    return PoolStats(
        share_log_work=await store.total_work(),
        share_count=await store.share_count(),
        window_work_target=ocean_target,
        window_work_filled=filled,
        addresses_in_window=len(addrs),
        last_pool_block_height=await _last_height(),
        blocks_last_24h=blocks_24h,
        orphans_last_24h=orphans_24h,
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
        block_confirmations=settings.block_confirmations,
        network=settings.network,
        pool_name=f"{settings.coinbase_tag_primary}/{settings.coinbase_tag_secondary}",
        pool_ops_address=settings.pool_ops_address,
        rpc_ok=_rpc_ok or chain_h is not None,
        address_work_cap=settings.address_work_cap(),
        address_work_cap_window_sec=settings.address_work_cap_window_sec,
        gpu_baseline_hs=settings.gpu_baseline_hashrate_hs,
        hashrate_hs=estimate_hashrate_hs(recent_work, hr_window),
        hashrate_window_sec=hr_window,
        hashrate_work=recent_work,
        hashrate_shares=len(recent),
    )


@app.get("/api/contributors", response_model=list[Contributor])
async def contributors(limit: int = Query(50, ge=1, le=500)) -> list[Contributor]:
    _shares, window, _cutoff, _n = await _payout_window()
    hr_window = 600
    recent = await store.list_share_rows_since(hr_window, limit=50_000)
    rows = contributor_rows(window, recent=recent, hashrate_window_sec=hr_window)[:limit]
    qmap = await store.list_quarantines([r["address"] for r in rows])
    out: list[Contributor] = []
    for r in rows:
        q = qmap.get(r["address"])
        out.append(
            Contributor(
                **r,
                quarantined=bool(q),
                quarantine_reason=(q or {}).get("reason") if q else None,
            )
        )
    return out


@app.get("/api/blocks", response_model=list[BlockOut])
async def blocks(limit: int = Query(20, ge=1, le=100)) -> list[BlockOut]:
    rows = await store.list_blocks(limit=limit * 2)
    # Hide synthetic lab rows from the public site
    real = [b for b in rows if not str(b.block_hash).startswith("lab-")][:limit]
    return [
        BlockOut(
            height=b.height,
            block_hash=b.block_hash,
            difficulty=b.difficulty,
            reward_sats=b.reward_sats,
            finder_address=b.finder_address,
            accounted_at=b.accounted_at,
            status=b.status,
            orphan_reason=b.orphan_reason,
            share_head_seq=b.share_head_seq,
        )
        for b in real
    ]


@app.get("/user/{address}", response_model=UserStats)
@app.get("/api/user/{address}", response_model=UserStats)
async def user_stats(address: str) -> UserStats:
    _shares, window, _cutoff, _n = await _payout_window()
    work = sum(s.work for s in window if s.address == address)
    total = sum(s.work for s in window) or 1
    pct = 100.0 * work / total
    miner_budget = (await _reward_estimate()) * miner_reward_bps(settings) // 10_000
    est = miner_budget * work // total
    finder, credit = await store.pending_finder_credit()
    pending = credit if finder == address else 0
    recent = await store.list_shares_for_address(address, limit=100)
    workers = sorted({r.worker for r in recent if r.worker})
    q = await store.get_quarantine(address)
    rej, attempts = await store.recent_attempt_stats(address, limit=20)
    return UserStats(
        address=address,
        work_in_window=work,
        share_pct=round(pct, 4),
        estimated_next_sats=est,
        pending_finder_credit_sats=pending,
        share_count_shown=len(recent),
        workers=workers,
        quarantined=bool(q),
        quarantine_reason=(q or {}).get("reason") if q else None,
        reject27_recent=rej,
        attempt_recent=attempts,
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


async def _coinbaser_payload() -> CoinbaserResponse:
    shares, _window, cutoff, _n = await _payout_window()
    tides = split_reward(
        shares,
        reward_sats=await _reward_estimate(),
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
    outputs: list[CoinbaseOutput] = []
    for o in raw:
        kind = o.get("kind") or "tides"
        if kind == "ops":
            name = "ops"
        else:
            name = await _worker_name_for_address(str(o.get("address") or ""))
        outputs.append(
            CoinbaseOutput(
                address=str(o.get("address") or ""),
                sats=int(o.get("sats") or 0),
                kind=kind,
                name=name,
            )
        )
    head = shares[0].seq if shares else None
    return CoinbaserResponse(
        reward_sats_estimate=await _reward_estimate(),
        outputs=outputs,
        window_work=tides.window_work,
        share_log_head_seq=head,
    )


@app.get("/coinbaser", response_model=CoinbaserResponse)
@app.get("/api/coinbaser", response_model=CoinbaserResponse)
async def coinbaser() -> CoinbaserResponse:
    return await _coinbaser_payload()


@app.post("/api/admin/clear-lab")
async def clear_lab(confirm: str = Query("")) -> dict:
    """Wipe share log / fake pool blocks. Keeps RC3 chain meta. confirm=YES"""
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
    return {
        "name": "tides-pool",
        "version": __version__,
        "network": settings.network,
        "mempool_explorer_url": settings.mempool_explorer_url,
        "docs": str(request.base_url) + "docs",
        "ui": str(request.base_url),
        "datum_prime_port": settings.datum_prime_port,
        "pool_host_hint": "tides.maveth.ca",
        "pool_port": settings.datum_prime_port,
        "pool_pubkey": _pool_pubkey_hex,
        "pool_pubkey_url": str(request.base_url) + "api/pool_pubkey",
        "datum_prime_up": _prime_server is not None,
        "join": {
            "pool_host": "tides.maveth.ca",
            "pool_port": settings.datum_prime_port,
            "pool_pubkey": _pool_pubkey_hex,
            "pool_pubkey_optional_if_autofetch": True,
            "note": "REQUIRED: run your own Knots RC4 Blake node and point DATUM bitcoind RPC at it — without that, DATUM stays not ready even if Prime connects. Then point DATUM pool_host here (miners → your DATUM Stratum, not :28916). Empty pool_pubkey OK on MaVeTh Blake builds. Set mining.pool_address to your mainnet bc1…/1… payout.",
        },
    }

