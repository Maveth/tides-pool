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
    status: str = "confirmed"
    share_head_seq: int | None = None
    orphan_reason: str | None = None


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
    @abstractmethod
    async def record_block(
        self,
        *,
        height: int,
        block_hash: str,
        difficulty: float,
        reward_sats: int,
        finder_address: str | None,
        status: str = "pending",
        share_head_seq: int | None = None,
    ) -> None: ...

    @abstractmethod
    async def list_blocks(self, limit: int = 20) -> list[BlockRow]: ...

    @abstractmethod
    async def list_blocks_by_status(self, status: str, limit: int = 50) -> list[BlockRow]: ...

    @abstractmethod
    async def list_confirmed_blocks(self, limit: int = 20) -> list[BlockRow]: ...

    @abstractmethod
    async def max_share_seq(self) -> int: ...

    @abstractmethod
    async def payout_window_cutoff_seq(self, window_finds: int) -> int | None: ...

    @abstractmethod
    async def set_block_status(self, height: int, status: str, *, orphan_reason: str | None = None) -> None: ...

    @abstractmethod
    async def mark_block_orphaned(self, height: int, *, reason: str) -> None: ...

    @abstractmethod
    async def reassign_pending_block(
        self,
        *,
        old_height: int,
        new_height: int,
        new_hash: str,
        finder_address: str | None,
        reason: str,
    ) -> None: ...

    @abstractmethod
    async def set_meta(self, key: str, value: str) -> None: ...

    @abstractmethod
    async def get_meta(self, key: str, default: str | None = None) -> str | None: ...

    @abstractmethod
    async def open_finder_credit(self, height: int, address: str, credit_sats: int) -> None: ...

    @abstractmethod
    async def pending_finder_credit(self) -> tuple[str | None, int]: ...

    @abstractmethod
    async def pending_finder_credit_id(self) -> int | None: ...

    @abstractmethod
    async def mark_finder_credits_paid(self, paid_in_height: int) -> int: ...

    @abstractmethod
    async def mark_finder_credit_paid(self, credit_id: int, paid_in_height: int) -> int: ...

    @abstractmethod
    async def clear_lab_data(self) -> dict: ...


    @abstractmethod
    async def work_for_address_since(self, address: str, since_seconds: int) -> int: ...




    @abstractmethod
    async def record_share_attempt(
        self,
        address: str,
        *,
        accepted: bool,
        reason_code: int = 0,
        why: str = "",
        worker: str | None = None,
        is_block: bool = False,
    ) -> None: ...

    @abstractmethod
    async def get_quarantine(self, address: str) -> dict | None: ...

    @abstractmethod
    async def set_quarantine(self, address: str, reason: str) -> None: ...

    @abstractmethod
    async def clear_quarantine(self, address: str) -> None: ...

    @abstractmethod
    async def list_quarantines(self, addresses: list[str]) -> dict[str, dict]: ...

    @abstractmethod
    async def recent_attempt_stats(self, address: str, limit: int = 20) -> tuple[int, int]:
        """Returns (reject27_count, total_attempts) over last N attempts."""


    @abstractmethod
    async def consecutive_good_attempts(self, address: str, *, limit: int = 20) -> int:
        """Count trailing accepted (reason 0) attempts for address."""


