"""Reject empty (cb_id=0) non-block shares once multi-out coinbaser was assigned.

DATUM Gateways empty-blast / stick on reject-path jobs; accepting those shares
for minutes lets them find ops-only blocks. Policy:

  - multi-out assigned + cid=0 + non-block → REJECT (nudge GW for new template)
  - multi-out assigned + cid=0 + block     → ACCEPT (ops_manual find)
  - multi-out assigned + cid=0xFF / subsidy + non-block → REJECT (unchanged)
  - multi-out assigned + cid=0xFF / subsidy + block     → ACCEPT (unchanged)
  - no multi-out yet → ACCEPT empty (still warming / first coinbaser)
"""
from __future__ import annotations

from tides_pool.datum_prime import DatumPrimeSession


def _sess(*, multi_out: bool) -> DatumPrimeSession:
    """Minimal stand-in: only recent_coinbasers + _coinbase_id_ok matter."""
    s = object.__new__(DatumPrimeSession)
    s.recent_coinbasers = {}
    if multi_out:
        s.recent_coinbasers[4] = {"n_value_outs": 40}
    return s


def test_empty_nonblock_rejected_when_multi_out_assigned():
    s = _sess(multi_out=True)
    ok, why = s._coinbase_id_ok(0, subsidy_only=False, is_block=False)
    assert ok is False
    assert "empty" in why.lower() or "multi-out" in why.lower()


def test_empty_block_still_accepted_when_multi_out_assigned():
    s = _sess(multi_out=True)
    ok, why = s._coinbase_id_ok(0, subsidy_only=False, is_block=True)
    assert ok is True
    assert why == ""


def test_empty_accepted_before_any_multi_out():
    s = _sess(multi_out=False)
    ok, why = s._coinbase_id_ok(0, subsidy_only=False, is_block=False)
    assert ok is True
    assert why == ""


def test_ff_nonblock_still_rejected_when_multi_out():
    s = _sess(multi_out=True)
    ok, why = s._coinbase_id_ok(0xFF, subsidy_only=False, is_block=False)
    assert ok is False
    assert "multi-out" in why.lower()


def test_ff_block_still_accepted_when_multi_out():
    s = _sess(multi_out=True)
    ok, why = s._coinbase_id_ok(0xFF, subsidy_only=False, is_block=True)
    assert ok is True


def test_subsidy_flag_nonblock_rejected_when_multi_out():
    s = _sess(multi_out=True)
    ok, why = s._coinbase_id_ok(4, subsidy_only=True, is_block=False)
    assert ok is False


def test_real_multi_out_id_accepted():
    s = _sess(multi_out=True)
    ok, why = s._coinbase_id_ok(4, subsidy_only=False, is_block=False)
    assert ok is True
    assert why == ""


def test_assigned_multi_out_helper():
    s = _sess(multi_out=False)
    assert s._assigned_multi_out() is False
    s._remember_coinbaser(7, 1)
    assert s._assigned_multi_out() is False
    s._remember_coinbaser(8, 2)
    assert s._assigned_multi_out() is True
