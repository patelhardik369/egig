"""Underlying price feed (Binance spot) used ONLY to decide entries (the |lead| filter).

IMPORTANT: this feed never decides win/loss. Polymarket resolves on the Chainlink
stream; Binance spot is a high-fidelity proxy for *when* to act. Settlement is done
elsewhere strictly from Polymarket's resolution status.
"""
from __future__ import annotations

import json
from typing import Dict, List

from .config import SYMBOL
from .http import get_json


class BinanceFeed:
    def __init__(self, host: str, coins: List[str], timeout: float = 8.0):
        self.host = host.rstrip("/")
        self.coins = coins
        self.timeout = timeout
        self._sym2coin = {SYMBOL[c]: c for c in coins}

    def prices(self) -> Dict[str, float]:
        """Latest spot price for each configured coin: {coin: price}. Best-effort."""
        symbols = [SYMBOL[c] for c in self.coins]
        params = {"symbols": json.dumps(symbols)}          # /ticker/price supports a symbols array
        out: Dict[str, float] = {}
        try:
            data = get_json(f"{self.host}/api/v3/ticker/price", params=params, timeout=self.timeout)
            for row in data:
                coin = self._sym2coin.get(row.get("symbol"))
                if coin:
                    out[coin] = float(row["price"])
        except Exception:
            # fall back to per-symbol calls so one bad coin can't blank the whole tick
            for c in self.coins:
                try:
                    d = get_json(f"{self.host}/api/v3/ticker/price",
                                 params={"symbol": SYMBOL[c]}, timeout=self.timeout)
                    out[c] = float(d["price"])
                except Exception:
                    pass
        return out
