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

    def window_choppiness(self, coin: str, T: int, now_s: int):
        """Authoritative crossing count of the open level over [T, now_s], from 1s kline CLOSES.

        This matches the research definition exactly. The per-tick ticker price jitters across
        the open and over-counts crossings; 1s-kline closes are the clean, validated measure.
        Returns int crossings, or None on failure (caller keeps the live estimate).
        """
        try:
            data = get_json(f"{self.host}/api/v3/klines",
                            params={"symbol": SYMBOL[coin], "interval": "1s",
                                    "startTime": (T - 2) * 1000, "endTime": now_s * 1000,
                                    "limit": 500}, timeout=self.timeout)
        except Exception:
            return None
        if not data:
            return None
        open_px = next((float(k[1]) for k in data if k[0] // 1000 == T), float(data[0][1]))
        cross, prev = 0, None
        for k in data:
            t = k[0] // 1000
            if t < T or t > now_s:
                continue
            sign = 1 if float(k[4]) >= open_px else -1
            if prev is not None and sign != prev:
                cross += 1
            prev = sign
        return cross
