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
    # onchain_split (normal multi-out) | ops_manual (ops-only coinbase; ops pays miners)
    payout_mode: str = "onchain_split"
    manual_payout_done: bool = False
    manual_payout_note: str | None = None
    intended_payout_json: str | None = None


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
    async def list_shares_after_cutoff(self, cutoff_seq: int | None) -> list[Share]:
        """Payout-window shares newest-first: seq > cutoff (or all if cutoff is None).

        No row cap — window is bounded by confirmed pool finds, not LIMIT 50_000.
        """
        ...

    @abstractmethod
    async def list_shares_for_address(
        self, address: str, *, limit: int = 50, offset: int = 0
    ) -> list[ShareRow]: ...

    @abstractmethod
    async def list_share_rows_since(self, since_seconds: int, *, limit: int = 50_000) -> list[ShareRow]: ...

    @abstractmethod
    async def share_work_buckets(
        self,
        *,
        start: datetime,
        end: datetime,
        bucket_sec: int,
        address: str | None = None,
    ) -> list[tuple[datetime, int]]:
        """Return [(bucket_start_utc, sum_work), ...] ascending for chart hashrate."""
        ...

    @abstractmethod
    async def list_blocks_between(
        self, *, start: datetime, end: datetime, limit: int = 500
    ) -> list[BlockRow]:
        """Pool finds with accounted_at in [start, end], oldest first."""
        ...

    @abstractmethod
    async def record_network_hashrate(
        self, hs: float, *, sampled_at: datetime | None = None, min_interval_sec: int = 50
    ) -> bool:
        """Append a network H/s sample (Knots getnetworkhashps). Returns False if throttled."""
        ...

    @abstractmethod
    async def list_network_hashrate(
        self, *, start: datetime, end: datetime, limit: int = 20_000
    ) -> list[tuple[datetime, float]]:
        """Return [(sampled_at, hs), ...] ascending for charts."""
        ...

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
        status: str = "pending",
        share_head_seq: int | None = None,
        payout_mode: str = "onchain_split",
        intended_payout_json: str | None = None,
        manual_payout_done: bool = False,
        manual_payout_note: str | None = None,
    ) -> None: ...

    @abstractmethod
    async def set_block_payout_meta(
        self,
        height: int,
        *,
        payout_mode: str | None = None,
        intended_payout_json: str | None = None,
        manual_payout_done: bool | None = None,
        manual_payout_note: str | None = None,
    ) -> None:
        """Update manual/ops payout fields on an existing block row."""
        ...

    @abstractmethod
    async def list_blocks(self, limit: int = 20) -> list[BlockRow]: ...

    @abstractmethod
    async def list_blocks_by_status(self, status: str, limit: int = 50) -> list[BlockRow]: ...

    @abstractmethod
    async def finder_workers_for_blocks(self, blocks: list[BlockRow]) -> dict[int, str]:
        """Map block height → stratum worker that submitted the winning share (best effort)."""
        ...

    @abstractmethod
    async def set_address_nickname(self, address: str, nickname: str) -> None:
        """Remember last-seen coinbase secondary tag (nickname) for an address."""
        ...

    @abstractmethod
    async def nicknames_for_addresses(self, addresses: list[str]) -> dict[str, str]:
        """address → last_nickname for known addresses."""
        ...

    @abstractmethod
    async def list_confirmed_blocks(self, limit: int = 20) -> list[BlockRow]: ...

    @abstractmethod
    async def max_share_seq(self) -> int: ...

    @abstractmethod
    async def payout_window_cutoff_seq(self, window_finds: int) -> int | None: ...

    @abstractmethod
    async def set_block_status(self, height: int, status: str, *, orphan_reason: str | None = None) -> None: ...

    @abstractmethod
    async def update_block_reward(self, height: int, reward_sats: int) -> None: ...

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
    async def finder_credit_totals(self, address: str) -> tuple[int, int]:
        """Return (paid_sats, unpaid_sats) for this address's finder bonuses."""
        ...

    @abstractmethod
    async def list_finder_credits_for_address(
        self, address: str, *, limit: int = 200
    ) -> list[tuple[int, int, int | None]]:
        """Return [(from_height, credit_sats, paid_in_height), ...] newest-first."""
        ...

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
        """Count trailing good multi-out attempts (ok / rehab-good / probation-good)."""

    @abstractmethod
    async def is_probation_cleared(self, address: str) -> bool:
        """True if address may receive window credit (graduated probation)."""

    @abstractmethod
    async def clear_probation(self, address: str) -> None:
        """Mark address as graduated from new-miner probation."""


