"""Settlement ledger: history/totals from coinbase / sendmany, not share replay."""

from __future__ import annotations

import json

import pytest

from tides_pool.miner_payouts import (
    SOURCE_ONCHAIN,
    SOURCE_PENDING,
    SOURCE_SENDMANY,
    STATUS_PAID,
    STATUS_PENDING,
    plan_sync_for_block,
    rows_from_intended,
    rows_from_manual_adjustment,
    pick_display_rows,
)
from tides_pool.store import MemoryStore


def test_rows_from_intended_sums_duplicate_addrs():
    snap = {
        "outputs": [
            {"address": "a", "sats": 100, "kind": "tides"},
            {"address": "a", "sats": 50, "kind": "bonus"},
            {"address": "b", "sats": 20, "kind": "tides"},
        ]
    }
    rows = rows_from_intended(10, snap, source=SOURCE_ONCHAIN, status=STATUS_PAID)
    by = {r.address: r for r in rows}
    assert by["a"].sats == 150
    assert by["a"].kind == "bonus"
    assert by["b"].sats == 20


def test_rows_from_sendmany_paid_only():
    adj = {
        "status": "pending_maturity",
        "pays": [{"address": "a", "pay_sats": 99, "kind": "tides"}],
    }
    assert rows_from_manual_adjustment(1, adj) == []

    adj2 = {
        "status": "paid",
        "txid": "deadbeef",
        "paid_at": "2026-09-09T23:00:00+00:00",
        "pays": [
            {"address": "a", "pay_sats": 100, "kind": "tides"},
            {"address": "b", "sats": 50, "kind": "bonus"},
        ],
    }
    rows = rows_from_manual_adjustment(2, adj2)
    assert {r.address: r.sats for r in rows} == {"a": 100, "b": 50}
    assert all(r.source == SOURCE_SENDMANY and r.status == STATUS_PAID for r in rows)


def test_plan_prefers_sendmany_over_onchain():
    intended = {"outputs": [{"address": "a", "sats": 1, "kind": "tides"}]}
    adj = {
        "status": "paid",
        "pays": [{"address": "a", "pay_sats": 999, "kind": "tides"}],
    }
    rows, clear = plan_sync_for_block(
        height=3,
        payout_mode="ops_manual",
        block_status="confirmed",
        intended=intended,
        adjustment=adj,
    )
    assert len(rows) == 1 and rows[0].sats == 999
    assert SOURCE_PENDING in clear and SOURCE_ONCHAIN in clear


def test_plan_onchain_split_paid():
    intended = {"outputs": [{"address": "a", "sats": 42, "kind": "tides"}]}
    rows, clear = plan_sync_for_block(
        height=4,
        payout_mode="onchain_split",
        block_status="confirmed",
        intended=intended,
        adjustment=None,
    )
    assert rows[0].source == SOURCE_ONCHAIN and rows[0].sats == 42
    assert SOURCE_ONCHAIN in clear


def test_plan_ops_manual_pending():
    intended = {"outputs": [{"address": "a", "sats": 7, "kind": "tides"}]}
    rows, clear = plan_sync_for_block(
        height=5,
        payout_mode="ops_manual",
        block_status="confirmed",
        intended=intended,
        adjustment={"status": "pending_maturity", "pays": []},
    )
    assert rows[0].source == SOURCE_PENDING and rows[0].status == STATUS_PENDING
    assert clear == [SOURCE_PENDING]


def test_pick_display_prefers_paid():
    from tides_pool.miner_payouts import MinerPayoutRow

    rows = [
        MinerPayoutRow(1, "a", 10, "tides", SOURCE_PENDING, STATUS_PENDING),
        MinerPayoutRow(1, "a", 99, "tides", SOURCE_SENDMANY, STATUS_PAID),
    ]
    got = pick_display_rows(rows)
    assert len(got) == 1 and got[0].sats == 99


@pytest.mark.asyncio
async def test_memory_store_sync_on_payout_meta():
    store = MemoryStore()
    await store.ensure_ready()
    await store.record_block(
        height=100,
        block_hash="abc",
        difficulty=1.0,
        reward_sats=1000,
        finder_address="finder",
        status="confirmed",
        payout_mode="onchain_split",
        intended_payout_json=json.dumps(
            {"outputs": [{"address": "miner1", "sats": 800, "kind": "tides"}]}
        ),
    )
    rows = await store.list_miner_payouts_for_address("miner1")
    assert len(rows) == 1
    assert rows[0].sats == 800 and rows[0].status == "paid"
    assert await store.sum_miner_payouts_paid("miner1") == 800

    # Ops sendmany replaces pending/onchain for ops_manual height
    await store.record_block(
        height=101,
        block_hash="def",
        difficulty=1.0,
        reward_sats=1000,
        finder_address="finder",
        status="confirmed",
        payout_mode="ops_manual",
        intended_payout_json=json.dumps(
            {"outputs": [{"address": "miner1", "sats": 500, "kind": "tides"}]}
        ),
    )
    pending = await store.list_miner_payouts_for_address("miner1")
    assert any(r.height == 101 and r.status == "pending" for r in pending)

    await store.set_meta(
        "manual_adjustment_101",
        json.dumps(
            {
                "status": "paid",
                "txid": "tx1",
                "pays": [{"address": "miner1", "pay_sats": 500, "kind": "tides"}],
            }
        ),
    )
    rows = [r for r in await store.list_miner_payouts_for_address("miner1") if r.height == 101]
    assert len(rows) == 1
    assert rows[0].source == SOURCE_SENDMANY and rows[0].sats == 500
    assert await store.sum_miner_payouts_paid("miner1") == 800 + 500
