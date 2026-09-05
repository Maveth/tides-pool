"""Cheap share PoT / work caps (no full header blake2b yet)."""

from __future__ import annotations

from tides_pool.config import Settings
from tides_pool.datum_prime import (
    ntime_skew_ok,
    share_work_from_target_byte,
    target_byte_allowed,
)


def test_share_target_byte_min_from_difficulty():
    s = Settings(min_share_difficulty=2048)
    assert s.share_target_byte_min() == 11
    s4 = Settings(min_share_difficulty=4)
    assert s4.share_target_byte_min() == 2


def test_target_byte_allowed_band():
    assert target_byte_allowed(0xFF, is_block=False, min_tb=11, max_tb_share=28, max_tb_block=48)
    assert target_byte_allowed(11, is_block=False, min_tb=11, max_tb_share=28, max_tb_block=48)
    assert target_byte_allowed(28, is_block=False, min_tb=11, max_tb_share=28, max_tb_block=48)
    assert not target_byte_allowed(10, is_block=False, min_tb=11, max_tb_share=28, max_tb_block=48)
    assert not target_byte_allowed(29, is_block=False, min_tb=11, max_tb_share=28, max_tb_block=48)
    # blocks may claim higher PoT
    assert target_byte_allowed(40, is_block=True, min_tb=11, max_tb_share=28, max_tb_block=48)
    assert not target_byte_allowed(49, is_block=True, min_tb=11, max_tb_share=28, max_tb_block=48)
    assert not target_byte_allowed(63, is_block=False, min_tb=0, max_tb_share=62, max_tb_block=62)


def test_work_clamped_to_ceiling():
    # attacker claims 2^40
    w = share_work_from_target_byte(40, min_share_difficulty=2048, work_ceiling=1 << 28)
    assert w == 1 << 28
    w2 = share_work_from_target_byte(11, min_share_difficulty=2048, work_ceiling=1 << 28)
    assert w2 == 2048
    w3 = share_work_from_target_byte(0xFF, min_share_difficulty=2048, work_ceiling=1 << 28)
    assert w3 == 2048


def test_ntime_skew():
    now = 1_700_000_000.0
    assert ntime_skew_ok(int(now), now=now, max_skew_sec=7200)
    assert ntime_skew_ok(int(now) + 7199, now=now, max_skew_sec=7200)
    assert not ntime_skew_ok(int(now) + 7201, now=now, max_skew_sec=7200)
    assert not ntime_skew_ok(int(now) - 14401, now=now, max_skew_sec=7200)
