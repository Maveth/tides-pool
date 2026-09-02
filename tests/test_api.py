from __future__ import annotations

from fastapi.testclient import TestClient

from tides_pool.api import app


client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["network"] == "testnet4"


def test_ui_index():
    r = client.get("/")
    assert r.status_code == 200
    assert "TIDES" in r.text
    assert "/static/app.js" in r.text


def test_lab_share_and_stats():
    client.post(
        "/lab/block",
        params={"finder": "mBootstrap", "height": 1, "difficulty": 100, "reward_sats": 1_000_000},
    )
    r = client.post("/lab/share", params={"address": "mAlice", "work": 100, "worker": "rig1"})
    assert r.status_code == 200
    r = client.post("/lab/share", params={"address": "mBob", "work": 100})
    assert r.status_code == 200
    stats = client.get("/api/stats").json()
    assert stats["share_log_work"] >= 200
    assert stats["window_work_target"] == 800
    user = client.get("/api/user/mAlice").json()
    assert user["work_in_window"] >= 100
    shares = client.get("/api/user/mAlice/shares").json()
    assert len(shares) >= 1
    assert shares[0]["worker"] == "rig1"
    contrib = client.get("/api/contributors").json()
    assert any(c["address"] == "mAlice" for c in contrib)


def test_lab_block_opens_finder_credit():
    client.post("/lab/share", params={"address": "mFinder", "work": 50})
    r = client.post(
        "/lab/block",
        params={"finder": "mFinder", "height": 42, "difficulty": 1, "reward_sats": 1_000_000},
    )
    assert r.status_code == 200
    assert r.json()["pending_credit_sats"] == 80_000
    coin = client.get("/api/coinbaser").json()
    assert coin["reward_sats_estimate"] == 1_000_000
    assert any(o["address"] == "mFinder" for o in coin["outputs"])
    blocks = client.get("/api/blocks").json()
    assert any(b["height"] == 42 for b in blocks)
    stats = client.get("/api/stats").json()
    assert "last_pool_block_height" in stats
    assert "chain_height" in stats

