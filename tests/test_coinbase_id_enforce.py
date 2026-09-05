"""Require known coinbase_id when multi-out coinbaser was assigned."""

from __future__ import annotations

from tides_pool.datum_prime import DatumPrimeSession


class _FakeSession:
    def __init__(self) -> None:
        self.recent_coinbasers: dict[int, dict] = {}

    _assigned_multi_out = DatumPrimeSession._assigned_multi_out
    _coinbase_id_ok = DatumPrimeSession._coinbase_id_ok
    _remember_coinbaser = DatumPrimeSession._remember_coinbaser


def test_no_multi_out_allows_any_id():
    s = _FakeSession()
    ok, why = s._coinbase_id_ok(0, subsidy_only=False)
    assert ok and why == ""


def test_multi_out_rejects_zero_only():
    s = _FakeSession()
    s._remember_coinbaser(3, 5)
    ok, why = s._coinbase_id_ok(0, subsidy_only=False)
    assert not ok and "multi-out" in why
    ok, why = s._coinbase_id_ok(0xFF, subsidy_only=True)
    assert not ok
    # Unknown non-zero id allowed again (ring check false-quarantined real GWs).
    ok, why = s._coinbase_id_ok(9, subsidy_only=False)
    assert ok and why == ""
    ok, why = s._coinbase_id_ok(3, subsidy_only=False)
    assert ok and why == ""


def test_remember_ring_keeps_recent_ids():
    s = _FakeSession()
    for i in range(30):
        s._remember_coinbaser(i, 4)
    assert len(s.recent_coinbasers) <= 24
    # newest still present
    assert 29 in s.recent_coinbasers
