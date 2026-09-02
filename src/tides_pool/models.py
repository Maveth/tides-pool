from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    network: str


class PoolStats(BaseModel):
    share_log_work: int = 0
    share_count: int = 0
    window_work_target: int = 0
    window_work_filled: int = 0
    addresses_in_window: int = 0
    last_pool_block_height: int | None = None
    blocks_last_24h: int = 0  # confirmed + pending only (orphans excluded)
    orphans_last_24h: int = 0
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
    block_confirmations: int = 2
    network: str = "testnet4"
    pool_name: str = "TIDES lab"
    pool_ops_address: str = ""
    rpc_ok: bool = False
    address_work_cap: int = 0
    address_work_cap_window_sec: int = 3600
    gpu_baseline_hs: float = 0.0
    # Rough pool hashrate from recent accepted share work (Diff1 × 2^32 / Δt)
    hashrate_hs: float = 0.0
    hashrate_window_sec: int = 600
    hashrate_work: int = 0
    hashrate_shares: int = 0
    hashrate_note: str = (
        "Estimate: Σ(share_work) × 2^32 / window_sec. "
        "share_work is Diff1 units from Gateway target_byte (often 4 at vardiff floor)."
    )




class UserStats(BaseModel):
    address: str
    work_in_window: int = 0
    share_pct: float = 0.0
    estimated_next_sats: int = 0
    pending_finder_credit_sats: int = 0
    share_count_shown: int = 0
    workers: list[str] = Field(default_factory=list)
    quarantined: bool = False
    quarantine_reason: str | None = None
    reject27_recent: int = 0
    attempt_recent: int = 0


class ShareOut(BaseModel):
    seq: int
    address: str
    worker: str | None = None
    work: int
    fee_bps: int
    accepted_at: datetime


class Contributor(BaseModel):
    address: str
    work: int
    share_pct: float
    shares: int = 0
    hashrate_hs: float = 0.0
    quarantined: bool = False
    quarantine_reason: str | None = None


class BlockOut(BaseModel):
    height: int
    block_hash: str
    difficulty: float
    reward_sats: int
    finder_address: str | None
    accounted_at: datetime
    status: str = "confirmed"
    orphan_reason: str | None = None
    share_head_seq: int | None = None


class CoinbaseOutput(BaseModel):
    address: str
    sats: int
    kind: str = "tides"
    name: str = ""  # stratum worker name for this payout address, when known


class CoinbaserResponse(BaseModel):
    reward_sats_estimate: int
    outputs: list[CoinbaseOutput]
    window_work: int
    share_log_head_seq: int | None = None
