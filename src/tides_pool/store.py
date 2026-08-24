"""Share log + pool state. Memory for tests; Postgres for NAS."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import asyncpg

from tides_pool.tides import Share


@dataclass
class ShareRow:
    seq: int
    address: str
    worker: str | None
    work: int
    fee_bps: int
    accepted_at: datetime


@dataclass
class BlockRow:
    height: int
    block_hash: str
    difficulty: float
    reward_sats: int
    finder_address: str | None
    accounted_at: datetime


class Store(ABC):
    @abstractmethod
    async def close(self) -> None: ...

    @abstractmethod
    async def ensure_ready(self) -> None: ...

    @abstractmethod
    async def append_share(
        self,
        address: str,
        work: int,
        *,
        worker: str | None = None,
        fee_bps: int = 0,
    ) -> ShareRow: ...

    @abstractmethod
    async def list_shares_newest(self, limit: int = 10_000) -> list[Share]: ...

    @abstractmethod
    async def list_shares_for_address(
        self, address: str, *, limit: int = 50, offset: int = 0
    ) -> list[ShareRow]: ...

    @abstractmethod
    async def list_share_rows_since(self, since_seconds: int, *, limit: int = 50_000) -> list[ShareRow]: ...

    @abstractmethod
    async def share_count(self) -> int: ...

    @abstractmethod
    async def total_work(self) -> int: ...

    @abstractmethod
    async def record_block(
        self,
        *,
        height: int,
        block_hash: str,
        difficulty: float,
        reward_sats: int,
        finder_address: str | None,
    ) -> None: ...

    @abstractmethod
    async def list_blocks(self, limit: int = 20) -> list[BlockRow]: ...

    @abstractmethod
    async def set_meta(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def get_meta(self, key: str, default: str | None = None) -> str | None: ...

    @abstractmethod
    async def open_finder_credit(self, height: int, address: str, credit_sats: int) -> None: ...

    @abstractmethod
    async def pending_finder_credit(self) -> tuple[str | None, int]: ...

    @abstractmethod
    async def mark_finder_credits_paid(self, paid_in_height: int) -> int: ...

    @abstractmethod
    async def clear_lab_data(self) -> dict: ...


    @abstractmethod
    async def work_for_address_since(self, address: str, since_seconds: int) -> int: ...




class MemoryStore(Store):
    def __init__(self) -> None:
        self._shares: list[ShareRow] = []
        self._seq = 0
        self._blocks: list[BlockRow] = []
        self._meta: dict[str, str] = {}
        self._credits: list[tuple[int, str, int, int | None]] = []  # height, addr, sats, paid

    async def close(self) -> None:
        return None

    async def ensure_ready(self) -> None:
        return None

    async def append_share(
        self,
        address: str,
        work: int,
        *,
        worker: str | None = None,
        fee_bps: int = 0,
    ) -> ShareRow:
        self._seq += 1
        row = ShareRow(
            seq=self._seq,
            address=address,
            worker=worker,
            work=work,
            fee_bps=fee_bps,
            accepted_at=datetime.now(timezone.utc),
        )
        self._shares.append(row)
        return row

    async def list_shares_newest(self, limit: int = 10_000) -> list[Share]:
        rows = list(reversed(self._shares))[:limit]
        return [Share(seq=r.seq, address=r.address, work=r.work, fee_bps=r.fee_bps) for r in rows]

    async def list_shares_for_address(
        self, address: str, *, limit: int = 50, offset: int = 0
    ) -> list[ShareRow]:
        rows = [r for r in reversed(self._shares) if r.address == address]
        return rows[offset : offset + limit]

    async def list_share_rows_since(self, since_seconds: int, *, limit: int = 50_000) -> list[ShareRow]:
        import time

        cutoff = time.time() - max(int(since_seconds), 0)
        out: list[ShareRow] = []
        for r in reversed(self._shares):
            ts = r.accepted_at.timestamp() if r.accepted_at.tzinfo else r.accepted_at.replace(tzinfo=timezone.utc).timestamp()
            if ts < cutoff:
                break
            out.append(r)
            if len(out) >= limit:
                break
        return out

    async def share_count(self) -> int:
        return len(self._shares)

    async def total_work(self) -> int:
        return sum(r.work for r in self._shares)

    async def record_block(
        self,
        *,
        height: int,
        block_hash: str,
        difficulty: float,
        reward_sats: int,
        finder_address: str | None,
    ) -> None:
        self._blocks = [b for b in self._blocks if b.height != height]
        self._blocks.append(
            BlockRow(
                height=height,
                block_hash=block_hash,
                difficulty=difficulty,
                reward_sats=reward_sats,
                finder_address=finder_address,
                accounted_at=datetime.now(timezone.utc),
            )
        )
        self._blocks.sort(key=lambda b: b.height, reverse=True)
        await self.set_meta("last_height", str(height))

    async def list_blocks(self, limit: int = 20) -> list[BlockRow]:
        return self._blocks[:limit]

    async def set_meta(self, key: str, value: str) -> None:
        self._meta[key] = value

    async def get_meta(self, key: str, default: str | None = None) -> str | None:
        return self._meta.get(key, default)

    async def open_finder_credit(self, height: int, address: str, credit_sats: int) -> None:
        self._credits.append((height, address, credit_sats, None))

    async def pending_finder_credit(self) -> tuple[str | None, int]:
        open_ = [c for c in self._credits if c[3] is None]
        if not open_:
            return None, 0
        # newest unpaid
        _, addr, sats, _ = open_[-1]
        return addr, sats

    async def mark_finder_credits_paid(self, paid_in_height: int) -> int:
        n = 0
        updated: list[tuple[int, str, int, int | None]] = []
        for h, addr, sats, paid in self._credits:
            if paid is None:
                updated.append((h, addr, sats, paid_in_height))
                n += 1
            else:
                updated.append((h, addr, sats, paid))
        self._credits = updated
        return n

    async def clear_lab_data(self) -> dict:
        n_shares = len(self._shares)
        n_blocks = len(self._blocks)
        self._shares.clear()
        self._seq = 0
        self._blocks.clear()
        self._credits.clear()
        # keep chain_* meta; drop pool bookkeeping
        for k in ("last_height",):
            self._meta.pop(k, None)
        return {"shares_deleted": n_shares, "blocks_deleted": n_blocks}

    async def work_for_address_since(self, address: str, since_seconds: int) -> int:
        cutoff = datetime.now(timezone.utc).timestamp() - max(since_seconds, 0)
        total = 0
        for r in self._shares:
            ts = r.accepted_at.timestamp() if r.accepted_at.tzinfo else r.accepted_at.replace(tzinfo=timezone.utc).timestamp()
            if r.address == address and ts >= cutoff:
                total += r.work
        return total


class PostgresStore(Store):

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def ensure_ready(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=8)

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    def _p(self) -> asyncpg.Pool:
        if not self._pool:
            raise RuntimeError("store not ready")
        return self._pool

    async def append_share(
        self,
        address: str,
        work: int,
        *,
        worker: str | None = None,
        fee_bps: int = 0,
    ) -> ShareRow:
        async with self._p().acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO users(address) VALUES($1)
                    ON CONFLICT (address) DO UPDATE SET last_seen = now()
                    """,
                    address,
                )
                if worker:
                    await conn.execute(
                        """
                        INSERT INTO workers(address, worker) VALUES($1, $2)
                        ON CONFLICT (address, worker) DO UPDATE SET last_seen = now()
                        """,
                        address,
                        worker,
                    )
                row = await conn.fetchrow(
                    """
                    INSERT INTO shares(address, worker, work, fee_bps)
                    VALUES($1, $2, $3, $4)
                    RETURNING seq, address, worker, work, fee_bps, accepted_at
                    """,
                    address,
                    worker,
                    work,
                    fee_bps,
                )
        return ShareRow(
            seq=row["seq"],
            address=row["address"],
            worker=row["worker"],
            work=row["work"],
            fee_bps=row["fee_bps"],
            accepted_at=row["accepted_at"],
        )

    async def list_shares_newest(self, limit: int = 10_000) -> list[Share]:
        rows = await self._p().fetch(
            """
            SELECT seq, address, work, fee_bps FROM shares
            ORDER BY seq DESC LIMIT $1
            """,
            limit,
        )
        return [
            Share(seq=r["seq"], address=r["address"], work=r["work"], fee_bps=r["fee_bps"])
            for r in rows
        ]

    async def list_shares_for_address(
        self, address: str, *, limit: int = 50, offset: int = 0
    ) -> list[ShareRow]:
        rows = await self._p().fetch(
            """
            SELECT seq, address, worker, work, fee_bps, accepted_at
            FROM shares WHERE address = $1
            ORDER BY seq DESC LIMIT $2 OFFSET $3
            """,
            address,
            limit,
            offset,
        )
        return [
            ShareRow(
                seq=r["seq"],
                address=r["address"],
                worker=r["worker"],
                work=r["work"],
                fee_bps=r["fee_bps"],
                accepted_at=r["accepted_at"],
            )
            for r in rows
        ]

    async def list_share_rows_since(self, since_seconds: int, *, limit: int = 50_000) -> list[ShareRow]:
        rows = await self._p().fetch(
            """
            SELECT seq, address, worker, work, fee_bps, accepted_at
            FROM shares
            WHERE accepted_at >= now() - ($1 * interval '1 second')
            ORDER BY seq DESC
            LIMIT $2
            """,
            int(since_seconds),
            int(limit),
        )
        return [
            ShareRow(
                seq=r["seq"],
                address=r["address"],
                worker=r["worker"],
                work=r["work"],
                fee_bps=r["fee_bps"],
                accepted_at=r["accepted_at"],
            )
            for r in rows
        ]

    async def share_count(self) -> int:
        return int(await self._p().fetchval("SELECT COUNT(*) FROM shares") or 0)

    async def total_work(self) -> int:
        return int(await self._p().fetchval("SELECT COALESCE(SUM(work),0) FROM shares") or 0)

    async def record_block(
        self,
        *,
        height: int,
        block_hash: str,
        difficulty: float,
        reward_sats: int,
        finder_address: str | None,
    ) -> None:
        async with self._p().acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO blocks(height, block_hash, difficulty, reward_sats, finder_address)
                    VALUES($1, $2, $3, $4, $5)
                    ON CONFLICT (height) DO UPDATE SET
                      block_hash = EXCLUDED.block_hash,
                      difficulty = EXCLUDED.difficulty,
                      reward_sats = EXCLUDED.reward_sats,
                      finder_address = EXCLUDED.finder_address,
                      accounted_at = now()
                    """,
                    height,
                    block_hash,
                    difficulty,
                    reward_sats,
                    finder_address,
                )
                # Only track last *pool* height here. Tip difficulty / subsidy
                # estimates stay owned by chain_sync (avoid lab/block pollution).
                await conn.execute(
                    """
                    INSERT INTO meta(key, value) VALUES('last_height', $1)
                    ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                    """,
                    str(height),
                )

    async def list_blocks(self, limit: int = 20) -> list[BlockRow]:
        rows = await self._p().fetch(
            """
            SELECT height, block_hash, difficulty, reward_sats, finder_address, accounted_at
            FROM blocks ORDER BY height DESC LIMIT $1
            """,
            limit,
        )
        return [
            BlockRow(
                height=r["height"],
                block_hash=r["block_hash"],
                difficulty=float(r["difficulty"]),
                reward_sats=r["reward_sats"],
                finder_address=r["finder_address"],
                accounted_at=r["accounted_at"],
            )
            for r in rows
        ]

    async def set_meta(self, key: str, value: str) -> None:
        await self._p().execute(
            """
            INSERT INTO meta(key, value) VALUES($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
            """,
            key,
            value,
        )

    async def get_meta(self, key: str, default: str | None = None) -> str | None:
        val = await self._p().fetchval("SELECT value FROM meta WHERE key = $1", key)
        return default if val is None else str(val)

    async def open_finder_credit(self, height: int, address: str, credit_sats: int) -> None:
        await self._p().execute(
            """
            INSERT INTO finder_credits(from_height, address, credit_sats)
            VALUES($1, $2, $3)
            """,
            height,
            address,
            credit_sats,
        )

    async def pending_finder_credit(self) -> tuple[str | None, int]:
        row = await self._p().fetchrow(
            """
            SELECT address, credit_sats FROM finder_credits
            WHERE paid_in_height IS NULL
            ORDER BY id DESC LIMIT 1
            """
        )
        if not row:
            return None, 0
        return row["address"], int(row["credit_sats"])

    async def mark_finder_credits_paid(self, paid_in_height: int) -> int:
        result = await self._p().execute(
            """
            UPDATE finder_credits
            SET paid_in_height = $1
            WHERE paid_in_height IS NULL
            """,
            paid_in_height,
        )
        # asyncpg returns e.g. "UPDATE 2"
        try:
            return int(str(result).split()[-1])
        except ValueError:
            return 0

    async def clear_lab_data(self) -> dict:
        async with self._p().acquire() as conn:
            async with conn.transaction():
                n_shares = await conn.fetchval("SELECT COUNT(*) FROM shares")
                n_blocks = await conn.fetchval("SELECT COUNT(*) FROM blocks")
                await conn.execute("DELETE FROM finder_credits")
                await conn.execute("DELETE FROM coinbaser_snapshots")
                await conn.execute("DELETE FROM shares")
                await conn.execute("DELETE FROM workers")
                await conn.execute("DELETE FROM blocks")
                await conn.execute("DELETE FROM users")
                await conn.execute("DELETE FROM meta WHERE key = 'last_height'")
                # reset share seq
                await conn.execute("ALTER SEQUENCE shares_seq_seq RESTART WITH 1")
        return {"shares_deleted": int(n_shares or 0), "blocks_deleted": int(n_blocks or 0)}

    async def work_for_address_since(self, address: str, since_seconds: int) -> int:
        val = await self._p().fetchval(
            """
            SELECT COALESCE(SUM(work), 0) FROM shares
            WHERE address = $1
              AND accepted_at >= now() - ($2 * interval '1 second')
            """,
            address,
            int(since_seconds),
        )
        return int(val or 0)


def window_slice(shares_newest_first: list[Share], target_work: int) -> list[Share]:
    out: list[Share] = []
    acc = 0
    for s in shares_newest_first:
        out.append(s)
        acc += s.work
        if acc >= target_work:
            break
    return out


def contributor_rows(
    window: list[Share],
    *,
    recent: list[ShareRow] | None = None,
    hashrate_window_sec: int = 600,
) -> list[dict[str, Any]]:
    work: dict[str, int] = {}
    shares: dict[str, int] = {}
    for s in window:
        work[s.address] = work.get(s.address, 0) + s.work
        shares[s.address] = shares.get(s.address, 0) + 1
    total = sum(work.values()) or 1

    recent_work: dict[str, int] = {}
    if recent:
        for r in recent:
            recent_work[r.address] = recent_work.get(r.address, 0) + r.work

    rows = []
    for addr, w in work.items():
        rw = recent_work.get(addr, 0)
        hs = estimate_hashrate_hs(rw, hashrate_window_sec) if rw else 0.0
        rows.append(
            {
                "address": addr,
                "work": w,
                "share_pct": round(100.0 * w / total, 4),
                "shares": shares.get(addr, 0),
                "hashrate_hs": hs,
            }
        )
    rows.sort(key=lambda r: (-r["work"], r["address"]))
    return rows


# Bitcoin-pool Diff1 convention: expected hashes ≈ difficulty × 2^32
_DIFF1_HASHES = float(1 << 32)


def estimate_hashrate_hs(work_diff1: int | float, window_sec: float) -> float:
    """Rough H/s from Diff1-equivalent share work over a wall-clock window."""
    if window_sec <= 0 or work_diff1 <= 0:
        return 0.0
    return float(work_diff1) * _DIFF1_HASHES / float(window_sec)
