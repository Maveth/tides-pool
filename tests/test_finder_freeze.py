"""Finder freeze + intended vs chain confirm helpers."""

from __future__ import annotations

import asyncio
import json

from tides_pool.block_confirm import coinbase_payout_map
from tides_pool.payment_verify import block_intended_to_map, diff_payments
from tides_pool.store import MemoryStore


def test_memory_record_block_freezes_finder():
    async def _run():
        s = MemoryStore()
        await s.record_block(
            height=100,
            block_hash="pool-100-first",
            difficulty=1.0,
            reward_sats=1000,
            finder_address="bc1qfirst",
            status="pending",
        )
        await s.record_block(
            height=100,
            block_hash="a" * 64,
            difficulty=2.0,
            reward_sats=2000,
            finder_address="bc1qthief",
            status="pending",
        )
        blocks = await s.list_blocks(limit=5)
        b = next(x for x in blocks if x.height == 100)
        assert b.finder_address == "bc1qfirst"
        assert b.block_hash == "a" * 64  # real hash may upgrade
        assert b.reward_sats == 2000

    asyncio.run(_run())


def test_memory_open_finder_credit_once_per_height():
    async def _run():
        s = MemoryStore()
        await s.record_block(
            height=50,
            block_hash="x",
            difficulty=1,
            reward_sats=1,
            finder_address="bc1qa",
        )
        await s.open_finder_credit(50, "bc1qa", 100)
        await s.open_finder_credit(50, "bc1qb", 999)
        addr, sats = await s.pending_finder_credit()
        assert addr == "bc1qa"
        assert sats == 100
        # only one row
        assert sum(1 for c in s._credits if c[0] == 50) == 1

    asyncio.run(_run())


def test_confirm_diff_flags_selfish_out():
    intended = {
        "outputs": [
            {"address": "bc1qminer", "sats": 900},
            {"address": "bc1qops", "sats": 100},
        ]
    }
    # chain: miner robbed, attacker paid
    chain_blk = {
        "tx": [
            {
                "vout": [
                    {
                        "value": 0.00000900,
                        "scriptPubKey": {"address": "bc1qattacker"},
                    },
                    {
                        "value": 0.00000100,
                        "scriptPubKey": {"address": "bc1qops"},
                    },
                ]
            }
        ]
    }
    listed = block_intended_to_map(json.dumps(intended))
    chain = coinbase_payout_map(chain_blk)
    d = diff_payments(listed, chain, dust_ignore=0)
    assert not d.ok
    assert "bc1qminer" in d.listed_only or "bc1qminer" in d.amount_mismatch
    assert "bc1qattacker" in d.chain_only
