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
_HEADLINE_RE = re.compile(
    r"NYPost|Deride\s+And\s+Conquer|8-30\s+NYPost",
    re.I,
)


def _ascii_from_script_hex(script_hex: str) -> str:
    try:
        raw = bytes.fromhex(script_hex)
    except ValueError:
        return ""
    return "".join(chr(b) if 32 <= b < 127 else "." for b in raw)


def coinbase_value_sats(block: dict[str, Any]) -> int | None:
    """Sum coinbase vout values (subsidy + fees) from getblock verbosity=2."""
    try:
        tx0 = block["tx"][0]
        total = 0
        for o in tx0.get("vout") or []:
            # BTC Core/Knots: value is BTC float; prefer valueSat if present
            if "valueSat" in o:
                total += int(o["valueSat"])
            elif "value" in o:
                total += int(round(float(o["value"]) * 100_000_000))
        return total if total > 0 else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def coinbase_payout_addresses(block: dict[str, Any]) -> list[str]:
    """Value-bearing payout addresses from getblock verbosity=2 coinbase."""
    try:
        tx0 = block["tx"][0]
    except (KeyError, IndexError, TypeError):
        return []
    addrs: list[str] = []
    for o in tx0.get("vout") or []:
        try:
            val = o.get("valueSat")
            if val is None:
                val = int(round(float(o.get("value") or 0) * 100_000_000))
            else:
                val = int(val)
        except (TypeError, ValueError):
            val = 0
        if val <= 0:
            continue
        spk = o.get("scriptPubKey") or {}
        if spk.get("address"):
            addrs.append(str(spk["address"]))
        for a in spk.get("addresses") or []:
            addrs.append(str(a))
    return addrs


def coinbase_ascii(block: dict[str, Any]) -> str:
    try:
        tx0 = block["tx"][0]
        vin0 = (tx0.get("vin") or [{}])[0]
        coinbase_hex = vin0.get("coinbase") or ""
        return _ascii_from_script_hex(coinbase_hex) if coinbase_hex else ""
    except (KeyError, IndexError, TypeError):
        return ""


def sanitize_nickname(raw: str | None) -> str | None:
    """Normalize a nickname; drop OP_PUSH / binary leftovers like ``DonSATS......P.q``."""
    if not raw:
        return None
    s = str(raw).strip()
    # Cut at mapped nul/OP-push runs (``...``) or control bytes.
    s = re.split(r"\.{2,}|\x00", s, maxsplit=1)[0]
    s = s.strip(" .\t|/|:;,")
    if len(s) < 2 or len(s) > 64:
        return None
    if s.startswith(("P.", "q.")):
        return None
    if _HEADLINE_RE.search(s):
        return None
    # Real nicknames have a letter run; skip ``PÎ`` / digit noise.
    if not re.search(r"[A-Za-z]{3,}", s):
        return None
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in s):
        return None
    # Dots are OP_PUSH placeholders in our ascii helper — not nickname chars.
    if "." in s:
        return None
    return s[:64]


def extract_secondary_tag(ascii_cb: str, primary: str) -> str | None:
    """Best-effort secondary coinbase tag (miner nickname) after primary TIDES tag.

    DATUM/TIDES coinbases typically embed primary then secondary as printable ASCII
    (nul / OP_PUSH length bytes show as '.' in our ascii helper), e.g.
    ``TIDES.Bitcoin ForkLift``. Do **not** allow ``.`` inside the nickname — that
    glued ``DonSATS......P.q`` from trailing script bytes.
    """
    tag = (primary or "").strip()
    if not tag or not ascii_cb or tag not in ascii_cb:
        return None
    i = ascii_cb.find(tag) + len(tag)
    # Skip separators + mapped non-printables between primary and secondary.
    while i < len(ascii_cb) and (
        ascii_cb[i] in ".\x00/|:;," or ord(ascii_cb[i]) < 32
    ):
        i += 1
    j = i
    # No '.' here — '.' is our stand-in for OP_PUSH / nul / binary.
    while j < len(ascii_cb) and (
        ascii_cb[j].isalnum() or ascii_cb[j] in " _-+'&"
    ):
        j += 1
    return sanitize_nickname(ascii_cb[i:j])


