"""Confirm pending pool blocks against tip; orphan + reopen finder bonus if needed."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from tides_pool.bitcoin_rpc import BitcoinRPC, BitcoinRPCError
from tides_pool.config import Settings, miner_reward_bps
from tides_pool.payment_verify import (
    block_intended_to_map,
    classify_intended_vs_chain,
    is_same_payee_value_drift,
    rescale_payment_map,
)
from tides_pool.store import Store
from tides_pool.tides import coinbase_suggestion, split_reward

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
    return list(coinbase_payout_map(block).keys())


def coinbase_payout_map(block: dict[str, Any]) -> dict[str, int]:
    """Address → sats from getblock verbosity=2 coinbase (sums duplicate addrs)."""
    try:
        tx0 = block["tx"][0]
    except (KeyError, IndexError, TypeError):
        return {}
    out: dict[str, int] = {}
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
        addrs: list[str] = []
        if spk.get("address"):
            addrs.append(str(spk["address"]))
        for a in spk.get("addresses") or []:
            addrs.append(str(a))
        if not addrs:
            continue
        # Unusual multi-address vout: attribute full value to first
        a0 = addrs[0].strip()
        if a0:
            out[a0] = out.get(a0, 0) + int(val)
    return out


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
    """Best-effort secondary coinbase tag (miner nickname) after primary pool tag.

    DATUM coinbases typically embed primary then secondary as printable ASCII
    (nul / OP_PUSH length bytes show as '.' in our ascii helper), e.g.
    ``RIPTIDE.Bitcoin ForkLift`` (legacy ``TIDES.…``). Do **not** allow ``.``
    inside the nickname — that glued ``DonSATS......P.q`` from trailing script.
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


def matched_pool_tag(
    ascii_cb: str,
    tag_primary: str,
    tag_legacy: str = "TIDES",
) -> str | None:
    """Return which configured primary (or legacy) appears in coinbase ASCII.

    Prefers the current ``tag_primary`` when both are present.
    """
    primary = (tag_primary or "").strip()
    cands: list[str] = []
    for t in [primary, *str(tag_legacy or "").split(",")]:
        t = (t or "").strip()
        if t and t not in cands:
            cands.append(t)
    if not ascii_cb or not cands:
        return None
    if primary and primary in ascii_cb:
        return primary
    for t in cands:
        if t in ascii_cb:
            return t
    return None


def classify_pool_coinbase(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
    tag_legacy: str = "TIDES",
) -> tuple[bool, str, str | None]:
    """Classify whether an on-chain coinbase is a pool find.

    Returns ``(ok, reason, payout_mode)`` where ``payout_mode`` is:
      - ``onchain_split`` — normal multi-out (miners + ops)
      - ``ops_manual`` — single value out to ops only (Prime/GW fallback);
        keep as pool find; ops pays miners manually
      - ``needs_review`` is **not** set here — that comes from confirm-time
        intended↔chain checks when the mismatch is not zero-sum drift
      - ``None`` when ``ok`` is False

    Hard gate: a single value out to anyone **other than ops** is not ours.
    Primary tag may be renamed (e.g. TIDES→RIPTIDE); ``tag_legacy`` keeps
    historical coinbases attributable.
    """
    tag = (tag_primary or "").strip()
    ops = (ops_address or "").strip()
    if not tag:
        return False, "no_tag_configured", None
    if not ops:
        return False, "no_ops_configured", None
    ascii_cb = coinbase_ascii(block)
    if matched_pool_tag(ascii_cb, tag, tag_legacy) is None:
        return False, "missing_tides_tag", None
    addrs = coinbase_payout_addresses(block)
    if not addrs:
        return False, "no_value_outs", None
    # Preserve order, unique
    uniq = list(dict.fromkeys(addrs))
    if len(uniq) == 1:
        if uniq[0] == ops:
            # Ops-only fallback — still our find; manual miner payout owed
            return True, "ops_manual_single", "ops_manual"
        return False, "single_out_not_ops", None
    if ops not in uniq:
        return False, "missing_ops_payout", None
    return True, "ok", "onchain_split"