class MemoryStore(Store):
    def __init__(self) -> None:
        self._shares: list[ShareRow] = []
        self._seq = 0
        self._blocks: list[BlockRow] = []
        self._meta: dict[str, str] = {}
        self._credits: list[tuple[int, str, int, int | None]] = []  # height, addr, sats, paid
        self._attempts: list[dict] = []
        self._quarantine: dict[str, dict] = {}

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
        status: str = "pending",
        share_head_seq: int | None = None,
    ) -> None:
        self._blocks = [b for b in self._blocks if b.height != height]
        head = share_head_seq if share_head_seq is not None else self._seq
        self._blocks.append(
            BlockRow(
                height=height,
                block_hash=block_hash,
                difficulty=difficulty,
                reward_sats=reward_sats,
                finder_address=finder_address,
                accounted_at=datetime.now(timezone.utc),
                status=status,
                share_head_seq=head,
            )
        )
        self._blocks.sort(key=lambda b: b.height, reverse=True)
        if status in ("pending", "confirmed"):
            await self.set_meta("last_height", str(height))

    async def list_blocks(self, limit: int = 20) -> list[BlockRow]:
        return self._blocks[:limit]

    async def list_blocks_by_status(self, status: str, limit: int = 50) -> list[BlockRow]:
        rows = [b for b in self._blocks if b.status == status]
        return rows[:limit]

    async def list_confirmed_blocks(self, limit: int = 20) -> list[BlockRow]:
        rows = [b for b in self._blocks if b.status == "confirmed"]
        return rows[:limit]

    async def max_share_seq(self) -> int:
        return int(self._seq)

    async def payout_window_cutoff_seq(self, window_finds: int) -> int | None:
        conf = [b for b in self._blocks if b.status == "confirmed"]
        if len(conf) < window_finds:
            return None
        oldest = conf[window_finds - 1]
        return int(oldest.share_head_seq or 0)

    async def set_block_status(self, height: int, status: str, *, orphan_reason: str | None = None) -> None:
        for i, b in enumerate(self._blocks):
            if b.height == height:
                self._blocks[i] = BlockRow(
                    height=b.height,
                    block_hash=b.block_hash,
                    difficulty=b.difficulty,
                    reward_sats=b.reward_sats,
                    finder_address=b.finder_address,
                    accounted_at=b.accounted_at,
                    status=status,
                    share_head_seq=b.share_head_seq,
                    orphan_reason=orphan_reason if status in ("orphaned", "misattributed") else None,
                )
                break

    async def mark_block_orphaned(self, height: int, *, reason: str) -> None:
        # reopen credits paid in this height; drop credits from this find
        updated = []
        for h, addr, sats, paid in self._credits:
            if h == height:
                continue  # void from_height
            if paid == height:
                updated.append((h, addr, sats, None))
            else:
                updated.append((h, addr, sats, paid))
        self._credits = updated
        await self.set_block_status(height, "orphaned", orphan_reason=reason)

    async def reassign_pending_block(
        self,
        *,
        old_height: int,
        new_height: int,
        new_hash: str,
        finder_address: str | None,
        reason: str,
    ) -> None:
        old = next((b for b in self._blocks if b.height == old_height), None)
        await self.mark_block_orphaned(old_height, reason=reason)
        await self.record_block(
            height=new_height,
            block_hash=new_hash,
            difficulty=old.difficulty if old else 1.0,
            reward_sats=old.reward_sats if old else 0,
            finder_address=finder_address or (old.finder_address if old else None),
            status="confirmed",
            share_head_seq=old.share_head_seq if old else None,
        )

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
        # oldest unpaid
        _, addr, sats, _ = open_[0]
        return addr, sats

    async def pending_finder_credit_id(self) -> int | None:
        # Memory store has no ids; return sentinel index
        for i, c in enumerate(self._credits):
            if c[3] is None:
                return i
        return None

    async def mark_finder_credits_paid(self, paid_in_height: int) -> int:
        # Only oldest unpaid (matches single coinbaser bonus line)
        open_idx = None
        for i, c in enumerate(self._credits):
            if c[3] is None:
                open_idx = i
                break
        if open_idx is None:
            return 0
        h, addr, sats, _ = self._credits[open_idx]
        self._credits[open_idx] = (h, addr, sats, paid_in_height)
        return 1

    async def mark_finder_credit_paid(self, credit_id: int, paid_in_height: int) -> int:
        if credit_id < 0 or credit_id >= len(self._credits):
            return 0
        h, addr, sats, paid = self._credits[credit_id]
        if paid is not None:
            return 0
        self._credits[credit_id] = (h, addr, sats, paid_in_height)
        return 1

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


    async def record_share_attempt(
        self,
        address: str,
        *,
        accepted: bool,
        reason_code: int = 0,
        why: str = "",
        worker: str | None = None,
        is_block: bool = False,
    ) -> None:
        self._attempts.append(
            {
                "address": address,
                "accepted": accepted,
                "reason_code": int(reason_code),
                "why": why,
                "worker": worker,
                "is_block": bool(is_block),
                "at": datetime.now(timezone.utc),
            }
        )
        if len(self._attempts) > 50_000:
            self._attempts = self._attempts[-20_000:]

    async def get_quarantine(self, address: str) -> dict | None:
        return self._quarantine.get(address)

    async def set_quarantine(self, address: str, reason: str) -> None:
        self._quarantine[address] = {
            "reason": reason,
            "at": datetime.now(timezone.utc).isoformat(),
        }

    async def clear_quarantine(self, address: str) -> None:
        self._quarantine.pop(address, None)

    async def list_quarantines(self, addresses: list[str]) -> dict[str, dict]:
        return {a: self._quarantine[a] for a in addresses if a in self._quarantine}

    async def recent_attempt_stats(self, address: str, limit: int = 20) -> tuple[int, int]:
        rows = [r for r in reversed(self._attempts) if r["address"] == address][:limit]
        rej = sum(1 for r in rows if (not r["accepted"]) and int(r["reason_code"]) == 27)
        return rej, len(rows)



    async def consecutive_good_attempts(self, address: str, *, limit: int = 20) -> int:
        rows = [r for r in reversed(self._attempts) if r["address"] == address][:limit]
        n = 0
        for r in rows:
            if r.get("accepted") and int(r.get("reason_code") or 0) == 0:
                n += 1
            else:
                break
        return n



