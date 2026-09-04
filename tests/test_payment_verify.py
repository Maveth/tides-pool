"""Payments listed (UI/API) must match coinbase outs — regression guard.

Covers:
  - last block: frozen / listed map vs on-chain-style outs
  - current block: live coinbaser suggestion vs the outs that would be mined
  - finder merge (UI tides+finder vs single chain vout)
  - mismatch detection (must fail loud)
"""
from __future__ import annotations

import pytest

from tides_pool.payment_verify import (
    assert_payments_match,
    block_intended_to_map,
    coinbaser_outputs_to_map,
    diff_payments,
    merge_listed_with_finder,
    normalize_payment_map,
)
from tides_pool.tides import Share, coinbase_suggestion, split_reward


OPS = "bc1qopsopsopsopsopsopsopsopsopsopsopsops0"
ALICE = "bc1qalicealicealicealicealicealicealice00"
BOB = "bc1qbobbobbobbobbobbobbobbobbobbobbobbob0"
FINDER = "bc1qfinderfinderfinderfinderfinderfind0"


def _split_and_list(*, reward: int, shares: list[Share], finder: str = "", credit: int = 0):
    tides = split_reward(
        shares,
        reward_sats=reward,
        block_difficulty=1,
        window_blocks=8,
        miner_bps=9500,
        pool_ops_address=OPS,
        cutoff_seq=None,
        window_mode="pool_finds",
    )
    listed = coinbase_suggestion(
        tides,
        pool_ops_address=OPS,
        finder_address=finder,
        finder_credit_sats=credit,
        min_output_sats=1000,
    )
    return tides, listed


def test_normalize_aggregates_duplicate_addresses():
    m = normalize_payment_map(
        [
            {"address": ALICE, "sats": 100},
            {"address": ALICE, "sats": 50},
            {"address": BOB, "sats": 20},
            {"address": "", "sats": 999},
            {"address": BOB, "sats": 0},
        ]
    )
    assert m == {ALICE: 150, BOB: 20}


def test_normalize_knots_btc_float_values():
    m = normalize_payment_map(
        [
            {"address": ALICE, "value": 0.01000000},
            {"scriptpubkey_address": BOB, "value": 500000},  # already sats-ish int
        ]
    )
    assert m[ALICE] == 1_000_000
    assert m[BOB] == 500_000


def test_last_block_listed_matches_coinbase():
    """Last confirmed find: what we listed == what landed in coinbase."""
    shares = [
        Share(seq=3, address=ALICE, work=600),
        Share(seq=2, address=BOB, work=400),
        Share(seq=1, address=OPS, work=0),  # ignored if work 0 — use real work
    ]
    shares = [
        Share(seq=3, address=ALICE, work=600),
        Share(seq=2, address=BOB, work=400),
    ]
    _tides, listed = _split_and_list(reward=10_000_000, shares=shares)
    # Simulate on-chain vouts = same outs the coinbaser produced (last block mined).
    chain = normalize_payment_map(listed)
    d = assert_payments_match(listed, chain, context="last_block")
    assert d.ok
    assert ALICE in d.matched and BOB in d.matched and OPS in d.matched


def test_current_block_coinbaser_matches_would_be_coinbase():
    """Current / next block: /api/coinbaser outs must equal the outs Gateways embed."""
    shares = [
        Share(seq=5, address=ALICE, work=700),
        Share(seq=4, address=BOB, work=300),
    ]
    _tides, listed = _split_and_list(
        reward=50_000_000, shares=shares, finder=FINDER, credit=200_000
    )
    # "Would-be coinbase" is exactly those outputs if this template wins.
    would_be_chain = normalize_payment_map(listed)
    api_shape = {
        "reward_sats_estimate": 50_000_000,
        "outputs": listed,
        "window_work": 1000,
    }
    listed_from_api = coinbaser_outputs_to_map(api_shape)
    assert_payments_match(
        listed_from_api, would_be_chain, context="current_block_coinbaser"
    )
    # Finder credit must appear on-chain merged into finder address.
    assert listed_from_api.get(FINDER, 0) >= 200_000


def test_finder_merge_ui_history_vs_chain():
    """Payment-history tides line alone can disagree; merged map must match chain."""
    chain = {ALICE: 1_500_000, OPS: 500_000}  # Alice tides 1.0M + finder 0.5M
    tides_only = {ALICE: 1_000_000, OPS: 500_000}
    finder_in = {ALICE: 500_000}
    # tides-only must NOT be used for verify
    bad = diff_payments(tides_only, chain)
    assert not bad.ok
    assert ALICE in bad.amount_mismatch
    # merged matches
    assert_payments_match(
        merge_listed_with_finder(tides_only, finder_paid_in=finder_in),
        chain,
        context="last_block_finder_merged",
    )


def test_mismatch_raises_with_clear_context():
    listed = {ALICE: 100, BOB: 50, OPS: 10}
    chain = {ALICE: 100, BOB: 40, OPS: 10}  # Bob short 10
    with pytest.raises(AssertionError, match="last_block.*MISMATCH"):
        assert_payments_match(listed, chain, context="last_block")


def test_listed_only_and_chain_only_detected():
    listed = {ALICE: 100, BOB: 50}
    chain = {ALICE: 100, OPS: 50}
    d = diff_payments(listed, chain)
    assert not d.ok
    assert d.listed_only == {BOB: 50}
    assert d.chain_only == {OPS: 50}


def test_dust_ignore_rounding():
    listed = {ALICE: 1_000_000, OPS: 100}
    chain = {ALICE: 1_000_005, OPS: 95}  # 5 sat noise
    assert_payments_match(listed, chain, dust_ignore=10, context="dust")


def test_intended_payout_json_shapes():
    assert block_intended_to_map({ALICE: 10, BOB: 20}) == {ALICE: 10, BOB: 20}
    assert block_intended_to_map(
        {"outputs": [{"address": ALICE, "sats": 7}, {"address": OPS, "sats": 3}]}
    ) == {ALICE: 7, OPS: 3}
    assert block_intended_to_map(None) == {}


def test_last_and_current_together_regression():
    """One test that exercises both 'last' and 'current' fixtures like a Prime guard."""
    # --- last block (already mined) ---
    last_listed = [
        {"address": ALICE, "sats": 4_000_000, "kind": "tides"},
        {"address": BOB, "sats": 3_000_000, "kind": "tides"},
        {"address": OPS, "sats": 500_000, "kind": "ops"},
    ]
    last_chain = [
        {"address": ALICE, "value": 0.04000000},
        {"address": BOB, "value": 0.03000000},
        {"address": OPS, "value": 0.00500000},
    ]
    assert_payments_match(last_listed, last_chain, context="last_block")

    # --- current (coinbaser preview = template outs) ---
    shares = [
        Share(seq=9, address=ALICE, work=500),
        Share(seq=8, address=BOB, work=500),
    ]
    _t, current_listed = _split_and_list(reward=8_000_000, shares=shares)
    assert_payments_match(
        current_listed,
        normalize_payment_map(current_listed),
        context="current_block",
    )
    # If UI drifted Bob's line, fail:
    drifted = normalize_payment_map(current_listed)
    drifted[BOB] = drifted.get(BOB, 0) + 12345
    with pytest.raises(AssertionError, match="current_block"):
        assert_payments_match(drifted, current_listed, context="current_block")