def coinbase_looks_like_ours(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
    tag_legacy: str = "TIDES",
) -> bool:
    """True if this is our pool block (multi-out split OR ops-only manual).

    Finder identity is separate (stratum address/worker on the winning share).
    Requires configured primary (or legacy) tag. Single-out to non-ops rejected.
    """
    ok, _reason, _mode = classify_pool_coinbase(
        block,
        tag_primary=tag_primary,
        ops_address=ops_address,
        tag_legacy=tag_legacy,
    )
    return ok


def verify_pool_block(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
    tag_legacy: str = "TIDES",
) -> tuple[bool, str]:
    """Return (ok, reason). Used before recording finds / opening credits."""
    ok, reason, _mode = classify_pool_coinbase(
        block,
        tag_primary=tag_primary,
        ops_address=ops_address,
        tag_legacy=tag_legacy,
    )
    return ok, reason


def pool_coinbase_payout_mode(
    block: dict[str, Any],
    *,
    tag_primary: str,
    ops_address: str,
    tag_legacy: str = "TIDES",
) -> str | None:
    """Return payout_mode if block is ours, else None."""
    ok, _reason, mode = classify_pool_coinbase(
        block,
        tag_primary=tag_primary,
        ops_address=ops_address,
        tag_legacy=tag_legacy,
    )
    return mode if ok else None


async def build_intended_payout_snapshot(
    store: Store,
    settings: Settings,
    *,
    reward_sats: int,
    share_head_seq: int | None,
) -> str:
    """Freeze who *should* have been paid (window at find) for confirm-time audit.

    Call **before** opening this find's finder_credit so pending credit is still
    the prior finder bonus that belonged in this coinbase.
    """
    cutoff = await store.payout_window_cutoff_seq(settings.window_blocks)
    shares = await store.list_shares_after_cutoff(cutoff)
    if share_head_seq is not None:
        shares = [s for s in shares if s.seq <= int(share_head_seq)]
    diff_meta = await store.get_meta("block_difficulty", "1") or "1"
    try:
        block_diff = max(int(float(diff_meta)), 1)
    except ValueError:
        block_diff = 1
    finder, credit = await store.pending_finder_credit()
    tides = split_reward(
        shares,
        reward_sats=int(reward_sats),
        block_difficulty=block_diff,
        window_blocks=settings.window_blocks,
        miner_bps=miner_reward_bps(settings),
        min_output_sats=settings.min_output_sats,
        pool_ops_address=settings.pool_ops_address,
        cutoff_seq=None,  # already trimmed
        window_mode="pool_finds",
    )
    outs = coinbase_suggestion(
        tides,
        pool_ops_address=settings.pool_ops_address or "ops",
        finder_address=finder or "",
        finder_credit_sats=int(credit or 0),
        min_output_sats=settings.min_output_sats,
    )
    payload = {
        "reward_sats": int(reward_sats),
        "cutoff_seq": cutoff,
        "share_head_seq": share_head_seq,
        "finder_address": finder or "",
        "finder_credit_sats": int(credit or 0),
        "outputs": outs,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload, separators=(",", ":"))


