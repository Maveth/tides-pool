"""SV1 / local-GW Stratum Endpoint nick stamp (no wait for good shares).

Unit tests only — no Docker, no Postgres, no live Prime.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from tides_pool.config import Settings
from tides_pool.datum_prime import (
    _STRATUM_ENDPOINT_NICK,
    DatumPrimeSession,
    QuarantineGuard,
)

# Well-formed mainnet bech32 (same family as live SV1 payouts).
_ADDR = "bc1qj30fwc353ketu3nwm0lq0gzmmygu4qn4nh5h8m"


class _FakeStore:
    def __init__(self) -> None:
        self.nicks: dict[str, str] = {}

    async def set_address_nickname(self, address: str, nickname: str) -> None:
        self.nicks[address] = nickname


def _session(*, peer_ip: str, store: _FakeStore | None = None) -> DatumPrimeSession:
    store = store or _FakeStore()
    settings = Settings(local_work_fee_peers="127.0.0.1,::1,172.16.13.1")
    sess = object.__new__(DatumPrimeSession)
    sess.settings = settings
    sess.store = store
    sess.peer_ip = peer_ip
    sess.coinbaser_cache = type("C", (), {"_sv1_stratum_nick_stamped": set()})()
    sess.qguard = QuarantineGuard(settings)
    return sess


def test_stamp_on_fee_peer():
    store = _FakeStore()
    sess = _session(peer_ip="172.16.13.1", store=store)
    asyncio.run(sess._stamp_sv1_stratum_nick(_ADDR))
    assert store.nicks[_ADDR] == _STRATUM_ENDPOINT_NICK
    assert sess.qguard.nick_status(_ADDR) == (True, True)


def test_no_stamp_on_remote_peer():
    store = _FakeStore()
    sess = _session(peer_ip="8.8.8.8", store=store)
    asyncio.run(sess._stamp_sv1_stratum_nick(_ADDR))
    assert store.nicks == {}


def test_stamp_debounced():
    store = _FakeStore()
    store.set_address_nickname = AsyncMock(wraps=store.set_address_nickname)
    sess = _session(peer_ip="172.16.13.1", store=store)

    async def _run() -> None:
        await sess._stamp_sv1_stratum_nick(_ADDR)
        await sess._stamp_sv1_stratum_nick(_ADDR)
        await sess._stamp_sv1_stratum_nick(_ADDR)

    asyncio.run(_run())
    assert store.set_address_nickname.await_count == 1


def test_stamp_mapped_ipv4():
    """Docker often presents ::ffff:172.16.13.1."""
    store = _FakeStore()
    sess = _session(peer_ip="::ffff:172.16.13.1", store=store)
    asyncio.run(sess._stamp_sv1_stratum_nick(_ADDR))
    assert store.nicks[_ADDR] == _STRATUM_ENDPOINT_NICK


def test_stamp_skips_empty():
    store = _FakeStore()
    sess = _session(peer_ip="172.16.13.1", store=store)
    asyncio.run(sess._stamp_sv1_stratum_nick(""))
    assert store.nicks == {}


def test_stratum_nick_constant():
    # Must match website AUTO_NICK_GROUP_NAMES / Contributors section.
    assert _STRATUM_ENDPOINT_NICK.lower() == "stratum endpoint"


def test_fee_peer_helper_accepts_mapped_ipv4():
    s = Settings(local_work_fee_peers="127.0.0.1,::1,172.16.13.1")
    assert s.is_local_work_fee_peer("172.16.13.1")
    assert s.is_local_work_fee_peer("::ffff:172.16.13.1")
    assert not s.is_local_work_fee_peer("203.0.113.9")
