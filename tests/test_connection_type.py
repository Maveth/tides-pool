
"""shares.connection_type tagging (unit / memory store)."""
import asyncio
from tides_pool.store import MemoryStore

def test_append_share_connection_type():
    store = MemoryStore()
    async def _run():
        await store.ensure_ready()
        r1 = await store.append_share("bc1qtest", 100, worker="w1", connection_type="sv1")
        r2 = await store.append_share("bc1qtest", 50, worker="w2", connection_type="datum")
        r3 = await store.append_share("bc1qtest", 10, worker="STRATUM FEE")
        assert r1.connection_type == "sv1"
        assert r2.connection_type == "datum"
        assert r3.connection_type is None
        # invalid coerced to None
        r4 = await store.append_share("bc1qtest", 1, worker="x", connection_type="nope")
        assert r4.connection_type is None
        br = await store.worker_breakdown_after_cutoff(None, addresses=["bc1qtest"])
        types = {(w["worker"], w.get("connection_type")) for w in br["bc1qtest"]}
        assert ("w1", "sv1") in types
        assert ("w2", "datum") in types
    asyncio.run(_run())
