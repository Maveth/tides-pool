"""Confirm pending pool blocks against tip; orphan + reopen finder bonus if needed."""

from __future__ import annotations

import logging
import re
from typing import Any

from tides_pool.bitcoin_rpc import BitcoinRPC, BitcoinRPCError
from tides_pool.config import Settings
from tides_pool.store import Store

log = logging.getLogger("tides_pool.block_confirm")

_HEX_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _ascii_from_script_hex(script_hex: str) -> str:
    try:
        raw = bytes.fromhex(script_hex)
    except ValueError:
        return ""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


def coinbase_looks_like_ours(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
) -> bool:
    """True if coinbase tag or ops payout suggests this is our TIDES block."""
    try:
        tx0 = block["tx"][0]
    except (KeyError, IndexError, TypeError):
        return False

    # Knots verbosity=2: tx is list of decoded txs
    vin0 = (tx0.get("vin") or [{}])[0]
    coinbase_hex = vin0.get("coinbase") or ""
    ascii_cb = _ascii_from_script_hex(coinbase_hex) if coinbase_hex else ""
    tag = (tag_primary or "").strip()
    if tag and tag in ascii_cb:
        return True

    vouts = tx0.get("vout") or []
    addrs: list[str] = []
    for o in vouts:
        spk = o.get("scriptPubKey") or {}
        if spk.get("address"):
            addrs.append(spk["address"])
        for a in spk.get("addresses") or []:
            addrs.append(a)
    if ops_address and ops_address in addrs and len(addrs) >= 2:
        # Multi-out including ops is a strong TIDES signal on this pool
        return True
    return False


def resolve_tides_block_near_height(
    rpc: BitcoinRPC,
    *,
    height: int,
    tag_primary: str,
    ops_address: str,
    scan: int = 2,
) -> tuple[int, str] | None:
    """Return (height, hash) of a nearby tip block that looks like ours."""
    for h in range(height - scan, height + scan + 1):
        if h < 0:
            continue
        try:
            hx = rpc.call("getblockhash", [int(h)])
        except BitcoinRPCError:
            continue
        if not isinstance(hx, str) or not _HEX_RE.match(hx):
            continue
        try:
            blk = rpc.call("getblock", [hx, 2])
        except BitcoinRPCError:
            continue
        if coinbase_looks_like_ours(blk, tag_primary=tag_primary, ops_address=ops_address):
            return int(h), hx
    return None


async def reconcile_pool_blocks(store: Store, settings: Settings) -> dict:
    """Advance pending → confirmed/orphaned once tip is N blocks ahead."""
    conf_n = max(int(getattr(settings, "block_confirmations", 2) or 2), 1)
    chain_raw = await store.get_meta("chain_height")
    try:
        tip = int(chain_raw) if chain_raw else 0
    except ValueError:
        tip = 0
    if tip <= 0:
        return {"tip": tip, "checked": 0}

    pending = await store.list_blocks_by_status("pending", limit=50)
    if not pending:
        return {"tip": tip, "checked": 0}

    rpc = BitcoinRPC(settings)
    checked = 0
    confirmed = 0
    orphaned = 0
    fixed = 0

    for b in pending:
        if tip < int(b.height) + conf_n:
            continue
        checked += 1
        our_hash = str(b.block_hash or "")
        synthetic = our_hash.startswith("pool-") or not _HEX_RE.match(our_hash)

        canonical = None
        try:
            canonical = rpc.call("getblockhash", [int(b.height)])
        except BitcoinRPCError as exc:
            log.warning("getblockhash(%s) failed: %s", b.height, exc)

        blk = None
        if isinstance(canonical, str) and _HEX_RE.match(canonical):
            try:
                blk = rpc.call("getblock", [canonical, 2])
            except BitcoinRPCError:
                blk = None

        ours_at_height = bool(
            blk
            and coinbase_looks_like_ours(
                blk,
                tag_primary=settings.coinbase_tag_primary,
                ops_address=settings.pool_ops_address,
            )
        )

        if not synthetic and canonical == our_hash and ours_at_height:
            await store.set_block_status(b.height, "confirmed")
            confirmed += 1
            log.info("block %s confirmed hash=%s", b.height, our_hash[:16])
            continue

        # Wrong hash recorded at this height, or reorged — try nearby TIDES block
        found = resolve_tides_block_near_height(
            rpc,
            height=int(b.height),
            tag_primary=settings.coinbase_tag_primary,
            ops_address=settings.pool_ops_address,
            scan=2,
        )
        if found:
            new_h, new_hash = found
            if new_h == b.height and new_hash == our_hash and ours_at_height:
                await store.set_block_status(b.height, "confirmed")
                confirmed += 1
                continue
            # Real TIDES block at different height/hash
            reason = "misattributed" if (canonical == our_hash and not ours_at_height) else "height_fix"
            await store.reassign_pending_block(
                old_height=int(b.height),
                new_height=int(new_h),
                new_hash=new_hash,
                finder_address=b.finder_address,
                reason=reason,
            )
            fixed += 1
            log.warning(
                "block reassigned %s → %s hash=%s (%s)",
                b.height,
                new_h,
                new_hash[:16],
                reason,
            )
            continue

        reason = "misattributed" if (canonical == our_hash and not ours_at_height) else "orphaned"
        await store.mark_block_orphaned(int(b.height), reason=reason)
        orphaned += 1
        log.warning("block %s marked %s (hash=%s)", b.height, reason, our_hash[:20])

    return {
        "tip": tip,
        "checked": checked,
        "confirmed": confirmed,
        "orphaned": orphaned,
        "fixed": fixed,
    }
