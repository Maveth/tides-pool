from tides_pool.config import Settings


def test_role_normalization(monkeypatch):
    monkeypatch.setenv("TIDES_ROLE", "WEB")
    s = Settings()
    assert s.normalized_role() == "web"
    assert s.runs_prime() is False
    assert s.runs_chain_sync() is False

    monkeypatch.setenv("TIDES_ROLE", "prime")
    s = Settings()
    assert s.normalized_role() == "prime"
    assert s.runs_prime() is True
    assert s.runs_chain_sync() is True

    monkeypatch.setenv("TIDES_ROLE", "all")
    s = Settings()
    assert s.normalized_role() == "all"
    assert s.runs_prime() is True
    assert s.runs_chain_sync() is True


def test_default_role_is_all(monkeypatch):
    monkeypatch.delenv("TIDES_ROLE", raising=False)
    s = Settings()
    assert s.normalized_role() == "all"
