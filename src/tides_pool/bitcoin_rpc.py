"""Minimal Bitcoin Core / Knots JSON-RPC client."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib import error, request

from tides_pool.config import Settings


class BitcoinRPCError(RuntimeError):
    pass


class BitcoinRPC:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def call(self, method: str, params: list[Any] | None = None) -> Any:
        url = self.settings.bitcoin_rpc_url.rstrip("/")
        payload = json.dumps(
            {"jsonrpc": "1.0", "id": "tides-pool", "method": method, "params": params or []}
        ).encode()
        auth = base64.b64encode(
            f"{self.settings.bitcoin_rpc_user}:{self.settings.bitcoin_rpc_password}".encode()
        ).decode()
        req = request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.settings.bitcoin_rpc_timeout) as resp:
                body = json.loads(resp.read().decode())
        except error.HTTPError as e:
            raise BitcoinRPCError(f"HTTP {e.code}: {e.read().decode(errors='replace')}") from e
        except Exception as e:  # noqa: BLE001
            raise BitcoinRPCError(str(e)) from e
        if body.get("error"):
            raise BitcoinRPCError(str(body["error"]))
        return body.get("result")

    def getblockchaininfo(self) -> dict:
        return self.call("getblockchaininfo")

    def getmininginfo(self) -> dict:
        return self.call("getmininginfo")

    def getblockcount(self) -> int:
        return int(self.call("getblockcount"))

    def estimatesubsidy(self, height: int | None = None) -> int:
        """Return subsidy in sats for height (halving schedule)."""
        try:
            if height is None:
                height = self.getblockcount() + 1
        except BitcoinRPCError:
            height = height or 0
        halvings = height // 210_000
        if halvings >= 64:
            return 0
        return (50 * 100_000_000) >> halvings

    def estimate_next_reward(self) -> int:
        """Best-effort next-block reward: GBT coinbasevalue (subsidy+fees) else subsidy.

        Blake2b Knots refuses templates that omit the blake2b rule, so ask for
        both segwit and blake2b. Without blake2b this fell through to subsidy
        only and the website coinbaser drifted from live Gateway amounts.
        """
        for rules in (["segwit", "blake2b"], ["segwit"]):
            try:
                tmpl = self.call("getblocktemplate", [{"rules": rules}])
                if isinstance(tmpl, dict) and tmpl.get("coinbasevalue"):
                    return int(tmpl["coinbasevalue"])
            except BitcoinRPCError:
                continue
        return self.estimatesubsidy()