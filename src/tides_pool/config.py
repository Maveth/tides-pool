from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIDES_", env_file=".env", extra="ignore")

    # HTTP API
    host: str = "0.0.0.0"
    port: int = 8080

    # Postgres
    database_url: str = "postgresql://tides:tides@localhost:5432/tides"

    # Knots / bitcoind RPC (RC2 TN4)
    bitcoin_rpc_url: str = "http://192.168.0.143:48332"
    bitcoin_rpc_user: str = "datum"
    bitcoin_rpc_password: str = "YOUR_LAB_PASSWORD"
    bitcoin_rpc_timeout: float = 15.0
    chain_sync_seconds: int = 15

    # TIDES / fees (basis points of block reward)
    window_blocks: int = Field(default=8, ge=1, le=32)
    fee_bps: int = Field(default=500, description="5% = 500 bps")
    finder_fee_share_bps: int = Field(
        default=8000,
        description="Of the pool fee, 80% goes to previous finder (4% of block); ops keep 20% of fee (1% of block)",
    )

    # Coinbase / dust
    min_output_sats: int = 1000
    # Dedicated fee-keep address (1% of block when finder bonus is active; 5% if no prior finder yet)
    pool_ops_address: str = "mqKdiu6W825MWc31NACiwxRchTb4dP2NRH"
    coinbase_tag_primary: str = "TIDES"
    coinbase_tag_secondary: str = "MaVeTh"

    # Share validation / DATUM configure override_vardiff_min
    min_share_difficulty: float = 4.0
    network: str = "testnet4"

    # Per-address work cap (GPU-friendly pool)
    # Expected work/s ≈ hashrate / 2^32. Baseline 2.5 GH/s → ~2095 work/hour;
    # 20× cap → ~41910 work/hour credited per address (rolling window).
    gpu_baseline_hashrate_hs: float = 2.5e9
    address_work_cap_multiplier: float = 20.0
    address_work_cap_window_sec: int = 3600

    # DATUM Prime listen (encrypted Gateway pool_host protocol)
    datum_prime_port: int = 28916

    def address_work_cap(self) -> int:
        """Max difficulty-1 work units credited per address per rolling window."""
        work_per_sec = self.gpu_baseline_hashrate_hs / float(2**32)
        raw = work_per_sec * self.address_work_cap_window_sec * self.address_work_cap_multiplier
        return max(int(raw), 1)




def miner_reward_bps(settings: Settings) -> int:
    return 10_000 - settings.fee_bps


def finder_credit_bps(settings: Settings) -> int:
    """Fraction of full block reward owed to previous finder (e.g. 800)."""
    return (settings.fee_bps * settings.finder_fee_share_bps) // 10_000


def ops_keep_bps(settings: Settings) -> int:
    return settings.fee_bps - finder_credit_bps(settings)
