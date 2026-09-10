"""Fast tip watcher + r27 tip grace for empty jobs after tip change."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

from tides_pool.datum_prime import CoinbaserSplitCache, DatumPrimeSession


def _sess(*, multi_out: bool, grace: bool = False) -> DatumPrimeSession:
    s = object.__new__(DatumPrimeSession)
    s.recent_coinbasers = {}
    if multi_out:
        s.recent_coinbasers[4] = {"n_value_outs": 40}
    cache = object.__new__(CoinbaserSplitCache)
    cache._r27_tip_grace_until = time.monotonic() + 60.0 if grace else 0.0
    cache._r27_tip_grace_sec = 45.0
    s.coinbaser_cache = cache
    return s


def test_empty_nonblock_accepted_during_tip_grace():
    s = _sess(multi_out=True, grace=True)
    ok, why = s._coinbase_id_ok(0, subsidy_only=False, is_block=False)
    assert ok is True
    assert why == ""


def test_empty_nonblock_still_rejected_outside_grace():
    s = _sess(multi_out=True, grace=False)
    ok, why = s._coinbase_id_ok(0, subsidy_only=False, is_block=False)
    assert ok is False
    assert "empty" in why.lower()


def test_ff_nonblock_not_covered_by_tip_grace():
    """Grace is only for empty (cid=0); 0xFF still rejected."""
    s = _sess(multi_out=True, grace=True)
    ok, why = s._coinbase_id_ok(0xFF, subsidy_only=False, is_block=False)
    assert ok is False


def test_note_chain_height_arms_grace_and_tracks_hash():
    cache = object.__new__(CoinbaserSplitCache)
    cache._chain_height_seen = 100
    cache._bestblockhash_seen = "aa" * 32
    cache._r27_tip_grace_until = 0.0
    cache._r27_tip_grace_sec = 45.0
    assert cache.note_chain_height(101, bestblockhash="bb" * 32) is True
    assert cache._chain_height_seen == 101
    assert cache._bestblockhash_seen == "bb" * 32
    assert cache.in_new_tip_r27_grace() is True


def test_note_chain_height_same_tip_no_change():
    cache = object.__new__(CoinbaserSplitCache)
    cache._chain_height_seen = 100
    cache._bestblockhash_seen = "aa" * 32
    cache._r27_tip_grace_until = 0.0
    cache._r27_tip_grace_sec = 45.0
    assert cache.note_chain_height(100, bestblockhash="aa" * 32) is False
    assert cache.in_new_tip_r27_grace() is False


def test_hash_change_same_height_still_counts():
    cache = object.__new__(CoinbaserSplitCache)
    cache._chain_height_seen = 100
    cache._bestblockhash_seen = "aa" * 32
    cache._r27_tip_grace_until = 0.0
    cache._r27_tip_grace_sec = 30.0
    assert cache.note_chain_height(100, bestblockhash="cc" * 32) is True
    assert cache.in_new_tip_r27_grace() is True


def test_invalidate_clears_cached_split():
    cache = object.__new__(CoinbaserSplitCache)
    cache._cached = object()
    cache._last_refresh_error = "x"
    cache.invalidate_coinbaser_for_tip()
    assert cache._cached is None
    assert cache._last_refresh_error is None


async def test_tip_watcher_disabled_when_poll_zero():
    cache = object.__new__(CoinbaserSplitCache)
    cache.settings = SimpleNamespace(tip_poll_seconds=0)
    cache._tip_polls = 0
    # Should return immediately without looping forever
    await asyncio.wait_for(cache.run_tip_watcher(), timeout=2.0)