def intended_snapshot_from_chain(
    blk: dict[str, Any],
    *,
    height: int,
    reward_sats: int,
    share_head_seq: int | None,
    finder_address: str | None,
    ops_address: str | None,
    existing_snap: str | None = None,
) -> str:
    """Build intended_payout_json from the mined coinbase (truth at confirm)."""
    chain = coinbase_payout_map(blk)
    ops = (ops_address or "").strip()
    cutoff = None
    finder_credit = 0
    if existing_snap:
        try:
            prev = json.loads(existing_snap)
            if isinstance(prev, dict):
                cutoff = prev.get("cutoff_seq")
                finder_credit = int(prev.get("finder_credit_sats") or 0)
        except Exception:  # noqa: BLE001
            pass
    outs = []
    for addr, sats in sorted(chain.items(), key=lambda kv: -kv[1]):
        kind = "ops" if ops and addr == ops else "tides"
        outs.append({"address": addr, "sats": int(sats), "kind": kind, "value": int(sats)})
    payload = {
        "height": int(height),
        "reward_sats": int(reward_sats),
        "cutoff_seq": cutoff,
        "share_head_seq": share_head_seq,
        "finder_address": finder_address or "",
        "finder_credit_sats": int(finder_credit or 0),
        "source": "onchain_refresh_confirm_drift",
        "outputs": outs,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload, separators=(",", ":"))


