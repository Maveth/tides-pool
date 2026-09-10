"""Period luck% with changing network difficulty."""
from tides_pool.api import period_luck_pct


def test_constant_diff_matches_classic():
    assert period_luck_pct(find_diffs=[100, 100], pool_work=400) == 50.0


def test_retarget_weights_by_find_diff():
    assert period_luck_pct(find_diffs=[100.0, 300.0], pool_work=400) == 100.0


def test_no_work_returns_none():
    assert period_luck_pct(find_diffs=[100], pool_work=0) is None


def test_work_but_no_finds_is_zero():
    assert period_luck_pct(find_diffs=[], pool_work=1000) == 0.0


def test_ignores_nonpositive_diffs():
    assert period_luck_pct(find_diffs=[0, -1, 200], pool_work=100) == 200.0
