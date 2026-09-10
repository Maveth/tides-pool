"""Per-miner settlement ledger helpers.

Payment history / Total earned must come from real coinbase outs or ops
sendmany pays — never from share-window ``split_reward`` replay.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

log = logging.getLogger(__name__)

SOURCE_ONCHAIN = "onchain"
SOURCE_SENDMANY = "sendmany"
SOURCE_PENDING = "intended_pending"

STATUS_PAID = "paid"
STATUS_PENDING = "pending"


@dataclass(frozen=True)
class MinerPayoutRow:
    height: int
    address: str
    sats: int
    kind: str
    source: str
    status: str
    txid: str | None = None
    paid_at: datetime | None = None


def _outputs_from_intended(raw: str | dict | list | None) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:  # noqa: BLE001
            return []
    else:
        parsed = raw
    if isinstance(parsed, dict):
        outs = parsed.get("outputs")
        if isinstance(outs, list):
            return [o for o in outs if isinstance(o, dict)]
        return []
    if isinstance(parsed, list):
        return [o for o in parsed if isinstance(o, dict)]
    return []


def rows_from_intended(
    height: int,
    intended: str | dict | list | None,
    *,
    source: str,
    status: str,
    txid: str | None = None,
    paid_at: datetime | None = None,
) -> list[MinerPayoutRow]:
    """Collapse intended outputs to one ledger row per address (sum sats)."""
    by_addr: dict[str, dict[str, Any]] = {}
    for o in _outputs_from_intended(intended):
        addr = str(o.get("address") or "").strip()
        if not addr:
            continue
        sats = int(o.get("sats") or o.get("value") or 0)
        if sats <= 0:
            continue
        kind = str(o.get("kind") or "tides")
        cur = by_addr.get(addr)
        if cur is None:
            by_addr[addr] = {"sats": sats, "kinds": {kind}}
        else:
            cur["sats"] += sats
            cur["kinds"].add(kind)
    out: list[MinerPayoutRow] = []
    for addr, info in by_addr.items():
        kinds = info["kinds"]
        if len(kinds) == 1:
            kind = next(iter(kinds))
        elif "bonus" in kinds:
            kind = "bonus"
        else:
            kind = "mixed"
        out.append(
            MinerPayoutRow(
                height=int(height),
                address=addr,
                sats=int(info["sats"]),
                kind=kind,
                source=source,
                status=status,
                txid=txid,
                paid_at=paid_at,
            )
        )
    return out


def rows_from_manual_adjustment(
    height: int, adj: Mapping[str, Any] | None
) -> list[MinerPayoutRow]:
    """Build paid sendmany / per-tx topup rows from ``manual_adjustment_{h}`` pays[].

    Accepts:
      - top-level ``status`` in paid/done/sent (classic sendmany), or
      - every pay line already has its own ``txid`` (listed_only separate sends).
    """
    if not adj or not isinstance(adj, Mapping):
        return []
    status = str(adj.get("status") or "").lower()
    pays = adj.get("pays") or []
    if not isinstance(pays, list) or not pays:
        return []
    per_tx = all(
        isinstance(p, Mapping) and isinstance(p.get("txid"), str) and bool(p.get("txid"))
        for p in pays
    )
    if status not in ("paid", "done", "sent") and not per_tx:
        # Still pending — caller may write intended_pending from intended instead.
        return []
    txid = adj.get("txid")
    if isinstance(txid, str):
        txid_s: str | None = txid
    else:
        txid_s = None
    paid_at = None
    raw_at = adj.get("paid_at")
    if isinstance(raw_at, str) and raw_at:
        try:
            paid_at = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            paid_at = datetime.now(timezone.utc)
    elif status in ("paid", "done", "sent") or per_tx:
        paid_at = datetime.now(timezone.utc)

    by_addr: dict[str, dict[str, Any]] = {}
    for p in pays:
        if not isinstance(p, Mapping):
            continue
        addr = str(p.get("address") or "").strip()
        if not addr:
            continue
        sats = int(p.get("pay_sats") or p.get("sats") or 0)
        if sats <= 0:
            continue
        kind = str(p.get("kind") or "tides")
        p_txid = p.get("txid")
        row_txid = str(p_txid) if isinstance(p_txid, str) and p_txid else txid_s
        cur = by_addr.get(addr)
        if cur is None:
            by_addr[addr] = {"sats": sats, "kinds": {kind}, "txid": row_txid}
        else:
            cur["sats"] += sats
            cur["kinds"].add(kind)
            if not cur.get("txid") and row_txid:
                cur["txid"] = row_txid

    out: list[MinerPayoutRow] = []
    for addr, info in by_addr.items():
        kinds = info["kinds"]
        if len(kinds) == 1:
            kind = next(iter(kinds))
        elif "bonus" in kinds:
            kind = "bonus"
        else:
            kind = "mixed"
        out.append(
            MinerPayoutRow(
                height=int(height),
                address=addr,
                sats=int(info["sats"]),
                kind=kind,
                source=SOURCE_SENDMANY,
                status=STATUS_PAID,
                txid=info.get("txid"),
                paid_at=paid_at,
            )
        )
    return out


def plan_sync_for_block(
    *,
    height: int,
    payout_mode: str | None,
    block_status: str | None,
    intended: str | dict | list | None,
    adjustment: Mapping[str, Any] | None,
) -> tuple[list[MinerPayoutRow], list[str]]:
    """Return (rows_to_upsert, sources_to_clear_before_upsert).

    Prefer sendmany when paid; else onchain for onchain_split; else pending owed.
    """
    height = int(height)
    mode = (payout_mode or "onchain_split").strip() or "onchain_split"
    st = (block_status or "").lower()
    sendmany = rows_from_manual_adjustment(height, adjustment)
    if sendmany:
        # Paid off-chain — drop any pending/onchain stubs for this height.
        return sendmany, [SOURCE_PENDING, SOURCE_ONCHAIN, SOURCE_SENDMANY]

    if mode == "onchain_split" and st not in ("orphaned", "misattributed"):
        rows = rows_from_intended(
            height,
            intended,
            source=SOURCE_ONCHAIN,
            status=STATUS_PAID,
        )
        if rows:
            return rows, [SOURCE_PENDING, SOURCE_ONCHAIN]

    # Owed but not yet settled on-chain / sendmany
    if st not in ("orphaned", "misattributed"):
        rows = rows_from_intended(
            height,
            intended,
            source=SOURCE_PENDING,
            status=STATUS_PENDING,
        )
        if rows:
            return rows, [SOURCE_PENDING]

    return [], []


async def sync_miner_payouts_for_height(store: Any, height: int) -> int:
    """Load block + adj meta and upsert ledger rows. Returns rows written."""
    height = int(height)
    blk = None
    getter = getattr(store, "get_block", None)
    if callable(getter):
        blk = await getter(height)
    if blk is None:
        blocks = await store.list_blocks(limit=2000)
        blk = next((b for b in blocks if int(b.height) == height), None)
    if blk is None:
        log.warning("sync_miner_payouts: no block %s", height)
        return 0

    adj = None
    try:
        raw = await store.get_meta(f"manual_adjustment_{height}")
        if raw:
            adj = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("sync_miner_payouts: bad adj meta %s: %s", height, exc)

    rows, clear_sources = plan_sync_for_block(
        height=height,
        payout_mode=getattr(blk, "payout_mode", None),
        block_status=getattr(blk, "status", None),
        intended=getattr(blk, "intended_payout_json", None),
        adjustment=adj if isinstance(adj, dict) else None,
    )
    if clear_sources:
        await store.delete_miner_payouts(height, sources=clear_sources)
    if not rows:
        return 0
    await store.upsert_miner_payouts(rows)
    return len(rows)


def pick_display_rows(
    rows: Iterable[MinerPayoutRow],
) -> list[MinerPayoutRow]:
    """Prefer paid over pending for the same height+address."""
    best: dict[tuple[int, str], MinerPayoutRow] = {}
    rank = {STATUS_PAID: 2, STATUS_PENDING: 1}
    for r in rows:
        key = (int(r.height), r.address)
        cur = best.get(key)
        if cur is None or rank.get(r.status, 0) > rank.get(cur.status, 0):
            best[key] = r
        elif (
            cur is not None
            and rank.get(r.status, 0) == rank.get(cur.status, 0)
            and r.source == SOURCE_SENDMANY
        ):
            best[key] = r
    return sorted(best.values(), key=lambda x: (-x.height, -x.sats))
