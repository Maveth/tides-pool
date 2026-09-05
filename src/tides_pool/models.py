from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Ops health for /health + status strip.

    status: ok | degraded | down
    """

    status: str = "ok"
    version: str
    network: str
    checks: dict = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class PoolStats(BaseModel):
    share_log_work: int = 0
    share_count: int = 0
    window_work_target: int = 0
    window_work_filled: int = 0
    addresses_in_window: int = 0
    last_pool_block_height: int | None = None
    last_pool_block_at: datetime | None = None  # accounted_at of latest non-orphan find
    last_pool_block_age_sec: int | None = None
    blocks_last_24h: int = 0  # confirmed + pending only (orphans excluded)
    orphans_last_24h: int = 0
    blocks_last_7d: int = 0
    orphans_last_7d: int = 0
    chain_height: int | None = None
    block_difficulty: int = 1
    reward_estimate_sats: int = 0
    pending_finder_address: str | None = None
    pending_finder_credit_sats: int = 0
    fee_bps: int = 1000
    finder_fee_share_bps: int = 8000
    window_blocks: int = 8
    window_mode: str = "pool_finds"  # last N confirmed pool finds
    window_confirmed_finds: int = 0
    window_cutoff_seq: int | None = None
    # Window luck% = 100 * finds_in_window * network_diff / window_work
    # (expected blocks ≈ work/diff when work is Diff1-sum). 100 = on pace.
    window_luck_pct: float | None = None
    window_luck_finds: int = 0
    block_confirmations: int = 2
    network: str = "testnet4"
    pool_name: str = "TIDES lab"
    pool_ops_address: str = ""
    rpc_ok: bool = False
    # Live Knots identity from getnetworkinfo (e.g. /Satoshi:29.4.1/Knots:20260508/)
    node_subversion: str | None = None
    node_version: int | None = None  # bip147-style packed version int from getnetworkinfo
    address_work_cap: int = 0
    address_work_cap_window_sec: int = 3600
    gpu_baseline_hs: float = 0.0
    # Rough pool hashrate from recent accepted share work (Diff1 × 2^32 / Δt)
    hashrate_hs: float = 0.0  # ~10 minute average
    hashrate_window_sec: int = 600
    hashrate_work: int = 0
    hashrate_shares: int = 0
    hashrate_hs_1h: float = 0.0  # ~1 hour average
    hashrate_window_1h_sec: int = 3600
    hashrate_note: str = (
        "Estimate: Σ(share_work) × 2^32 / window_sec. "
        "share_work is Diff1 units from Gateway target_byte (often 4 at vardiff floor)."
    )
    # Network (Knots getnetworkhashps) + pool luck-of-the-draw estimates
    network_hashrate_hs: float = 0.0
    pool_network_share_pct: float = 0.0  # pool_hs / network_hs * 100
    est_block_time_sec: float | None = None  # difficulty * 2^32 / pool_hs
    # Rough miner payout if you ran 1 TH/s for a day (7d find rate × reward × 1TH/pool_hs).
    est_sats_per_day_per_ths: float | None = None




class WorkerBreak(BaseModel):
    """Per-worker slice of an address's window (or coinbaser line)."""

    worker: str
    shares: int = 0
    work: int = 0
    share_pct: float = 0.0  # of this address's window work
    hashrate_hs: float = 0.0
    sats: int | None = None  # proportional coinbaser sats when known


class UserStats(BaseModel):
    address: str
    work_in_window: int = 0
    share_pct: float = 0.0
    estimated_next_sats: int = 0
    pending_finder_credit_sats: int = 0
    # Lifetime coinbase reconstruction (TIDES share lines + paid finder bonuses).
    total_earned_sats: int = 0
    # Open finder bonus(es) not yet paid in a coinbase (excludes est. next tides share).
    unpaid_pending_sats: int = 0
    share_count_shown: int = 0
    workers: list[str] = Field(default_factory=list)
    worker_breakdown: list[WorkerBreak] = Field(default_factory=list)
    quarantined: bool = False
    quarantine_reason: str | None = None
    reject27_recent: int = 0
    attempt_recent: int = 0
    # Most recent pool find attributed to this address (as block finder).
    last_find_height: int | None = None
    last_find_at: datetime | None = None
    last_find_age_sec: int | None = None


class ShareOut(BaseModel):
    seq: int
    address: str
    worker: str | None = None
    work: int
    fee_bps: int
    accepted_at: datetime


class UserPayoutOut(BaseModel):
    """One reconstructed coinbase credit for a miner address."""

    height: int
    block_hash: str | None = None
    kind: str  # "tides" | "finder"
    sats: int
    status: str  # "confirmed" | "pending" | "unpaid"
    accounted_at: datetime | None = None
    paid_in_height: int | None = None


class Contributor(BaseModel):
    address: str
    work: int  # total work in full payout window (7 confirmed + current)
    work_current: int = 0  # work since last confirmed pool find (this block only)
    share_pct: float
    shares: int = 0
    shares_current: int = 0
    hashrate_hs: float = 0.0
    # live = ~10m HR; idle = this-block work but quiet; offline = no this-block work
    activity: str = "idle"  # "live" | "idle" | "offline"
    # 0 = still hashing this unfinished block; N = last shares were during the Nth-last confirmed find
    last_share_blocks_ago: int = 0
    last_share_block_height: int | None = None  # confirmed height when ago>=1; None if CURRENT
    quarantined: bool = False
    quarantine_reason: str | None = None
    nickname: str | None = None  # last coinbase secondary tag seen for this address
    # Finder luck in the payout window: 100 * finds_by_addr * net_diff / work.
    # None when no finds by this address in-window (or no work).
    luck_pct: float | None = None
    luck_finds: int = 0
    workers: list[WorkerBreak] = Field(default_factory=list)


class BlockOut(BaseModel):
    height: int
    block_hash: str
    difficulty: float
    reward_sats: int
    finder_address: str | None
    finder_worker: str | None = None  # stratum worker that submitted the block share
    finder_nickname: str | None = None  # coinbase secondary tag at find time (nickname)
    accounted_at: datetime
    status: str = "confirmed"
    orphan_reason: str | None = None
    share_head_seq: int | None = None
    # onchain_split | ops_manual (ops-only coinbase; ops pays miners off-chain)
    payout_mode: str = "onchain_split"
    manual_payout_done: bool = False
    manual_payout_note: str | None = None
    intended_payout: list | dict | None = None  # parsed snapshot for UI/ops


class CoinbaseOutput(BaseModel):
    address: str
    sats: int
    kind: str = "tides"
    name: str = ""  # stratum worker label(s) for this payout address, when known
    nickname: str | None = None  # last known coinbase secondary tag for address
    workers: list[WorkerBreak] = Field(default_factory=list)


class CoinbaserResponse(BaseModel):
    reward_sats_estimate: int
    outputs: list[CoinbaseOutput]
    window_work: int
    share_log_head_seq: int | None = None