async def apply_confirm_payout_checks(
    store: Store,
    settings: Settings,
    *,
    height: int,
    blk: dict[str, Any],
    existing_snap: str | None,
    existing_note: str | None,
    share_head_seq: int | None,
    finder_address: str | None,
    reward_fallback: int,
) -> str:
    """At confirm: refresh reward, ensure snapshot, compare chain vs intended.

    Returns final payout_mode:
      - ``onchain_split`` — intended matches chain within per-address dust
      - ``needs_review`` — any cross-user / LISTED_ONLY / CHAIN_ONLY / material
        mismatch (ops must review; unpaid LISTED_ONLY → top up from ops)
      - ``ops_manual`` — true ops-only coinbase
    """
    actual = coinbase_value_sats(blk)
    if actual and actual != int(reward_fallback or 0):
        await store.update_block_reward(int(height), actual)
        log.info(
            "block %s reward corrected %s → %s (incl fees)",
            height,
            reward_fallback,
            actual,
        )
    reward_now = int(actual or reward_fallback or 0)

    _ascii = coinbase_ascii(blk)
    _legacy = str(getattr(settings, "coinbase_tag_legacy", "TIDES") or "TIDES")
    _matched = (
        matched_pool_tag(_ascii, settings.coinbase_tag_primary, _legacy)
        or settings.coinbase_tag_primary
    )
    nick = extract_secondary_tag(_ascii, _matched)
    if nick and finder_address:
        try:
            await store.set_address_nickname(str(finder_address), nick)
        except Exception as exc:  # noqa: BLE001
            log.warning("nickname save failed: %s", exc)

    mode = pool_coinbase_payout_mode(
        blk,
        tag_primary=settings.coinbase_tag_primary,
        ops_address=settings.pool_ops_address,
        tag_legacy=_legacy,
    ) or "onchain_split"

    snap = existing_snap
    if not snap:
        try:
            snap = await build_intended_payout_snapshot(
                store,
                settings,
                reward_sats=reward_now,
                share_head_seq=share_head_seq,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("intended payout snapshot failed: %s", exc)
            snap = None

    note = existing_note
    locked_head: int | None = None
    if mode == "ops_manual":
        note = note or "Coinbase was ops-only; ops will pay miners manually"
    elif snap and mode == "onchain_split":
        listed = block_intended_to_map(snap)
        chain = coinbase_payout_map(blk)
        kind, d = classify_intended_vs_chain(listed, chain, dust_ignore=1000)
        # 1) Dust-only / exact → keep onchain_split.
        # 2) Else try recent coinbaser-cache candidates (snap ring) embedded at
        #    find — rescale to chain total, dust≤1000 OK; lock share_head.
        # 3) Soft ring + same-payee dust drift (tiny LISTED_ONLY/CHAIN_ONLY
        #    crumbs OK, #970411) → refresh intended from chain; lock share_head
        #    from soft-matched ring candidate when available.
        # 4) Material LISTED_ONLY / CHAIN_ONLY → needs_review (ops top-up).
        if kind == "mismatch":
            resolved = False
            try:
                prev = json.loads(snap) if isinstance(snap, str) else {}
            except Exception:  # noqa: BLE001
                prev = {}
            ops = (settings.pool_ops_address or "").strip() or None
            cands = list(prev.get("recent_candidates") or [])
            # Also try the primary outputs after rescale (cache_value → chain).
            cands = [
                {
                    "outputs": prev.get("outputs") or [],
                    "cache_value": prev.get("cache_value"),
                    "max_seq": prev.get("share_head_seq") or prev.get("max_seq"),
                }
            ] + cands
            target = sum(chain.values())
            soft_ring_head: int | None = None
            soft_ring_i: int | None = None
            for i, cand in enumerate(cands):
                raw = block_intended_to_map({"outputs": cand.get("outputs") or []})
                if len(raw) < 2:
                    continue
                scaled = rescale_payment_map(raw, target, dust_addr=ops)
                k2, d2 = classify_intended_vs_chain(scaled, chain, dust_ignore=1000)
                if k2 == "ok":
                    cand_head = int(cand.get("max_seq") or 0)
                    if cand_head > 0:
                        locked_head = cand_head
                    use_head = locked_head if locked_head is not None else share_head_seq
                    payload = {
                        "height": int(height),
                        "reward_sats": reward_now,
                        "cutoff_seq": prev.get("cutoff_seq"),
                        "share_head_seq": use_head,
                        "finder_address": finder_address or prev.get("finder_address") or "",
                        "finder_credit_sats": int(prev.get("finder_credit_sats") or 0),
                        "source": "coinbaser_cache_confirm_match",
                        "matched_candidate": i,
                        "outputs": [
                            {
                                "address": a,
                                "sats": int(s),
                                "value": int(s),
                                "kind": "ops" if ops and a == ops else "tides",
                            }
                            for a, s in sorted(scaled.items(), key=lambda kv: -kv[1])
                        ],
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                    }
                    snap = json.dumps(payload, separators=(",", ":"))
                    note = (
                        f"confirm matched coinbaser cache candidate#{i} "
                        f"(dust≤1000); locked share_head={use_head}; "
                        f"accepted full multi-out"
                    )
                    log.info("block %s %s", height, note)
                    resolved = True
                    break
                # Soft ring match: same-payee dust drift vs chain → remember head
                # so we can lock share_head when we accept chain as amount truth.
                if soft_ring_head is None and is_same_payee_value_drift(d2) and len(scaled) >= 2:
                    soft_ring_i = i
                    soft_ring_head = int(cand.get("max_seq") or 0) or None
            if not resolved and is_same_payee_value_drift(d) and len(chain) >= 2:
                use_head = soft_ring_head if soft_ring_head else share_head_seq
                if soft_ring_head:
                    locked_head = soft_ring_head
                snap = intended_snapshot_from_chain(
                    blk,
                    height=int(height),
                    reward_sats=reward_now,
                    share_head_seq=use_head,
                    finder_address=finder_address,
                    ops_address=settings.pool_ops_address,
                    existing_snap=snap if isinstance(snap, str) else json.dumps(snap),
                )
                # Stamp source for ops/UI: chain truth + optional ring head lock.
                try:
                    payload = json.loads(snap)
                    if soft_ring_head:
                        payload["source"] = "onchain_refresh_ring_soft_match"
                        payload["matched_candidate"] = soft_ring_i
                        payload["share_head_seq"] = use_head
                    else:
                        payload["source"] = "onchain_refresh_confirm_drift"
                    snap = json.dumps(payload, separators=(",", ":"))
                except Exception:  # noqa: BLE001
                    pass
                if soft_ring_head:
                    note = (
                        f"confirm soft-matched coinbaser cache candidate#{soft_ring_i} "
                        f"(same-payee dust); intended refreshed from chain; "
                        f"locked share_head={use_head}; payout_mode=onchain_split"
                    )
                else:
                    note = (
                        "same-payee confirm drift (late shares / coinbaser cache race); "
                        "intended refreshed from on-chain coinbase; payout_mode=onchain_split"
                    )
                log.info("block %s %s", height, note)
                resolved = True
            if not resolved:
                mode = "needs_review"
                note = f"coinbase≠intended (needs review): {d.summary()}"
                log.warning("block %s %s", height, note)
        else:
            # Already ok — if snap carried a cache max_seq, prefer locking to it
            try:
                prev = json.loads(snap) if isinstance(snap, str) else {}
                h = int(prev.get("share_head_seq") or prev.get("max_seq") or 0)
                if h > 0 and share_head_seq and h < int(share_head_seq):
                    locked_head = h
            except Exception:  # noqa: BLE001
                pass

    await store.set_block_payout_meta(
        int(height),
        payout_mode=mode,
        intended_payout_json=snap,
        manual_payout_note=note,
        share_head_seq=locked_head,
    )
    return mode


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


async def adopt_missed_tides_finds(
    store: Store,
    settings: Settings,
    *,
    tip: int,
    lookback: int = 16,
) -> dict:
    """Scan recent tip heights for TIDES+ops coinbases missing from ``blocks``.

    Catches Prime-down / DATUM-disconnect races (e.g. #968837) and makes
    double-finds visible. Inserts with intended-from-chain; confirms when
    deep enough. Logs a clear warning so ops notices even if UI is cached.
    """
    lookback = max(3, min(int(lookback), 64))
    if tip <= 0:
        return {"adopted": 0, "heights": []}

    conf_n = max(int(getattr(settings, "block_confirmations", 2) or 2), 1)
    tag = settings.coinbase_tag_primary
    legacy = str(getattr(settings, "coinbase_tag_legacy", "TIDES") or "TIDES")
    ops = settings.pool_ops_address
    known = {int(b.height) for b in await store.list_blocks(limit=max(lookback * 3, 48))}
    rpc = BitcoinRPC(settings)
    adopted: list[int] = []

    def _scan() -> list[tuple[int, str, dict[str, Any]]]:
        found: list[tuple[int, str, dict[str, Any]]] = []
        for h in range(tip, max(0, tip - lookback) - 1, -1):
            if h in known:
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
            if not coinbase_looks_like_ours(
                blk, tag_primary=tag, ops_address=ops, tag_legacy=legacy
            ):
                continue
            found.append((int(h), hx, blk))
        return found

    candidates = await asyncio.to_thread(_scan)
    for height, hx, blk in candidates:
        reward = coinbase_value_sats(blk) or 0
        mode = pool_coinbase_payout_mode(
            blk, tag_primary=tag, ops_address=ops, tag_legacy=legacy
        ) or "onchain_split"
        # Best-effort finder from secondary tag → unique nickname match
        _ascii = coinbase_ascii(blk)
        _matched = matched_pool_tag(_ascii, tag, legacy) or tag
        nick = sanitize_nickname(
            extract_secondary_tag(_ascii, _matched) or ""
        )
        finder: str | None = None
        if nick:
            try:
                payees = list(coinbase_payout_map(blk).keys())
                nmap = await store.nicknames_for_addresses(payees)
                hits = [a for a, n in nmap.items() if n == nick]
                if len(hits) == 1:
                    finder = hits[0]
            except Exception:  # noqa: BLE001
                pass
        status = "confirmed" if tip >= height + conf_n else "pending"
        note = (
            f"AUTO-ADOPTED missed TIDES find (not in blocks; tip scan). "
            f"Likely Prime/DATUM gap at submit. tag2={nick or '-'}"
        )
        try:
            head = await store.max_share_seq()
            snap = intended_snapshot_from_chain(
                blk,
                height=height,
                reward_sats=int(reward),
                share_head_seq=head,
                finder_address=finder,
                ops_address=ops,
            )
            # Insert first — confirm helpers UPDATE an existing row.
            await store.record_block(
                height=height,
                block_hash=hx,
                difficulty=float(blk.get("difficulty") or 1),
                reward_sats=int(reward),
                finder_address=finder,
                status=status,
                share_head_seq=head,
                payout_mode=mode,
                intended_payout_json=snap,
                manual_payout_done=False,
                manual_payout_note=note,
            )
            if status == "confirmed":
                mode = await apply_confirm_payout_checks(
                    store,
                    settings,
                    height=height,
                    blk=blk,
                    existing_snap=snap,
                    existing_note=note,
                    share_head_seq=head,
                    finder_address=finder,
                    reward_fallback=int(reward),
                )
                if mode == "onchain_split":
                    await store.set_block_payout_meta(
                        height,
                        manual_payout_done=True,
                    )
            try:
                prev = await store.get_meta("last_height")
                if not prev or int(prev) < height:
                    await store.set_meta("last_height", str(height))
            except Exception:  # noqa: BLE001
                await store.set_meta("last_height", str(height))
            adopted.append(height)
            log.warning(
                "MISSED TIDES FIND ADOPTED height=%s hash=%s status=%s mode=%s "
                "finder=%s — was absent from blocks (tip lookback)",
                height,
                hx[:20],
                status,
                mode,
                (finder or "")[:20],
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("adopt missed find %s failed: %s", height, exc)

    return {"adopted": len(adopted), "heights": adopted}


async def reconcile_pool_blocks(store: Store, settings: Settings) -> dict:
    """Advance pending → confirmed/orphaned once tip is N blocks ahead.

    Also tip-scans for TIDES finds missing from ``blocks`` (Prime gap / double find).
    """
    conf_n = max(int(getattr(settings, "block_confirmations", 2) or 2), 1)
    chain_raw = await store.get_meta("chain_height")
    try:
        tip = int(chain_raw) if chain_raw else 0
    except ValueError:
        tip = 0
    if tip <= 0:
        return {"tip": tip, "checked": 0}

    # Always look for unrecorded TIDES tips (cheap lookback).
    miss = await adopt_missed_tides_finds(store, settings, tip=tip)
    adopted = int(miss.get("adopted") or 0)

    pending = await store.list_blocks_by_status("pending", limit=50)
    if not pending:
        return {
            "tip": tip,
            "checked": 0,
            "adopted": adopted,
            "missed_heights": miss.get("heights") or [],
        }

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
            mode = "onchain_split"
            if blk is not None:
                mode = await apply_confirm_payout_checks(
                    store,
                    settings,
                    height=int(b.height),
                    blk=blk,
                    existing_snap=b.intended_payout_json,
                    existing_note=b.manual_payout_note,
                    share_head_seq=b.share_head_seq,
                    finder_address=b.finder_address,
                    reward_fallback=int(b.reward_sats or 0),
                )
                if mode == "ops_manual":
                    log.warning(
                        "block %s confirmed ops_manual hash=%s",
                        b.height,
                        our_hash[:16],
                    )
                elif mode == "needs_review":
                    log.warning(
                        "block %s confirmed needs_review hash=%s",
                        b.height,
                        our_hash[:16],
                    )
            await store.set_block_status(b.height, "confirmed")
            confirmed += 1
            if mode not in ("ops_manual", "needs_review"):
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
                    await apply_confirm_payout_checks(
                        store,
                        settings,
                        height=int(b.height),
                        blk=new_blk,
                        existing_snap=b.intended_payout_json,
                        existing_note=b.manual_payout_note,
                        share_head_seq=b.share_head_seq,
                        finder_address=b.finder_address,
                        reward_fallback=int(b.reward_sats or 0),
                    )
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
                    await apply_confirm_payout_checks(
                        store,
                        settings,
                        height=int(new_h),
                        blk=new_blk,
                        existing_snap=b.intended_payout_json,
                        existing_note=None,
                        share_head_seq=b.share_head_seq,
                        finder_address=b.finder_address,
                        reward_fallback=int(b.reward_sats or 0),
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
        "adopted": adopted,
        "missed_heights": miss.get("heights") or [],
    }
