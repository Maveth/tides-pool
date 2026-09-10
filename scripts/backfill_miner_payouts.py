#!/usr/bin/env python3
"""Backfill miner_payouts from blocks.intended_payout_json + manual_adjustment_* meta.

Usage (on NAS / in Prime container):
  python -m scripts.backfill_miner_payouts           # dry-run summary
  python -m scripts.backfill_miner_payouts --apply   # write ledger

Does NOT use share-window replay.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys


async def main(apply: bool) -> int:
    # Allow running from repo root or container /app
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
    from tides_pool.config import Settings
    from tides_pool.miner_payouts import sync_miner_payouts_for_height
    from tides_pool.store import PostgresStore

    url = os.environ.get("TIDES_DATABASE_URL") or Settings().database_url
    if not url:
        print("TIDES_DATABASE_URL / Settings.database_url required", file=sys.stderr)
        return 2

    store = PostgresStore(url)
    await store.ensure_ready()
    blocks = await store.list_blocks(limit=2000)
    # oldest first so pending→paid natural order doesn't matter (idempotent upsert)
    blocks = sorted(blocks, key=lambda b: int(b.height))
    n_ok = n_skip = n_rows = 0
    for b in blocks:
        if str(b.block_hash).startswith(("lab-", "pool-")):
            n_skip += 1
            continue
        st = (b.status or "").lower()
        if st in ("orphaned", "misattributed"):
            n_skip += 1
            continue
        if not b.intended_payout_json and not (
            await store.get_meta(f"manual_adjustment_{int(b.height)}")
        ):
            n_skip += 1
            continue
        if not apply:
            # dry-run: just count what plan would write
            from tides_pool.miner_payouts import plan_sync_for_block

            adj = None
            raw = await store.get_meta(f"manual_adjustment_{int(b.height)}")
            if raw:
                try:
                    adj = json.loads(raw)
                except Exception:
                    adj = None
            rows, _ = plan_sync_for_block(
                height=int(b.height),
                payout_mode=b.payout_mode,
                block_status=b.status,
                intended=b.intended_payout_json,
                adjustment=adj if isinstance(adj, dict) else None,
            )
            n_rows += len(rows)
            n_ok += 1
            continue
        wrote = await sync_miner_payouts_for_height(store, int(b.height))
        n_rows += wrote
        n_ok += 1
        if n_ok % 25 == 0:
            print(f"… synced {n_ok} blocks, rows≈{n_rows}")

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"{mode}: blocks_synced={n_ok} skipped={n_skip} ledger_rows={n_rows}")
    await store.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write miner_payouts rows")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply)))
