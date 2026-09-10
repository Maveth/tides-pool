"""Hot-reloadable fee knobs for DATUM Prime (no process restart).

Stored in Postgres ``meta.runtime_fees`` as JSON. Polled every few seconds.
Falls back to ``Settings`` / env defaults when a key is absent.

Keys (all optional):
  - local_work_fee_bps: int  (SV1/local-GW work skim; 100 = 1%)
  - local_work_fee_ops_share_bps: int  (fraction of skim → OPERATION FEE; 5000 = 50%)
  - local_work_fee_community_live_sec: int
  - note: str  (ops breadcrumb)

Global coinbaser ``fee_bps`` is intentionally NOT here — changing it mid-window
rewrites the live split; use compose/env + recreate for that.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from tides_pool.config import Settings
from tides_pool.store import Store

log = logging.getLogger(__name__)

META_KEY = "runtime_fees"
DEFAULT_TTL_SEC = 3.0


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


class RuntimeFees:
    """Cached view of ``meta.runtime_fees`` overlaid on Settings defaults."""

    def __init__(
        self,
        settings: Settings,
        store: Store | None = None,
        *,
        ttl_sec: float = DEFAULT_TTL_SEC,
    ) -> None:
        self.settings = settings
        self.store = store
        self.ttl_sec = max(0.5, float(ttl_sec))
        self._raw: dict[str, Any] = {}
        self._loaded_at = 0.0
        self._last_log_bps: int | None = None

    def bind_store(self, store: Store) -> None:
        self.store = store

    async def refresh(self, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and self._loaded_at and (now - self._loaded_at) < self.ttl_sec:
            return
        self._loaded_at = now
        if self.store is None:
            return
        try:
            raw = await self.store.get_meta(META_KEY, "{}")
            data = json.loads(raw or "{}")
            if not isinstance(data, dict):
                data = {}
            self._raw = data
        except Exception:  # noqa: BLE001
            log.exception("runtime_fees: failed loading %s", META_KEY)
            return
        bps = self.local_work_fee_bps_sync()
        if self._last_log_bps is None or self._last_log_bps != bps:
            log.info(
                "runtime_fees: local_work_fee_bps=%s (ops_share_bps=%s) source=%s",
                bps,
                self.local_work_fee_ops_share_bps_sync(),
                "meta" if "local_work_fee_bps" in self._raw else "settings",
            )
            self._last_log_bps = bps

    def local_work_fee_bps_sync(self) -> int:
        default = int(getattr(self.settings, "local_work_fee_bps", 100) or 0)
        if "local_work_fee_bps" not in self._raw:
            return max(0, min(5000, default))
        return _clamp_int(self._raw.get("local_work_fee_bps"), 0, 5000, default)

    def local_work_fee_ops_share_bps_sync(self) -> int:
        default = int(
            getattr(self.settings, "local_work_fee_ops_share_bps", 5000) or 5000
        )
        if "local_work_fee_ops_share_bps" not in self._raw:
            return max(0, min(10_000, default))
        return _clamp_int(
            self._raw.get("local_work_fee_ops_share_bps"), 0, 10_000, default
        )

    def local_work_fee_community_live_sec_sync(self) -> int:
        default = int(
            getattr(self.settings, "local_work_fee_community_live_sec", 600) or 600
        )
        if "local_work_fee_community_live_sec" not in self._raw:
            return max(60, min(3600, default))
        return _clamp_int(
            self._raw.get("local_work_fee_community_live_sec"), 60, 3600, default
        )

    async def local_work_fee_bps(self) -> int:
        await self.refresh()
        return self.local_work_fee_bps_sync()

    async def local_work_fee_ops_share_bps(self) -> int:
        await self.refresh()
        return self.local_work_fee_ops_share_bps_sync()

    async def local_work_fee_community_live_sec(self) -> int:
        await self.refresh()
        return self.local_work_fee_community_live_sec_sync()

    async def snapshot(self) -> dict[str, Any]:
        await self.refresh(force=True)
        return {
            "meta_key": META_KEY,
            "raw": dict(self._raw),
            "effective": {
                "local_work_fee_bps": self.local_work_fee_bps_sync(),
                "local_work_fee_ops_share_bps": self.local_work_fee_ops_share_bps_sync(),
                "local_work_fee_community_live_sec": self.local_work_fee_community_live_sec_sync(),
            },
            "settings_defaults": {
                "local_work_fee_bps": int(
                    getattr(self.settings, "local_work_fee_bps", 100) or 0
                ),
                "local_work_fee_ops_share_bps": int(
                    getattr(self.settings, "local_work_fee_ops_share_bps", 5000) or 5000
                ),
                "local_work_fee_community_live_sec": int(
                    getattr(self.settings, "local_work_fee_community_live_sec", 600)
                    or 600
                ),
            },
            "ttl_sec": self.ttl_sec,
        }
