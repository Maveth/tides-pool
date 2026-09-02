from __future__ import annotations

from tides_pool.tides import (
    Share,
    apply_finder_credit,
    coinbase_suggestion,
    select_window,
    split_reward,
    window_size,
)


def test_window_size():
    assert window_size(100, 8) == 800


def test_select_window_stops_at_target():
    shares = [
        Share(seq=5, address="a", work=300),
        Share(seq=4, address="b", work=300),
        Share(seq=3, address="a", work=300),
        Share(seq=2, address="c", work=300),
    ]
    # newest first; target = 2 * 100 = 200? use diff=100 blocks=2 → 200
    w = select_window(shares, block_difficulty=100, window_blocks=2)
    assert sum(s.work for s in w) >= 200
    assert w[0].seq == 5
    assert len(w) == 1  # first share alone is 300 >= 200


def test_split_proportional_90_10():
    # 4 equal shares from two miners, window fits all
    shares = [
        Share(seq=4, address="alice", work=100, fee_bps=0),
        Share(seq=3, address="bob", work=100, fee_bps=0),
        Share(seq=2, address="alice", work=100, fee_bps=0),
        Share(seq=1, address="bob", work=100, fee_bps=0),
    ]
    reward = 1_000_000
    split = split_reward(
        shares,
        reward_sats=reward,
        block_difficulty=50,  # window = 400, exactly all work
        window_blocks=8,
        miner_bps=9000,
        min_output_sats=0,
    )
    by = {ln.address: ln.sats for ln in split.lines}
    assert by["alice"] == by["bob"]
    assert by["alice"] + by["bob"] == 900_000
    assert split.ops_sats == 100_000
    assert split.total_assigned == reward


def test_short_log_uses_available_work():
    shares = [Share(seq=1, address="solo", work=10, fee_bps=0)]
    split = split_reward(
        shares,
        reward_sats=1000,
        block_difficulty=1000,  # window target huge
        window_blocks=8,
        miner_bps=9000,
        min_output_sats=0,
    )
    assert split.window_work == 10
    assert split.lines[0].address == "solo"
    assert split.lines[0].sats == 900
    assert split.ops_sats == 100


def test_dust_min_output_folded():
    shares = [
        Share(seq=2, address="big", work=999, fee_bps=0),
        Share(seq=1, address="tiny", work=1, fee_bps=0),
    ]
    split = split_reward(
        shares,
        reward_sats=100_000,
        block_difficulty=125,
        window_blocks=8,
        miner_bps=9000,
        min_output_sats=5000,  # tiny share earns ~90 sats → dropped
    )
    addrs = {ln.address for ln in split.lines}
    assert "tiny" not in addrs
    assert "big" in addrs
    assert split.ops_sats + sum(ln.sats for ln in split.lines) == 100_000


def test_finder_credit_from_ops():
    lines = []
    from tides_pool.tides import RewardLine

    lines = [RewardLine(address="alice", sats=900, work=100)]
    updated, ops_left = apply_finder_credit(
        lines,
        finder_address="bob",
        credit_sats=80,
        ops_sats=100,
    )
    assert ops_left == 20
    assert any(l.address == "bob" and l.sats == 80 for l in updated)


def test_coinbase_suggestion_fee_split():
    shares = [
        Share(seq=2, address="alice", work=50, fee_bps=0),
        Share(seq=1, address="bob", work=50, fee_bps=0),
    ]
    tides = split_reward(
        shares,
        reward_sats=1_000_000,
        block_difficulty=12,
        window_blocks=8,
        miner_bps=9000,
        min_output_sats=0,
    )
    # ops = 100k; pay 80k finder credit to bob (previous finder)
    outs = coinbase_suggestion(
        tides,
        pool_ops_address="ops",
        finder_address="bob",
        finder_credit_sats=80_000,
        min_output_sats=0,
    )
    by = {o["address"]: o["sats"] for o in outs}
    assert by["ops"] == 20_000
    assert by["bob"] == 450_000 + 80_000  # tides half of 900k + finder
    assert by["alice"] == 450_000
    assert sum(by.values()) == 1_000_000


def test_window_rotates_old_shares_out():
    """As new work arrives, older shares fall out of the 8×diff window."""
    # window = 8 * 10 = 80
    alice = [
        Share(seq=8, address="alice", work=20),
        Share(seq=7, address="alice", work=20),
        Share(seq=6, address="alice", work=20),
        Share(seq=5, address="alice", work=20),
    ]
    bob_on_top = [
        Share(seq=12, address="bob", work=20),
        Share(seq=11, address="bob", work=20),
        Share(seq=10, address="bob", work=20),
        Share(seq=9, address="bob", work=20),
    ] + alice
    w = select_window(bob_on_top, block_difficulty=10, window_blocks=8)
    assert all(s.address == "bob" for s in w)
    assert not any(s.address == "alice" for s in w)
    split = split_reward(
        bob_on_top,
        reward_sats=8000,
        block_difficulty=10,
        window_blocks=8,
        miner_bps=9000,
        min_output_sats=0,
    )
    assert {ln.address for ln in split.lines} == {"bob"}