def coinbase_looks_like_ours(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
) -> bool:
    """True if this is our pool block: primary TIDES tag AND ops payout address present.

    Finder identity is separate (stratum address/worker on the winning share).
    Do not treat tag-only or ops-only as sufficient — both are required.
    """
    tag = (tag_primary or "").strip()
    ops = (ops_address or "").strip()
    if not tag or not ops:
        return False
    ascii_cb = coinbase_ascii(block)
    if tag not in ascii_cb:
        return False
    addrs = coinbase_payout_addresses(block)
    if ops not in addrs:
        return False
    # Real TIDES coinbaser is multi-out; reject solo-looking templates
    if len(addrs) < 2:
        return False
    return True


def verify_pool_block(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
) -> tuple[bool, str]:
    """Return (ok, reason). Used before recording finds / opening credits."""
    tag = (tag_primary or "").strip()
    ops = (ops_address or "").strip()
    if not tag:
        return False, "no_tag_configured"
    if not ops:
        return False, "no_ops_configured"
    ascii_cb = coinbase_ascii(block)
    if tag not in ascii_cb:
        return False, "missing_tides_tag"
    addrs = coinbase_payout_addresses(block)
    if ops not in addrs:
        return False, "missing_ops_payout"
    if len(addrs) < 2:
        return False, "not_multi_out"
    return True, "ok"


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
            # Refresh reward from chain (subsidy + fees) — find-time estimate is often subsidy-only
            if blk is not None:
                actual = coinbase_value_sats(blk)
                if actual and actual != int(b.reward_sats or 0):
                    await store.update_block_reward(int(b.height), actual)
                    log.info(
                        "block %s reward corrected %s → %s (incl fees)",
                        b.height,
                        b.reward_sats,
                        actual,
                    )
                nick = extract_secondary_tag(
                    coinbase_ascii(blk), settings.coinbase_tag_primary
                )
                if nick and b.finder_address:
                    try:
                        await store.set_address_nickname(str(b.finder_address), nick)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("nickname save failed: %s", exc)
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
            # Re-load and re-verify (tag + ops) before trusting the nearby match
            try:
                new_blk = rpc.call("getblock", [new_hash, 2])
            except BitcoinRPCError:
                new_blk = None
            ok, why = (
                verify_pool_block(
                    new_blk,
                    tag_primary=settings.coinbase_tag_primary,
                    ops_address=settings.pool_ops_address,
                )
                if new_blk
                else (False, "getblock_failed")
            )
            if not ok:
                log.warning(
                    "block %s nearby candidate %s rejected (%s)",
                    b.height,
                    new_hash[:16],
                    why,
                )
            elif new_h == b.height and new_hash == our_hash:
                if new_blk is not None:
                    actual = coinbase_value_sats(new_blk)
                    if actual and actual != int(b.reward_sats or 0):
                        await store.update_block_reward(int(b.height), actual)
                await store.set_block_status(b.height, "confirmed")
                confirmed += 1
                continue
            elif ok:
                reason = "misattributed" if (canonical == our_hash and not ours_at_height) else "height_fix"
                await store.reassign_pending_block(
                    old_height=int(b.height),
                    new_height=int(new_h),
                    new_hash=new_hash,
                    finder_address=b.finder_address,
                    reason=reason,
                )
                if new_blk is not None:
                    actual = coinbase_value_sats(new_blk)
                    if actual:
                        await store.update_block_reward(int(new_h), actual)
                    nick = extract_secondary_tag(
                        coinbase_ascii(new_blk), settings.coinbase_tag_primary
                    )
                    if nick and b.finder_address:
                        try:
                            await store.set_address_nickname(str(b.finder_address), nick)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("nickname save failed: %s", exc)
                fixed += 1
                log.warning(
                    "block reassigned %s → %s hash=%s (%s)",
                    b.height,
                    new_h,
                    new_hash[:16],
                    reason,
                )
                continue

        # Synthetic claims that never matched a real TIDES+ops coinbase: void credits
        reason = "misattributed" if (canonical == our_hash and not ours_at_height) else "orphaned"
        if synthetic:
            reason = "unverified_claim"
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
