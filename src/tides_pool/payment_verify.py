"""Compare listed pool payments to on-chain (or would-be) coinbase outs.

Goal: catch UI / API / reconstruction bugs before miners do. Listed amounts that
users see (coinbaser preview, frozen intended snapshot, merged Payment history)
must match the coinbase address→sats map for the block in question.

Pure functions — safe for unit tests and for optional live audits.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence


def normalize_payment_map(
    outputs: Iterable[Any],
    *,
    address_key: str = "address",
    sats_key: str = "sats",
) -> dict[str, int]:
    """Collapse outputs to address → sats (sum duplicates, drop empty/zero)."""
    out: dict[str, int] = {}
    for item in outputs:
        if item is None:
            continue
        if isinstance(item, Mapping):
            addr = str(item.get(address_key) or item.get("scriptpubkey_address") or "")
            if "sats" in item or sats_key in item:
                sats = int(item.get(sats_key) or item.get("sats") or 0)
            elif "value" in item:
                # BTC float (Knots) or already-sats int
                v = item["value"]
                if isinstance(v, float):
                    sats = int(round(v * 1e8))
                else:
                    sats = int(v)
            else:
                sats = 0
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            addr, sats = str(item[0]), int(item[1])
        else:
            continue
        addr = addr.strip()
        if not addr or sats == 0:
            continue
        out[addr] = out.get(addr, 0) + int(sats)
    return out


def merge_listed_with_finder(
    tides_by_addr: Mapping[str, int],
    *,
    finder_paid_in: Mapping[str, int] | None = None,
) -> dict[str, int]:
    """Payment-history tides lines + finder bonus paid in this coinbase.

    On-chain usually merges finder into the same address vout — compare that map
    to chain, not tides-only UI lines.
    """
    out = {a: int(s) for a, s in tides_by_addr.items() if int(s)}
    for a, s in (finder_paid_in or {}).items():
        if int(s) == 0:
            continue
        out[a] = out.get(a, 0) + int(s)
    return out


@dataclass
class PaymentDiff:
    matched: dict[str, int] = field(default_factory=dict)
    amount_mismatch: dict[str, tuple[int, int]] = field(default_factory=dict)
    # addr → (listed_sats, chain_sats)
    listed_only: dict[str, int] = field(default_factory=dict)
    chain_only: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.amount_mismatch and not self.listed_only and not self.chain_only

    def summary(self) -> str:
        if self.ok:
            return f"OK ({len(self.matched)} address(es) match)"
        parts = []
        for a, (li, ch) in sorted(self.amount_mismatch.items()):
            parts.append(f"MISMATCH {a}: listed={li} chain={ch} delta={li - ch:+d}")
        for a, s in sorted(self.listed_only.items()):
            parts.append(f"LISTED_ONLY {a}: {s}")
        for a, s in sorted(self.chain_only.items()):
            parts.append(f"CHAIN_ONLY {a}: {s}")
        return "; ".join(parts)


def diff_payments(
    listed: Mapping[str, int] | Iterable[Any],
    chain: Mapping[str, int] | Iterable[Any],
    *,
    dust_ignore: int = 0,
) -> PaymentDiff:
    """Compare listed payment map to coinbase outs.

    dust_ignore: abs deltas ≤ this (sats) are treated as match (rounding).
    """
    L = (
        dict(listed)
        if isinstance(listed, Mapping)
        else normalize_payment_map(listed)  # type: ignore[arg-type]
    )
    C = (
        dict(chain)
        if isinstance(chain, Mapping)
        else normalize_payment_map(chain)  # type: ignore[arg-type]
    )
    # Drop dust-only dust_ignore entries from both sides for presence checks
    if dust_ignore > 0:
        L = {a: s for a, s in L.items() if abs(s) > dust_ignore or a in C}
        C = {a: s for a, s in C.items() if abs(s) > dust_ignore or a in L}

    result = PaymentDiff()
    for a in sorted(set(L) | set(C)):
        li = int(L.get(a, 0))
        ch = int(C.get(a, 0))
        if li == 0 and ch == 0:
            continue
        if li == 0:
            result.chain_only[a] = ch
        elif ch == 0:
            result.listed_only[a] = li
        elif abs(li - ch) <= dust_ignore:
            result.matched[a] = ch
        else:
            result.amount_mismatch[a] = (li, ch)
    return result


def classify_intended_vs_chain(
    listed: Mapping[str, int] | Iterable[Any],
    chain: Mapping[str, int] | Iterable[Any],
    *,
    dust_ignore: int = 1000,
    total_dust: int = 0,  # kept for call-site compat; unused
) -> tuple[str, PaymentDiff]:
    """Return (kind, diff) where kind is ``ok`` | ``mismatch``.

    - ok: every address within dust_ignore (same-user lag only)
    - mismatch: anything else — including cross-user reshuffle, LISTED_ONLY,
      CHAIN_ONLY, or total mismatch. Confirm may auto-resolve same-payee
      value drift via recent coinbaser-cache snaps / on-chain refresh;
      LISTED_ONLY stays needs_review + ops top-up.

    Cross-user value move is CRITICAL even when totals match (e.g. #968456:
    15 miners omitted on-chain, ~0.0665 BTC folded into ops).
    """
    del total_dust  # API compat
    L = (
        dict(listed)
        if isinstance(listed, Mapping)
        else normalize_payment_map(listed)  # type: ignore[arg-type]
    )
    C = (
        dict(chain)
        if isinstance(chain, Mapping)
        else normalize_payment_map(chain)  # type: ignore[arg-type]
    )
    d = diff_payments(L, C, dust_ignore=dust_ignore)
    if d.ok:
        return "ok", d
    return "mismatch", d


def is_same_payee_value_drift(
    diff: PaymentDiff,
    *,
    max_chain_only_sats: int = 150_000,
    max_listed_only_sats: int = 150_000,
) -> bool:
    """True when payee sets match (modulo tiny crumbs) and amounts differ.

    Classic late-share / coinbaser-cache race (#968420, #968646, #968827, #969431, #970411):
    same miners on chain vs intended, values drifted — not a material LISTED_ONLY truncate.

    Allow small CHAIN_ONLY outs (dust floors / fee crumbs / late tiny payee) and small
    LISTED_ONLY crumbs (dust-floor fold into ops on the winning template) so confirm can
    auto-refresh intended from chain instead of needs_review. Material unpaid miners
    (large LISTED_ONLY) still disqualify.
    """
    listed_extra = int(sum(int(v) for v in (diff.listed_only or {}).values()))
    if listed_extra > int(max_listed_only_sats):
        return False
    chain_extra = int(sum(int(v) for v in (diff.chain_only or {}).values()))
    if chain_extra > int(max_chain_only_sats):
        return False
    return bool(diff.amount_mismatch) or chain_extra > 0 or listed_extra > 0


def rescale_payment_map(
    amounts: Mapping[str, int],
    target_total: int,
    *,
    dust_addr: str | None = None,
) -> dict[str, int]:
    """Proportionally rescale address→sats to ``target_total`` (floor + dust)."""
    src = {a: int(v) for a, v in amounts.items() if int(v) > 0}
    target = int(target_total or 0)
    if target <= 0 or not src:
        return dict(src)
    total = sum(src.values())
    if total <= 0:
        return dict(src)
    if total == target:
        return dict(src)
    out: dict[str, int] = {}
    assigned = 0
    # Stable order: largest first so dust lands predictably
    items = sorted(src.items(), key=lambda kv: (-kv[1], kv[0]))
    for i, (a, v) in enumerate(items):
        if i == len(items) - 1:
            # last gets remainder to preserve total (prefer dust_addr if set)
            continue
        sats = (target * v) // total
        if sats > 0:
            out[a] = sats
            assigned += sats
    last_a, _last_v = items[-1]
    rem = target - assigned
    sink = dust_addr if dust_addr and dust_addr in src else last_a
    if rem > 0:
        out[sink] = out.get(sink, 0) + rem
    return {a: s for a, s in out.items() if s > 0}


def assert_payments_match(
    listed: Mapping[str, int] | Iterable[Any],
    chain: Mapping[str, int] | Iterable[Any],
    *,
    dust_ignore: int = 0,
    context: str = "",
) -> PaymentDiff:
    """Raise AssertionError with a clear message if listed ≠ coinbase."""
    d = diff_payments(listed, chain, dust_ignore=dust_ignore)
    if not d.ok:
        prefix = f"{context}: " if context else ""
        raise AssertionError(prefix + d.summary())
    return d


def coinbaser_outputs_to_map(coinbaser_payload: Mapping[str, Any]) -> dict[str, int]:
    """`/api/coinbaser` JSON → address map (current / next-block listed payments)."""
    return normalize_payment_map(coinbaser_payload.get("outputs") or [])


def block_intended_to_map(intended: Any) -> dict[str, int]:
    """blocks.intended_payout_json / API intended field → address map."""
    if intended is None or intended == "":
        return {}
    if isinstance(intended, str):
        import json

        intended = json.loads(intended)
    if isinstance(intended, Mapping):
        # either {addr: sats} or {outputs: [...]}
        if "outputs" in intended:
            return normalize_payment_map(intended["outputs"])  # type: ignore[arg-type]
        # plain addr→sats
        if all(isinstance(v, (int, float)) for v in intended.values()):
            return {str(k): int(v) for k, v in intended.items() if int(v)}
    if isinstance(intended, Sequence):
        return normalize_payment_map(intended)
    return {}
