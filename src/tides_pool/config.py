from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TIDES_", env_file=".env", extra="ignore")

    # Process role: "all" (legacy one-box), "web" (HTTP only), "prime" (DATUM Prime + sync).
    # Split so website deploys/restarts do not bounce Gateway sessions.
    role: str = Field(
        default="all",
        description="all | web | prime — which subsystems this process runs",
    )

    # HTTP API
    host: str = "0.0.0.0"
    port: int = 8080
    # When role=web, health probes this host for Prime TCP (compose service name).
    prime_host: str = "127.0.0.1"

    # Postgres
    database_url: str = "postgresql://tides:tides@localhost:5432/tides"

    # Knots / bitcoind RPC (RC3 TN4)
    bitcoin_rpc_url: str = "http://192.168.0.143:48332"
    bitcoin_rpc_user: str = "datum"
    bitcoin_rpc_password: str = "YOUR_LAB_PASSWORD"
    bitcoin_rpc_timeout: float = 15.0
    chain_sync_seconds: int = 15

    # TIDES / fees (basis points of block reward)
    # window_blocks = how many *confirmed pool finds* keep shares in the payout window.
    # Orphans/misattributed finds do NOT count. (Not Ocean's 8×network-difficulty work.)
    window_blocks: int = Field(default=8, ge=1, le=32)
    block_confirmations: int = Field(
        default=2,
        ge=1,
        le=32,
        description="Chain blocks after a find before confirm/orphan verdict",
    )
    fee_bps: int = Field(default=500, description="5% = 500 bps")
    finder_fee_share_bps: int = Field(
        default=5000,
        description="Of the pool fee, 50% goes to previous finder (next coinbase)",
    )

    # Coinbase / dust
    min_output_sats: int = 1000
    # Dedicated fee-keep address (2.5% of block when finder bonus is active; 5% if no prior finder yet)
    pool_ops_address: str = "mqKdiu6W825MWc31NACiwxRchTb4dP2NRH"
    coinbase_tag_primary: str = "TIDES"
    coinbase_tag_secondary: str = "MaVeTh"

    # Block explorer for Recent pool blocks links (lab mempool UI)
    mempool_explorer_url: str = "https://mempool.maveth.ca"

    # Share validation / DATUM configure override_vardiff_min
    min_share_difficulty: float = 4.0
    network: str = "testnet4"

    # Per-address work cap (0 multiplier = disabled → normal pool, full ASIC credit)
    # Expected work/s ≈ hashrate / 2^32. Baseline used only when multiplier > 0.
    gpu_baseline_hashrate_hs: float = 2.5e9
    address_work_cap_multiplier: float = 0.0
    address_work_cap_window_sec: int = 3600

    # DATUM Prime listen (encrypted Gateway pool_host protocol)
    datum_prime_port: int = 28916

    # Quarantine: freeze NEW shares if miner mostly fails coinbaser check
    quarantine_reject27_ratio: float = 0.5
    quarantine_reject27_window: int = 20
    quarantine_reject27_min_samples: int = 3
    # Auto-clear (non-ops) quarantine after this many consecutive good multi-out shares.
    quarantine_rehab_shares: int = Field(default=5, ge=1, le=50)
    # New payout addresses: no window credit until this many consecutive good
    # multi-out shares (do not assume good at first connect).
    probation_good_shares: int = Field(default=5, ge=1, le=50)
    # Comma/space-separated payout addresses that never auto-quarantine (still
    # subject to reject-27 on bad coinbase shares; allowlist only skips the freeze).
    quarantine_allowlist: str = ""
    # Auto-Q scan throttle: clean miners checked every N attempts (in-memory ring).
    # Hot miners (recent reject-27 in ring) are checked every attempt.
    quarantine_check_every_n: int = Field(default=10, ge=1, le=200)

    # Coinbaser split cache: reuse window weights; full reload every N seconds
    # or on invalidate (new confirmed find / finder credit). Gateway work_update
    # is separate (DATUM) — do not lower that to fix Prime load.
    coinbaser_cache_seconds: float = Field(default=15.0, ge=1.0, le=300.0)

    def normalized_role(self) -> str:
        r = (self.role or "all").strip().lower()
        if r in ("web", "www", "ui", "api"):
            return "web"
        if r in ("prime", "datum", "pool"):
            return "prime"
        return "all"

    def runs_prime(self) -> bool:
        return self.normalized_role() in ("all", "prime")

    def runs_web(self) -> bool:
        """HTTP API + static. Prime-only still exposes /health for probes."""
        return True

    def runs_chain_sync(self) -> bool:
        # Only one writer should reconcile blocks / sample net HR.
        return self.normalized_role() in ("all", "prime")

    def quarantine_allowlisted(self, address: str) -> bool:
        addr = (address or "").strip()
        if not addr:
            return False
        raw = self.quarantine_allowlist or ""
        allowed = {a.strip() for a in raw.replace(";", ",").replace(" ", ",").split(",") if a.strip()}
        return addr in allowed

    def address_work_cap(self) -> int:
        """Max Diff1 work credited per address per window. 0 = unlimited (no ASIC throttle)."""
        if self.address_work_cap_multiplier <= 0:
            return 0
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
