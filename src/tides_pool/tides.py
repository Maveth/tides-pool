"""Pure TIDES reward math (no I/O).

Window size = window_blocks × block_difficulty (in difficulty-1 share units).
Shares are ordered newest-first when tallied from a given head.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Share:
    """One distinct proof in the share log (append order = increasing seq)."""

    seq: int
    address: str
    work: int  # difficulty-1 equivalent units (>= 1)
    fee_bps: int = 500  # fee tagged at share time (default matches Settings)


@dataclass(frozen=True, slots=True)
class RewardLine:
    address: str
    sats: int
    work: int


@dataclass(frozen=True, slots=True)
class TidesSplit:
    """Result of splitting one block reward across the share-log window."""

    window_work: int
    miner_budget_sats: int
    ops_sats: int
    dust_sats: int  # leftover from floor rounding, assigned to ops
    lines: tuple[RewardLine, ...]

    @property
    def total_assigned(self) -> int:
        return sum(l.sats for l in self.lines) + self.ops_sats


def window_size(block_difficulty: int, window_blocks: int = 8) -> int:
    if block_difficulty < 1:
        raise ValueError("block_difficulty must be >= 1")
    if window_blocks < 1:
        raise ValueError("window_blocks must be >= 1")
    return block_difficulty * window_blocks


def select_window(
    shares_newest_first: Sequence[Share],
    *,
    block_difficulty: int,
    window_blocks: int = 8,
) -> list[Share]:
    """Walk from the job-issue head (newest) backward until window work is filled.

    `shares_newest_first[0]` must be the share-log head at **job issue** time
    (Ocean anti-cheat), not necessarily wall-clock find time.
    """
    target = window_size(block_difficulty, window_blocks)
    selected: list[Share] = []
    acc = 0
    for s in shares_newest_first:
        if s.work < 1:
            raise ValueError(f"share seq={s.seq} has non-positive work")
        selected.append(s)
        acc += s.work
        if acc >= target:
            break
    return selected


def tally_work(shares: Iterable[Share]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in shares:
        out[s.address] = out.get(s.address, 0) + s.work
    return out


def split_reward(
    shares_newest_first: Sequence[Share],
    *,
    reward_sats: int,
    block_difficulty: int,
    window_blocks: int = 8,
    miner_bps: int = 9000,
    min_output_sats: int = 0,
    pool_ops_address: str = "",
) -> TidesSplit:
    """Split `reward_sats` using TIDES.

    - miner_bps: portion of reward for miners after pool fee (e.g. 9000 = 90%).
    - Per-share fee flags are applied as: each share's contribution to the miner
      budget is scaled by (10000 - fee_bps) / 10000 relative to a full share.
      For v1 uniform fees, pass shares all tagged with the same fee_bps and set
      miner_bps = 10000 - fee; the per-share path still works for mixed rates.

    Rounding: each miner line floors to sats; dust goes to ops.
    Outputs below min_output_sats are folded into dust/ops (Ocean dust behavior).
    """
    if reward_sats < 0:
        raise ValueError("reward_sats must be >= 0")
    if not (0 <= miner_bps <= 10_000):
        raise ValueError("miner_bps out of range")

    window = select_window(
        shares_newest_first,
        block_difficulty=block_difficulty,
        window_blocks=window_blocks,
    )
    if not window:
        ops = reward_sats
        return TidesSplit(
            window_work=0,
            miner_budget_sats=0,
            ops_sats=ops,
            dust_sats=0,
            lines=(),
        )

    # Effective work after per-share fee: work * (10000 - fee_bps) / 10000
    # Denominator for proportions uses fee-adjusted work so mixed fee rates work.
    eff: dict[str, int] = {}
    raw: dict[str, int] = {}
    total_eff = 0
    total_raw = 0
    for s in window:
        adj = s.work * (10_000 - s.fee_bps) // 10_000
        # If fee already taken via miner_bps globally, shares may use fee_bps=0
        # and miner_bps=9000. Support both: prefer per-share when any fee_bps>0
        # and miner_bps==10000; else apply miner_bps on top of raw work.
        raw[s.address] = raw.get(s.address, 0) + s.work
        total_raw += s.work
        eff[s.address] = eff.get(s.address, 0) + adj
        total_eff += adj

    use_per_share_fee = any(s.fee_bps > 0 for s in window) and miner_bps == 10_000
    if use_per_share_fee:
        weights = eff
        weight_sum = total_eff
        miner_budget = reward_sats  # fees already removed in weights via fee_bps
        # Actually when fee_bps on shares, miner budget should still be full reward
        # with fee embedded in reduced weights — leftover is ops.
        # Ocean: fee deducted per share then remainder to miners.
        # Equivalent: miner_budget = sum over shares of floor(share_frac * R * (1-f))
        # We compute ops as R - sum(miner lines).
        miner_budget = reward_sats
        weights = eff
        weight_sum = total_eff if total_eff > 0 else total_raw
        if total_eff == 0:
            weights = raw
            weight_sum = total_raw
    else:
        weights = raw
        weight_sum = total_raw
        miner_budget = reward_sats * miner_bps // 10_000

    if weight_sum <= 0:
        return TidesSplit(
            window_work=total_raw,
            miner_budget_sats=0,
            ops_sats=reward_sats,
            dust_sats=0,
            lines=(),
        )

    lines: list[RewardLine] = []
    assigned = 0
    for address, w in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        if w <= 0:
            continue
        sats = (miner_budget * w) // weight_sum
        if sats < min_output_sats:
            continue
        lines.append(RewardLine(address=address, sats=sats, work=raw.get(address, w)))
        assigned += sats

    dust = miner_budget - assigned
    if use_per_share_fee:
        # Ops = everything not paid to miners (includes fee + rounding dust)
        ops = reward_sats - assigned
        dust_sats = dust if dust > 0 else 0
    else:
        ops = reward_sats - miner_budget + max(dust, 0)
        dust_sats = max(dust, 0)

    if pool_ops_address and ops > 0:
        # ops stay as ops_sats; caller adds pool_ops_address as separate output
        pass

    return TidesSplit(
        window_work=total_raw,
        miner_budget_sats=miner_budget if not use_per_share_fee else assigned,
        ops_sats=ops,
        dust_sats=dust_sats,
        lines=tuple(lines),
    )


def apply_finder_credit(
    lines: Sequence[RewardLine],
    *,
    finder_address: str,
    credit_sats: int,
    ops_sats: int,
    min_output_sats: int = 0,
) -> tuple[list[RewardLine], int]:
    """Add previous-finder bonus into the coinbaser suggestion.

    Returns (updated_lines, remaining_ops_sats). Credit is taken from ops budget
    that was reserved for fee (caller should have reserved finder+ops from fee).
    """
    if credit_sats <= 0 or not finder_address:
        return list(lines), ops_sats
    pay = min(credit_sats, ops_sats)
    if pay < min_output_sats and pay != credit_sats:
        return list(lines), ops_sats

    out = list(lines)
    for i, line in enumerate(out):
        if line.address == finder_address:
            out[i] = RewardLine(address=line.address, sats=line.sats + pay, work=line.work)
            return out, ops_sats - pay
    out.append(RewardLine(address=finder_address, sats=pay, work=0))
    return out, ops_sats - pay


def coinbase_suggestion(
    tides: TidesSplit,
    *,
    pool_ops_address: str,
    finder_address: str = "",
    finder_credit_sats: int = 0,
    min_output_sats: int = 1000,
) -> list[dict]:
    """Build ordered coinbase output dicts for DATUM Gateway consumption."""
    lines, ops_left = apply_finder_credit(
        tides.lines,
        finder_address=finder_address,
        credit_sats=finder_credit_sats,
        ops_sats=tides.ops_sats,
        min_output_sats=min_output_sats,
    )
    outputs: list[dict] = [
        {"address": ln.address, "sats": ln.sats, "kind": "tides"} for ln in lines if ln.sats > 0
    ]
    if finder_address and finder_credit_sats > 0:
        # mark finder line if present
        for o in outputs:
            if o["address"] == finder_address and o.get("kind") == "tides":
                o["kind"] = "tides+finder"
                break
    if pool_ops_address and ops_left >= min_output_sats:
        outputs.append({"address": pool_ops_address, "sats": ops_left, "kind": "ops"})
    elif ops_left > 0 and outputs:
        # fold residual ops into largest miner line if ops addr missing / dust
        outputs[0]["sats"] += ops_left
    return outputs


def estimate_window_work_needed(block_difficulty: int, window_blocks: int = 8) -> int:
    return window_size(block_difficulty, window_blocks)


def summarize_shares(shares: Sequence[Share]) -> Mapping[str, int]:
    return tally_work(shares)
