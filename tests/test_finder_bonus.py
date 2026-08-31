import pytest
from fastapi.testclient import TestClient

from tides_pool.api import app


client = TestClient(app)


def test_lab_block_opens_finder_then_coinbaser_includes_bonus():
    client.post("/api/admin/clear-lab", params={"confirm": "YES"})
    # seed share so TIDES has a miner line
    client.post("/api/lab/share", params={"address": "n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5", "work": 100})
    client.post(
        "/api/lab/block",
        params={
            "finder": "n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5",
            "height": 200,
            "difficulty": 100,
            "reward_sats": 1_000_000,
        },
    )
    stats = client.get("/api/stats").json()
    assert stats["pending_finder_address"] == "n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5"
    assert stats["pending_finder_credit_sats"] == 40_000  # 4% of lab reward 1e6 (80% of 5% fee)

    coin = client.get("/api/coinbaser").json()
    by = {o["address"]: o["sats"] for o in coin["outputs"]}
    # finder should appear with TIDES share + bonus (or bonus line)
    assert "n1Qve4H9J1b16iYKQwVNKH64JvEHWK8Fg5" in by
    # ops keeps (5% of coinbaser reward estimate) minus the fixed pending finder credit
    fee_addr = stats["pool_ops_address"]
    assert fee_addr in by
    rew = int(coin["reward_sats_estimate"])
    assert by[fee_addr] == pytest.approx(rew * 500 // 10_000 - 40_000, abs=5)
