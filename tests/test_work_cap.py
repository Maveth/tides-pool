from tides_pool.config import Settings


def test_address_work_cap_25gh_20x():
    s = Settings(
        gpu_baseline_hashrate_hs=2.5e9,
        address_work_cap_multiplier=20.0,
        address_work_cap_window_sec=3600,
    )
    cap = s.address_work_cap()
    # ~2095 work/hr * 20 ≈ 41910
    assert 40000 <= cap <= 44000
