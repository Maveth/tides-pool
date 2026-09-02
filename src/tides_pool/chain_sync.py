"""Pull tip / difficulty / subsidy estimate from Knots into pool meta; confirm pool finds."""

from __future__ import annotations

import asyncio
import logging

from tides_pool.bitcoin_rpc import BitcoinRPC, BitcoinRPCError
from tides_pool.block_confirm import reconcile_pool_blocks
from tides_pool.config import Settings
from tides_pool.store import Store

log = logging.getLogger("tides_pool.chain_sync")


async def sync_once(store: Store, settings: Settings) -> dict:
    rpc = BitcoinRPC(settings)

    def _pull() -> dict:
        info = rpc.getblockchaininfo()
        mining = rpc.getmininginfo()
        height = int(info["blocks"])
        difficulty = float(info.get("difficulty") or mining.get("difficulty") or 1)
        # Estimate next block reward: subsidy only for now (fees unknown until found)
        try:
            subsidy = rpc.estimatesubsidy(height + 1)
        except BitcoinRPCError:
            subsidy = 50 * 100_000_000
        return {
            "height": height,
            "difficulty": difficulty,
            "bestblockhash": info.get("bestblockhash"),
            "chain": info.get("chain"),
            "reward_estimate_sats": subsidy,
            "networkhashps": mining.get("networkhashps"),
        }

    data = await asyncio.to_thread(_pull)
    # Store as meta — do NOT invent pool blocks or shares
    await store.set_meta("chain_height", str(data["height"]))
    await store.set_meta("block_difficulty", str(int(data["difficulty"])))
    await store.set_meta("reward_estimate", str(int(data["reward_estimate_sats"])))
    await store.set_meta("bestblockhash", str(data.get("bestblockhash") or ""))
    await store.set_meta("chain", str(data.get("chain") or ""))
    if data.get("networkhashps") is not None:
        await store.set_meta("networkhashps", str(data["networkhashps"]))
    log.info(
        "chain sync: height=%s diff=%s subsidy≈%s",
        data["height"],
        int(data["difficulty"]),
        data["reward_estimate_sats"],
    )
    try:
        recon = await reconcile_pool_blocks(store, settings)
        if recon.get("checked"):
            log.info("block reconcile: %s", recon)
            data["reconcile"] = recon
    except Exception as exc:  # noqa: BLE001
        log.warning("block reconcile failed: %s", exc)
    return data


async def chain_sync_loop(store: Store, settings: Settings, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await sync_once(store, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("chain sync failed: %s", exc)
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(settings.chain_sync_seconds, 5))
        except asyncio.TimeoutError:
            pass