class MemoryStore(Store):
    def __init__(self) -> None:
        self._shares: list[ShareRow] = []
        self._seq = 0
        self._blocks: list[BlockRow] = []
        self._meta: dict[str, str] = {}
        self._credits: list[tuple[int, str, int, int | None]] = []  # height, addr, sats, paid
        self._attempts: list[dict] = []
        self._quarantine: dict[str, dict] = {}
        self._probation_cleared: set[str] = set()

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

    async def list_shares_after_cutoff(self, cutoff_seq: int | None) -> list[Share]:
        out: list[Share] = []
        for r in reversed(self._shares):
            if cutoff_seq is not None and r.seq <= cutoff_seq:
                break
            if r.work < 1:
                continue
            out.append(Share(seq=r.seq, address=r.address, work=r.work, fee_bps=r.fee_bps))
        return out

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

    async def share_work_buckets(
        self,
        *,
        start: datetime,
        end: datetime,
        bucket_sec: int,
        address: str | None = None,
    ) -> list[tuple[datetime, int]]:
        import time
        from collections import defaultdict

        bsec = max(int(bucket_sec), 1)
        start_ts = start.timestamp()
        end_ts = end.timestamp()
        acc: dict[int, int] = defaultdict(int)
        for r in self._shares:
            if address and r.address != address:
                continue
            ts = r.accepted_at.timestamp() if r.accepted_at.tzinfo else r.accepted_at.replace(tzinfo=timezone.utc).timestamp()
            if ts < start_ts or ts >= end_ts:
                continue
            bucket = int(ts // bsec) * bsec
            acc[bucket] += int(r.work)
        return [
            (datetime.fromtimestamp(k, tz=timezone.utc), acc[k])
            for k in sorted(acc.keys())
        ]

    async def list_blocks_between(
        self, *, start: datetime, end: datetime, limit: int = 500
    ) -> list[BlockRow]:
        rows = [
            b
            for b in self._blocks
            if b.status in ("pending", "confirmed")
            and b.accounted_at
            and start <= b.accounted_at <= end
        ]
        rows.sort(key=lambda b: b.accounted_at or datetime.min.replace(tzinfo=timezone.utc))
        return rows[:limit]

    async def record_network_hashrate(
        self, hs: float, *, sampled_at: datetime | None = None, min_interval_sec: int = 50
    ) -> bool:
        if not hasattr(self, "_net_hs"):
            self._net_hs: list[tuple[datetime, float]] = []
        ts = sampled_at or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if self._net_hs:
            last = self._net_hs[-1][0]
            if (ts - last).total_seconds() < max(int(min_interval_sec), 0):
                return False
        self._net_hs.append((ts, float(hs)))
        return True

    async def list_network_hashrate(
        self, *, start: datetime, end: datetime, limit: int = 20_000
    ) -> list[tuple[datetime, float]]:
        if not hasattr(self, "_net_hs"):
            return []
        out = [(t, h) for t, h in self._net_hs if start <= t <= end]
        return out[:limit]

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
        payout_mode: str = "onchain_split",
        intended_payout_json: str | None = None,
        manual_payout_done: bool = False,
        manual_payout_note: str | None = None,
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
                payout_mode=payout_mode or "onchain_split",
                manual_payout_done=bool(manual_payout_done),
                manual_payout_note=manual_payout_note,
                intended_payout_json=intended_payout_json,
            )
        )
        self._blocks.sort(key=lambda b: b.height, reverse=True)
        if status in ("pending", "confirmed"):
            await self.set_meta("last_height", str(height))

    async def set_block_payout_meta(
        self,
        height: int,
        *,
        payout_mode: str | None = None,
        intended_payout_json: str | None = None,
        manual_payout_done: bool | None = None,
        manual_payout_note: str | None = None,
    ) -> None:
        for i, b in enumerate(self._blocks):
            if b.height != height:
                continue
            self._blocks[i] = BlockRow(
                height=b.height,
                block_hash=b.block_hash,
                difficulty=b.difficulty,
                reward_sats=b.reward_sats,
                finder_address=b.finder_address,
                accounted_at=b.accounted_at,
                status=b.status,
                share_head_seq=b.share_head_seq,
                orphan_reason=b.orphan_reason,
                payout_mode=payout_mode if payout_mode is not None else b.payout_mode,
                manual_payout_done=(
                    bool(manual_payout_done)
                    if manual_payout_done is not None
                    else b.manual_payout_done
                ),
                manual_payout_note=(
                    manual_payout_note
                    if manual_payout_note is not None
                    else b.manual_payout_note
                ),
                intended_payout_json=(
                    intended_payout_json
                    if intended_payout_json is not None
                    else b.intended_payout_json
                ),
            )
            break

    async def list_blocks(self, limit: int = 20) -> list[BlockRow]:
        return self._blocks[:limit]

    async def list_blocks_by_status(self, status: str, limit: int = 50) -> list[BlockRow]:
        rows = [b for b in self._blocks if b.status == status]
        return rows[:limit]

    async def finder_workers_for_blocks(self, blocks: list[BlockRow]) -> dict[int, str]:
        out: dict[int, str] = {}
        for b in blocks:
            if not b.finder_address:
                continue
            best = None
            best_dt = None
            for a in reversed(getattr(self, "_attempts", [])):
                if not a.get("is_block"):
                    continue
                if a.get("address") != b.finder_address:
                    continue
                w = (a.get("worker") or "").strip()
                if not w:
                    continue
                at = a.get("at")
                if b.accounted_at and at:
                    try:
                        if abs((at - b.accounted_at).total_seconds()) > 600:
                            continue
                    except Exception:
                        pass
                if best_dt is None or (at and best_dt and at > best_dt) or best_dt is None:
                    best, best_dt = w, at
            if best:
                out[int(b.height)] = best
        return out

    async def set_address_nickname(self, address: str, nickname: str) -> None:
        from tides_pool.block_confirm import sanitize_nickname

        nick = sanitize_nickname(nickname)
        if not address or not nick:
            return
        if not hasattr(self, "_nicknames"):
            self._nicknames = {}
        self._nicknames[address] = nick

    async def nicknames_for_addresses(self, addresses: list[str]) -> dict[str, str]:
        nmap = getattr(self, "_nicknames", {})
        return {a: nmap[a] for a in addresses if a in nmap}

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
                    payout_mode=b.payout_mode,
                    manual_payout_done=b.manual_payout_done,
                    manual_payout_note=b.manual_payout_note,
                    intended_payout_json=b.intended_payout_json,
                )
                break

    async def update_block_reward(self, height: int, reward_sats: int) -> None:
        for i, b in enumerate(self._blocks):
            if b.height == height:
                self._blocks[i] = BlockRow(
                    height=b.height,
                    block_hash=b.block_hash,
                    difficulty=b.difficulty,
                    reward_sats=int(reward_sats),
                    finder_address=b.finder_address,
                    accounted_at=b.accounted_at,
                    status=b.status,
                    share_head_seq=b.share_head_seq,
                    orphan_reason=b.orphan_reason,
                    payout_mode=b.payout_mode,
                    manual_payout_done=b.manual_payout_done,
                    manual_payout_note=b.manual_payout_note,
                    intended_payout_json=b.intended_payout_json,
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

    async def finder_credit_totals(self, address: str) -> tuple[int, int]:
        paid = unpaid = 0
        for _h, addr, sats, paid_h in self._credits:
            if addr != address:
                continue
            if paid_h is None:
                unpaid += int(sats)
            else:
                paid += int(sats)
        return paid, unpaid

    async def list_finder_credits_for_address(
        self, address: str, *, limit: int = 200
    ) -> list[tuple[int, int, int | None]]:
        rows = [
            (int(h), int(sats), int(paid_h) if paid_h is not None else None)
            for h, addr, sats, paid_h in self._credits
            if addr == address
        ]
        rows.sort(key=lambda r: r[0], reverse=True)
        return rows[:limit]

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
        good_why = {"ok", "rehab-good", "probation-good"}
        n = 0
        for r in rows:
            why = (r.get("why") or "ok").strip() or "ok"
            if (
                r.get("accepted")
                and int(r.get("reason_code") or 0) == 0
                and why in good_why
            ):
                n += 1
            else:
                break
        return n

    async def is_probation_cleared(self, address: str) -> bool:
        return address in self._probation_cleared

    async def clear_probation(self, address: str) -> None:
        if address:
            self._probation_cleared.add(address)


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
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_nickname TEXT"
            )
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS nickname_seen_at TIMESTAMPTZ"
            )
            await conn.execute(
                "ALTER TABLE users ADD COLUMN IF NOT EXISTS probation_cleared_at TIMESTAMPTZ"
            )
            # One-shot: graduate anyone who already has credited shares. New addresses
            # stay in probation until they prove N good multi-out shares.
            await conn.execute(
                """
                UPDATE users u
                SET probation_cleared_at = COALESCE(u.first_seen, now())
                WHERE u.probation_cleared_at IS NULL
                  AND EXISTS (SELECT 1 FROM shares s WHERE s.address = u.address LIMIT 1)
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS network_hashrate_samples (
                    sampled_at TIMESTAMPTZ PRIMARY KEY,
                    hs DOUBLE PRECISION NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS network_hashrate_samples_at_idx "
                "ON network_hashrate_samples (sampled_at DESC)"
            )
            # Ops-only finds (Prime timeout fallback): keep as pool finds with manual payout
            await conn.execute(
                "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS payout_mode TEXT "
                "NOT NULL DEFAULT 'onchain_split'"
            )
            await conn.execute(
                "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS manual_payout_done BOOLEAN "
                "NOT NULL DEFAULT false"
            )
            await conn.execute(
                "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS manual_payout_note TEXT"
            )
            await conn.execute(
                "ALTER TABLE blocks ADD COLUMN IF NOT EXISTS intended_payout_json TEXT"
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

    async def list_shares_after_cutoff(self, cutoff_seq: int | None) -> list[Share]:
        """All payout-window shares (newest-first). No artificial LIMIT."""
        if cutoff_seq is None:
            rows = await self._p().fetch(
                """
                SELECT seq, address, work, fee_bps FROM shares
                WHERE work >= 1
                ORDER BY seq DESC
                """
            )
        else:
            rows = await self._p().fetch(
                """
                SELECT seq, address, work, fee_bps FROM shares
                WHERE seq > $1 AND work >= 1
                ORDER BY seq DESC
                """,
                int(cutoff_seq),
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

    async def share_work_buckets(
        self,
        *,
        start: datetime,
        end: datetime,
        bucket_sec: int,
        address: str | None = None,
    ) -> list[tuple[datetime, int]]:
        bsec = max(int(bucket_sec), 1)
        if address:
            rows = await self._p().fetch(
                """
                SELECT to_timestamp(floor(extract(epoch FROM accepted_at) / $3) * $3)
                         AT TIME ZONE 'UTC' AS bucket_ts,
                       COALESCE(SUM(work), 0)::bigint AS work
                FROM shares
                WHERE accepted_at >= $1 AND accepted_at < $2
                  AND address = $4
                GROUP BY 1
                ORDER BY 1
                """,
                start,
                end,
                bsec,
                address,
            )
        else:
            rows = await self._p().fetch(
                """
                SELECT to_timestamp(floor(extract(epoch FROM accepted_at) / $3) * $3)
                         AT TIME ZONE 'UTC' AS bucket_ts,
                       COALESCE(SUM(work), 0)::bigint AS work
                FROM shares
                WHERE accepted_at >= $1 AND accepted_at < $2
                GROUP BY 1
                ORDER BY 1
                """,
                start,
                end,
                bsec,
            )
        out: list[tuple[datetime, int]] = []
        for r in rows:
            ts = r["bucket_ts"]
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append((ts, int(r["work"] or 0)))
        return out

    async def list_blocks_between(
        self, *, start: datetime, end: datetime, limit: int = 500
    ) -> list[BlockRow]:
        rows = await self._p().fetch(
            """
            SELECT height, block_hash, difficulty, reward_sats, finder_address,
                   accounted_at, status, share_head_seq, orphan_reason,
                   COALESCE(payout_mode, 'onchain_split') AS payout_mode,
                   COALESCE(manual_payout_done, false) AS manual_payout_done,
                   manual_payout_note, intended_payout_json
            FROM blocks
            WHERE status IN ('pending', 'confirmed')
              AND accounted_at >= $1 AND accounted_at <= $2
            ORDER BY accounted_at ASC
            LIMIT $3
            """,
            start,
            end,
            int(limit),
        )
        return [self._block_row(r) for r in rows]

    async def record_network_hashrate(
        self, hs: float, *, sampled_at: datetime | None = None, min_interval_sec: int = 50
    ) -> bool:
        ts = sampled_at or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        last = await self._p().fetchval(
            "SELECT sampled_at FROM network_hashrate_samples ORDER BY sampled_at DESC LIMIT 1"
        )
        if last is not None:
            if getattr(last, "tzinfo", None) is None:
                last = last.replace(tzinfo=timezone.utc)
            if (ts - last).total_seconds() < max(int(min_interval_sec), 0):
                return False
        await self._p().execute(
            """
            INSERT INTO network_hashrate_samples(sampled_at, hs)
            VALUES ($1, $2)
            ON CONFLICT (sampled_at) DO UPDATE SET hs = EXCLUDED.hs
            """,
            ts,
            float(hs),
        )
        return True

    async def list_network_hashrate(
        self, *, start: datetime, end: datetime, limit: int = 20_000
    ) -> list[tuple[datetime, float]]:
        rows = await self._p().fetch(
            """
            SELECT sampled_at, hs
            FROM network_hashrate_samples
            WHERE sampled_at >= $1 AND sampled_at <= $2
            ORDER BY sampled_at ASC
            LIMIT $3
            """,
            start,
            end,
            int(limit),
        )
        out: list[tuple[datetime, float]] = []
        for r in rows:
            ts = r["sampled_at"]
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append((ts, float(r["hs"] or 0.0)))
        return out

    async def share_count(self) -> int:
        return int(await self._p().fetchval("SELECT COUNT(*) FROM shares") or 0)

    async def total_work(self) -> int:
        return int(await self._p().fetchval("SELECT COALESCE(SUM(work),0) FROM shares") or 0)

    def _block_row(self, r: Any) -> BlockRow:
        keys = set(r.keys()) if hasattr(r, "keys") else set()
        status = str(r["status"]) if "status" in keys and r["status"] is not None else "confirmed"
        head = r["share_head_seq"] if "share_head_seq" in keys else None
        reason = r["orphan_reason"] if "orphan_reason" in keys else None
        mode = (
            str(r["payout_mode"])
            if "payout_mode" in keys and r["payout_mode"]
            else "onchain_split"
        )
        done = bool(r["manual_payout_done"]) if "manual_payout_done" in keys else False
        note = r["manual_payout_note"] if "manual_payout_note" in keys else None
        snap = r["intended_payout_json"] if "intended_payout_json" in keys else None
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
            payout_mode=mode,
            manual_payout_done=done,
            manual_payout_note=note,
            intended_payout_json=snap,
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
        payout_mode: str = "onchain_split",
        intended_payout_json: str | None = None,
        manual_payout_done: bool = False,
        manual_payout_note: str | None = None,
    ) -> None:
        if share_head_seq is None:
            share_head_seq = await self.max_share_seq()
        mode = (payout_mode or "onchain_split").strip() or "onchain_split"
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
                      share_head_seq, status, status_checked_at, orphan_reason,
                      payout_mode, manual_payout_done, manual_payout_note, intended_payout_json
                    )
                    VALUES($1, $2, $3, $4, $5, $6, $7, NULL, NULL, $8, $9, $10, $11)
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
                        ELSE blocks.accounted_at END,
                      payout_mode = CASE
                        WHEN blocks.status = 'pending' THEN EXCLUDED.payout_mode
                        ELSE blocks.payout_mode END,
                      intended_payout_json = CASE
                        WHEN blocks.status = 'pending'
                         AND EXCLUDED.intended_payout_json IS NOT NULL
                        THEN EXCLUDED.intended_payout_json
                        ELSE blocks.intended_payout_json END,
                      manual_payout_note = CASE
                        WHEN blocks.status = 'pending'
                         AND EXCLUDED.manual_payout_note IS NOT NULL
                        THEN EXCLUDED.manual_payout_note
                        ELSE blocks.manual_payout_note END
                    """,
                    height,
                    block_hash,
                    difficulty,
                    reward_sats,
                    finder_address,
                    share_head_seq,
                    status,
                    mode,
                    bool(manual_payout_done),
                    manual_payout_note,
                    intended_payout_json,
                )
                if status in ("pending", "confirmed"):
                    await conn.execute(
                        """
                        INSERT INTO meta(key, value) VALUES('last_height', $1)
                        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                        """,
                        str(height),
                    )

    async def set_block_payout_meta(
        self,
        height: int,
        *,
        payout_mode: str | None = None,
        intended_payout_json: str | None = None,
        manual_payout_done: bool | None = None,
        manual_payout_note: str | None = None,
    ) -> None:
        # Build dynamic SET — only touch provided fields
        sets: list[str] = []
        args: list[Any] = [int(height)]
        if payout_mode is not None:
            args.append(payout_mode)
            sets.append(f"payout_mode = ${len(args)}")
        if intended_payout_json is not None:
            args.append(intended_payout_json)
            sets.append(f"intended_payout_json = ${len(args)}")
        if manual_payout_done is not None:
            args.append(bool(manual_payout_done))
            sets.append(f"manual_payout_done = ${len(args)}")
        if manual_payout_note is not None:
            args.append(manual_payout_note)
            sets.append(f"manual_payout_note = ${len(args)}")
        if not sets:
            return
        await self._p().execute(
            f"UPDATE blocks SET {', '.join(sets)} WHERE height = $1",
            *args,
        )

    async def list_blocks(self, limit: int = 20) -> list[BlockRow]:
        rows = await self._p().fetch(
            """
            SELECT height, block_hash, difficulty, reward_sats, finder_address, accounted_at,
                   COALESCE(status, 'confirmed') AS status, share_head_seq, orphan_reason,
                   COALESCE(payout_mode, 'onchain_split') AS payout_mode,
                   COALESCE(manual_payout_done, false) AS manual_payout_done,
                   manual_payout_note, intended_payout_json
            FROM blocks ORDER BY height DESC LIMIT $1
            """,
            limit,
        )
        return [self._block_row(r) for r in rows]

    async def finder_workers_for_blocks(self, blocks: list[BlockRow]) -> dict[int, str]:
        """Best-effort: is_block share_attempt for finder near accounted_at; else last worker share."""
        out: dict[int, str] = {}
        if not blocks:
            return out
        for b in blocks:
            if not b.finder_address:
                continue
            w = None
            if b.accounted_at is not None:
                row = await self._p().fetchrow(
                    """
                    SELECT worker FROM share_attempts
                    WHERE is_block = true
                      AND address = $1
                      AND worker IS NOT NULL AND worker <> ''
                      AND attempted_at BETWEEN ($2::timestamptz - interval '10 minutes')
                                           AND ($2::timestamptz + interval '2 minutes')
                    ORDER BY attempted_at DESC
                    LIMIT 1
                    """,
                    b.finder_address,
                    b.accounted_at,
                )
                if row and row["worker"]:
                    w = str(row["worker"]).strip()
            if not w and b.share_head_seq is not None:
                row = await self._p().fetchrow(
                    """
                    SELECT worker FROM shares
                    WHERE address = $1
                      AND seq <= $2
                      AND worker IS NOT NULL AND worker <> ''
                    ORDER BY seq DESC
                    LIMIT 1
                    """,
                    b.finder_address,
                    int(b.share_head_seq),
                )
                if row and row["worker"]:
                    w = str(row["worker"]).strip()
            if not w:
                row = await self._p().fetchrow(
                    """
                    SELECT worker FROM shares
                    WHERE address = $1
                      AND worker IS NOT NULL AND worker <> ''
                    ORDER BY seq DESC
                    LIMIT 1
                    """,
                    b.finder_address,
                )
                if row and row["worker"]:
                    w = str(row["worker"]).strip()
            if w:
                out[int(b.height)] = w
        return out

    async def set_address_nickname(self, address: str, nickname: str) -> None:
        # Lazy import avoids cycle (block_confirm imports Store).
        from tides_pool.block_confirm import sanitize_nickname

        nick = sanitize_nickname(nickname)
        addr = (address or "").strip()
        if not addr or not nick:
            return
        async with self._p().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users(address, last_nickname, nickname_seen_at)
                VALUES($1, $2, now())
                ON CONFLICT (address) DO UPDATE SET
                  last_nickname = EXCLUDED.last_nickname,
                  nickname_seen_at = now(),
                  last_seen = now()
                """,
                addr,
                nick,
            )

    async def nicknames_for_addresses(self, addresses: list[str]) -> dict[str, str]:
        addrs = [a for a in addresses if a]
        if not addrs:
            return {}
        rows = await self._p().fetch(
            """
            SELECT address, last_nickname FROM users
            WHERE address = ANY($1::text[])
              AND last_nickname IS NOT NULL AND last_nickname <> ''
            """,
            addrs,
        )
        return {str(r["address"]): str(r["last_nickname"]) for r in rows}

    async def list_blocks_by_status(self, status: str, limit: int = 50) -> list[BlockRow]:
        rows = await self._p().fetch(
            """
            SELECT height, block_hash, difficulty, reward_sats, finder_address, accounted_at,
                   COALESCE(status, 'confirmed') AS status, share_head_seq, orphan_reason,
                   COALESCE(payout_mode, 'onchain_split') AS payout_mode,
                   COALESCE(manual_payout_done, false) AS manual_payout_done,
                   manual_payout_note, intended_payout_json
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
                   COALESCE(status, 'confirmed') AS status, share_head_seq, orphan_reason,
                   COALESCE(payout_mode, 'onchain_split') AS payout_mode,
                   COALESCE(manual_payout_done, false) AS manual_payout_done,
                   manual_payout_note, intended_payout_json
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

    async def update_block_reward(self, height: int, reward_sats: int) -> None:
        await self._p().execute(
            "UPDATE blocks SET reward_sats = $2 WHERE height = $1",
            height,
            int(reward_sats),
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

                # 4) Re-apply "this block pays previous finder bonus".
                # Step 2 reopened credits marked paid on the synthetic/wrong height; without
                # this, pending_finder_credit() keeps pointing at the old finder (e.g. Maveth
                # still unpaid after GS4's real block) and live coinbaser pays the wrong person.
                await conn.execute(
                    """
                    UPDATE finder_credits
                    SET paid_in_height = $1
                    WHERE id = (
                      SELECT id FROM finder_credits
                      WHERE paid_in_height IS NULL
                        AND from_height < $1
                      ORDER BY id ASC
                      LIMIT 1
                    )
                    """,
                    int(new_height),
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

    async def finder_credit_totals(self, address: str) -> tuple[int, int]:
        row = await self._p().fetchrow(
            """
            SELECT
              COALESCE(SUM(credit_sats) FILTER (WHERE paid_in_height IS NOT NULL), 0)::bigint AS paid,
              COALESCE(SUM(credit_sats) FILTER (WHERE paid_in_height IS NULL), 0)::bigint AS unpaid
            FROM finder_credits
            WHERE address = $1
            """,
            address,
        )
        if not row:
            return 0, 0
        return int(row["paid"] or 0), int(row["unpaid"] or 0)

    async def list_finder_credits_for_address(
        self, address: str, *, limit: int = 200
    ) -> list[tuple[int, int, int | None]]:
        rows = await self._p().fetch(
            """
            SELECT from_height, credit_sats, paid_in_height
            FROM finder_credits
            WHERE address = $1
            ORDER BY from_height DESC, id DESC
            LIMIT $2
            """,
            address,
            limit,
        )
        return [
            (
                int(r["from_height"]),
                int(r["credit_sats"]),
                int(r["paid_in_height"]) if r["paid_in_height"] is not None else None,
            )
            for r in rows
        ]

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
            SELECT accepted, reason_code, why FROM share_attempts
            WHERE address = $1
            ORDER BY attempted_at DESC, id DESC
            LIMIT $2
            """,
            address,
            limit,
        )
        good_why = {"ok", "rehab-good", "probation-good"}
        n = 0
        for r in rows:
            why = (r["why"] or "ok").strip() or "ok"
            if r["accepted"] and int(r["reason_code"] or 0) == 0 and why in good_why:
                n += 1
            else:
                break
        return n

    async def is_probation_cleared(self, address: str) -> bool:
        addr = (address or "").strip()
        if not addr:
            return False
        val = await self._p().fetchval(
            "SELECT probation_cleared_at FROM users WHERE address = $1",
            addr,
        )
        return val is not None

    async def clear_probation(self, address: str) -> None:
        addr = (address or "").strip()
        if not addr:
            return
        async with self._p().acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users(address, first_seen, last_seen, probation_cleared_at)
                VALUES($1, now(), now(), now())
                ON CONFLICT (address) DO UPDATE SET
                  probation_cleared_at = COALESCE(users.probation_cleared_at, now()),
                  last_seen = now()
                """,
                addr,
            )


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
    current_since_seq: int | None = None,
) -> list[dict[str, Any]]:
    """Build contributor rows for the payout window.

    work / shares = full window (prior confirmed finds in window + current).
    work_current / shares_current = shares newer than current_since_seq
    (typically the share_head_seq of the latest confirmed pool find = this block only).
    current_since_seq=None → treat the whole window as current (no confirmed finds yet).
    """
    work: dict[str, int] = {}
    shares: dict[str, int] = {}
    work_cur: dict[str, int] = {}
    shares_cur: dict[str, int] = {}
    for s in window:
        work[s.address] = work.get(s.address, 0) + s.work
        shares[s.address] = shares.get(s.address, 0) + 1
        if current_since_seq is None or s.seq > current_since_seq:
            work_cur[s.address] = work_cur.get(s.address, 0) + s.work
            shares_cur[s.address] = shares_cur.get(s.address, 0) + 1
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
                "work_current": work_cur.get(addr, 0),
                "share_pct": round(100.0 * w / total, 4),
                "shares": shares.get(addr, 0),
                "shares_current": shares_cur.get(addr, 0),
                "hashrate_hs": hs,
                # live = recent HR; idle = this-block work but quiet; offline = no this-block work
                "activity": (
                    "live"
                    if hs > 0
                    else ("idle" if work_cur.get(addr, 0) > 0 else "offline")
                ),
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