class PostgresStore(Store):

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def ensure_ready(self) -> None:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=8)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS share_attempts (
                    id           BIGSERIAL PRIMARY KEY,
                    address      TEXT NOT NULL,
                    worker       TEXT,
                    accepted     BOOLEAN NOT NULL,
                    reason_code  INT NOT NULL DEFAULT 0,
                    why          TEXT NOT NULL DEFAULT '',
                    is_block     BOOLEAN NOT NULL DEFAULT false,
                    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS share_attempts_addr_time_idx "
                "ON share_attempts (address, attempted_at DESC)"
            )
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS quarantined_at TIMESTAMPTZ"
            )
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS quarantine_reason TEXT"
            )

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

    def _block_row(self, r: Any) -> BlockRow:
        keys = set(r.keys()) if hasattr(r, "keys") else set()
        status = str(r["status"]) if "status" in keys and r["status"] is not None else "confirmed"
        head = r["share_head_seq"] if "share_head_seq" in keys else None
        reason = r["orphan_reason"] if "orphan_reason" in keys else None
        return BlockRow(
            height=r["height"],
            block_hash=r["block_hash"],
            difficulty=float(r["difficulty"]),
            reward_sats=r["reward_sats"],
            finder_address=r["finder_address"],
            accounted_at=r["accounted_at"],
            status=status,
            share_head_seq=int(head) if head is not None else None,
            orphan_reason=reason,
        )

    async def record_block(
        self,
        *,
        height: int,
        block_hash: str,
        difficulty: float,
        reward_sats: int,
        finder_address: str | None,
        status: str = "pending",
        share_head_seq: int | None = None,
    ) -> None:
        if share_head_seq is None:
            share_head_seq = await self.max_share_seq()
        async with self._p().acquire() as conn:
            async with conn.transaction():
                # Do not clobber a finalized orphan/confirmed row with a blind upsert
                existing = await conn.fetchrow(
                    "SELECT status FROM blocks WHERE height = $1", height
                )
                if existing and existing["status"] in ("confirmed", "orphaned", "misattributed"):
                    # Only allow update if still pending, or inserting new height
                    pass
                await conn.execute(
                    """
                    INSERT INTO blocks(
                      height, block_hash, difficulty, reward_sats, finder_address,
                      share_head_seq, status, status_checked_at, orphan_reason
                    )
                    VALUES($1, $2, $3, $4, $5, $6, $7, NULL, NULL)
                    ON CONFLICT (height) DO UPDATE SET
                      block_hash = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.block_hash
                        ELSE blocks.block_hash END,
                      difficulty = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.difficulty
                        ELSE blocks.difficulty END,
                      reward_sats = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.reward_sats
                        ELSE blocks.reward_sats END,
                      finder_address = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.finder_address
                        ELSE blocks.finder_address END,
                      share_head_seq = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.share_head_seq
                        ELSE blocks.share_head_seq END,
                      status = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.status
                        ELSE blocks.status END,
                      accounted_at = CASE
                        WHEN blocks.status = 'pending' THEN now()
                        ELSE blocks.accounted_at END
                    """,
                    height,
                    block_hash,
                    difficulty,
                    reward_sats,
                    finder_address,
                    share_head_seq,
                    status,
                )
                if status in ("pending", "confirmed"):
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
            SELECT height, block_hash, difficulty, reward_sats, finder_address, accounted_at,
                   COALESCE(status, 'confirmed') AS status, share_head_seq, orphan_reason
            FROM blocks ORDER BY height DESC LIMIT $1
            """,
            limit,
        )
        return [self._block_row(r) for r in rows]

    async def list_blocks_by_status(self, status: str, limit: int = 50) -> list[BlockRow]:
        rows = await self._p().fetch(
            """
            SELECT height, block_hash, difficulty, reward_sats, finder_address, accounted_at,
                   COALESCE(status, 'confirmed') AS status, share_head_seq, orphan_reason
            FROM blocks
            WHERE COALESCE(status, 'confirmed') = $1
            ORDER BY height ASC
            LIMIT $2
            """,
            status,
            limit,
        )
        return [self._block_row(r) for r in rows]

    async def list_confirmed_blocks(self, limit: int = 20) -> list[BlockRow]:
        rows = await self._p().fetch(
            """
            SELECT height, block_hash, difficulty, reward_sats, finder_address, accounted_at,
                   COALESCE(status, 'confirmed') AS status, share_head_seq, orphan_reason
            FROM blocks
            WHERE COALESCE(status, 'confirmed') = 'confirmed'
            ORDER BY height DESC
            LIMIT $1
            """,
            limit,
        )
        return [self._block_row(r) for r in rows]

    async def max_share_seq(self) -> int:
        return int(await self._p().fetchval("SELECT COALESCE(MAX(seq), 0) FROM shares") or 0)

    async def payout_window_cutoff_seq(self, window_finds: int) -> int | None:
        """Shares with seq > cutoff are in the window. None = all shares (fewer than N finds)."""
        rows = await self.list_confirmed_blocks(limit=max(window_finds, 1))
        if len(rows) < window_finds:
            return None
        oldest = rows[window_finds - 1]
        if oldest.share_head_seq is None:
            return 0
        return int(oldest.share_head_seq)

    async def set_block_status(self, height: int, status: str, *, orphan_reason: str | None = None) -> None:
        await self._p().execute(
            """
            UPDATE blocks
            SET status = $2,
                status_checked_at = now(),
                orphan_reason = $3
            WHERE height = $1
            """,
            height,
            status,
            orphan_reason,
        )

    async def mark_block_orphaned(self, height: int, *, reason: str) -> None:
        async with self._p().acquire() as conn:
            async with conn.transaction():
                # Reopen bonuses that were marked paid in this (now-orphan) block
                await conn.execute(
                    """
                    UPDATE finder_credits
                    SET paid_in_height = NULL
                    WHERE paid_in_height = $1
                    """,
                    height,
                )
                # Void finder credit created from the orphan find
                await conn.execute(
                    "DELETE FROM finder_credits WHERE from_height = $1",
                    height,
                )
                await conn.execute(
                    """
                    UPDATE blocks
                    SET status = 'orphaned',
                        status_checked_at = now(),
                        orphan_reason = $2
                    WHERE height = $1
                    """,
                    height,
                    reason,
                )

    async def reassign_pending_block(
        self,
        *,
        old_height: int,
        new_height: int,
        new_hash: str,
        finder_address: str | None,
        reason: str,
    ) -> None:
        async with self._p().acquire() as conn:
            async with conn.transaction():
                old = await conn.fetchrow("SELECT * FROM blocks WHERE height = $1", old_height)
                if not old:
                    return
                # 1) Insert/upsert the real height first (FK target for credits)
                await conn.execute(
                    """
                    INSERT INTO blocks(
                      height, block_hash, difficulty, reward_sats, finder_address,
                      share_head_seq, status, status_checked_at, orphan_reason, accounted_at
                    )
                    VALUES($1, $2, $3, $4, $5, $6, 'confirmed', now(), NULL, $7)
                    ON CONFLICT (height) DO UPDATE SET
                      block_hash = EXCLUDED.block_hash,
                      difficulty = EXCLUDED.difficulty,
                      reward_sats = EXCLUDED.reward_sats,
                      finder_address = EXCLUDED.finder_address,
                      share_head_seq = COALESCE(blocks.share_head_seq, EXCLUDED.share_head_seq),
                      status = 'confirmed',
                      status_checked_at = now(),
                      orphan_reason = NULL
                    """,
                    new_height,
                    new_hash,
                    float(old["difficulty"]),
                    int(old["reward_sats"]),
                    finder_address or old["finder_address"],
                    old["share_head_seq"],
                    old["accounted_at"],
                )
                # 2) Reopen bonuses wrongly marked paid on the bad row
                await conn.execute(
                    """
                    UPDATE finder_credits
                    SET paid_in_height = NULL
                    WHERE paid_in_height = $1
                    """,
                    old_height,
                )
                # 3) Move finder credit created from the bad find onto the real height
                if int(old_height) != int(new_height):
                    await conn.execute(
                        """
                        UPDATE finder_credits
                        SET from_height = $2
                        WHERE from_height = $1
                        """,
                        old_height,
                        new_height,
                    )
                    await conn.execute(
                        """
                        UPDATE blocks
                        SET status = 'orphaned', status_checked_at = now(), orphan_reason = $2
                        WHERE height = $1
                        """,
                        old_height,
                        reason,
                    )
                else:
                    # same height, hash corrected — already confirmed above
                    await conn.execute(
                        """
                        UPDATE blocks
                        SET status = 'confirmed', status_checked_at = now(), orphan_reason = NULL,
                            block_hash = $2
                        WHERE height = $1
                        """,
                        old_height,
                        new_hash,
                    )

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
            ORDER BY id ASC LIMIT 1
            """
        )
        if not row:
            return None, 0
        return row["address"], int(row["credit_sats"])

    async def pending_finder_credit_id(self) -> int | None:
        val = await self._p().fetchval(
            """
            SELECT id FROM finder_credits
            WHERE paid_in_height IS NULL
            ORDER BY id ASC LIMIT 1
            """
        )
        return int(val) if val is not None else None

    async def mark_finder_credits_paid(self, paid_in_height: int) -> int:
        # Only the oldest unpaid credit (single bonus line in coinbaser)
        result = await self._p().execute(
            """
            UPDATE finder_credits
            SET paid_in_height = $1
            WHERE id = (
              SELECT id FROM finder_credits
              WHERE paid_in_height IS NULL
              ORDER BY id ASC
              LIMIT 1
            )
            """,
            paid_in_height,
        )
        try:
            return int(str(result).split()[-1])
        except ValueError:
            return 0

    async def mark_finder_credit_paid(self, credit_id: int, paid_in_height: int) -> int:
        result = await self._p().execute(
            """
            UPDATE finder_credits
            SET paid_in_height = $2
            WHERE id = $1 AND paid_in_height IS NULL
            """,
            credit_id,
            paid_in_height,
        )
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


    async def record_share_attempt(
        self,
        address: str,
        *,
        accepted: bool,
        reason_code: int = 0,
        why: str = "",
        worker: str | None = None,
        is_block: bool = False,
    ) -> None:
        async with self._p().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users(address) VALUES($1)
                ON CONFLICT (address) DO UPDATE SET last_seen = now()
                """,
                address,
            )
            await conn.execute(
                """
                INSERT INTO share_attempts(address, worker, accepted, reason_code, why, is_block)
                VALUES($1, $2, $3, $4, $5, $6)
                """,
                address,
                worker,
                accepted,
                int(reason_code),
                why or "",
                bool(is_block),
            )

    async def get_quarantine(self, address: str) -> dict | None:
        row = await self._p().fetchrow(
            """
            SELECT quarantined_at, quarantine_reason FROM users WHERE address = $1
            """,
            address,
        )
        if not row or row["quarantined_at"] is None:
            return None
        return {
            "reason": row["quarantine_reason"] or "quarantined",
            "at": row["quarantined_at"].isoformat(),
        }

    async def set_quarantine(self, address: str, reason: str) -> None:
        async with self._p().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users(address, quarantined_at, quarantine_reason)
                VALUES($1, now(), $2)
                ON CONFLICT (address) DO UPDATE
                SET quarantined_at = COALESCE(users.quarantined_at, now()),
                    quarantine_reason = $2,
                    last_seen = now()
                """,
                address,
                reason,
            )

    async def clear_quarantine(self, address: str) -> None:
        await self._p().execute(
            """
            UPDATE users SET quarantined_at = NULL, quarantine_reason = NULL
            WHERE address = $1
            """,
            address,
        )

    async def list_quarantines(self, addresses: list[str]) -> dict[str, dict]:
        if not addresses:
            return {}
        rows = await self._p().fetch(
            """
            SELECT address, quarantined_at, quarantine_reason FROM users
            WHERE address = ANY($1::text[]) AND quarantined_at IS NOT NULL
            """,
            addresses,
        )
        out: dict[str, dict] = {}
        for r in rows:
            out[r["address"]] = {
                "reason": r["quarantine_reason"] or "quarantined",
                "at": r["quarantined_at"].isoformat(),
            }
        return out

    async def recent_attempt_stats(self, address: str, limit: int = 20) -> tuple[int, int]:
        rows = await self._p().fetch(
            """
            SELECT accepted, reason_code FROM share_attempts
            WHERE address = $1
            ORDER BY attempted_at DESC, id DESC
            LIMIT $2
            """,
            address,
            limit,
        )
        rej = sum(1 for r in rows if (not r["accepted"]) and int(r["reason_code"]) == 27)
        return rej, len(rows)


    async def consecutive_good_attempts(self, address: str, *, limit: int = 20) -> int:
        rows = await self._p().fetch(
            """
            SELECT accepted, reason_code FROM share_attempts
            WHERE address = $1
            ORDER BY attempted_at DESC, id DESC
            LIMIT $2
            """,
            address,
            limit,
        )
        n = 0
        for r in rows:
            if r["accepted"] and int(r["reason_code"] or 0) == 0:
                n += 1
            else:
                break
        return n


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
