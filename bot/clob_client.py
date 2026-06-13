"""Thin wrapper around the OFFICIAL Polymarket client (py-clob-client, v0.34.x).

Used for REST reads only (no signing in paper mode):
  - resolution(condition_id)  -> winning outcome index (decides P&L)
  - best_ask(token_id)        -> REST order-book fallback when the websocket has no quote yet

If py-clob-client is not installed the wrapper degrades to .ok = False and callers
fall back to the lightweight REST client in bot/polymarket.py.
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("egig")

try:
    from py_clob_client.client import ClobClient            # official SDK
    _HAVE_SDK = True
except Exception as _e:                                      # pragma: no cover
    ClobClient = None  # type: ignore
    _HAVE_SDK = False


def _outcome_to_index(name: str) -> Optional[int]:
    n = (name or "").strip().lower()
    return 0 if n == "up" else 1 if n == "down" else None


class OfficialClob:
    def __init__(self, host: str, chain_id: int = 137):
        self.ok = _HAVE_SDK
        self.client = None
        if _HAVE_SDK:
            try:
                self.client = ClobClient(host, chain_id=chain_id)
                log.info("py-clob-client ready (host=%s chain=%s)", host, chain_id)
            except Exception as e:
                log.warning("py-clob-client init failed (%s); using REST fallback", e)
                self.ok = False

    def best_ask(self, token_id: str) -> Optional[float]:
        if not self.ok:
            return None
        try:
            ob = self.client.get_order_book(token_id)
            asks = getattr(ob, "asks", None) or []
            prices = [float(getattr(a, "price", a["price"])) for a in asks]
            return min(prices) if prices else None
        except Exception as e:
            log.debug("official best_ask %s: %s", str(token_id)[:12], e)
            return None

    def last_price(self, token_id: str) -> Optional[float]:
        if not self.ok:
            return None
        try:
            r = self.client.get_last_trade_price(token_id)
            p = r.get("price") if isinstance(r, dict) else r
            return float(p) if p is not None else None
        except Exception:
            return None

    def resolution(self, condition_id: str) -> Optional[int]:
        """Winning outcome index from the official market endpoint, else None."""
        if not self.ok:
            return None
        try:
            m = self.client.get_market(condition_id)
            for t in (m.get("tokens") or []):
                if t.get("winner"):
                    return _outcome_to_index(t.get("outcome", ""))
        except Exception as e:
            log.debug("official resolution %s: %s", str(condition_id)[:12], e)
        return None
