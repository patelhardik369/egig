"""Polymarket read-only client (no auth needed for paper trading).

Three jobs:
  1. discover_window()  - find the 5-min market for a (coin, epoch) via Gamma
  2. best_ask()         - current cheapest sell offer for a token (the paper fill price), via CLOB
  3. get_resolution()   - the WINNING outcome index once the market resolves  <-- decides P&L

get_resolution() is the single source of truth for win/loss, per the project rule:
"final result decided from Polymarket resolution status only."
"""
from __future__ import annotations

import json
import logging
from typing import Dict, Optional

from .config import SLUG_PREFIX
from .http import get_json
from .models import Window, UP, DOWN

log = logging.getLogger("egig")


def _name_to_index(name: str) -> Optional[int]:
    n = (name or "").strip().lower()
    if n == "up": return UP
    if n == "down": return DOWN
    return None


def _as_list(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return None
    return v


def _winner_from_market(m: dict) -> Optional[int]:
    """Winning outcome index from a CLOB or Gamma market object, else None.

    Handles: CLOB `tokens[].winner`, and Gamma `outcomePrices` (["1","0"]) once the
    market is closed/resolved. Returns None while the market is still live.
    """
    if not isinstance(m, dict):
        return None
    # 1) CLOB token winner flag
    for t in (m.get("tokens") or []):
        if t.get("winner"):
            i = _name_to_index(t.get("outcome", ""))
            if i is not None:
                return i
    # 2) Gamma outcomePrices, only trust once the market is closed/resolved
    uma = m.get("umaResolutionStatuses")
    resolved = bool(m.get("closed")) or (uma not in (None, "", "[]"))
    op = _as_list(m.get("outcomePrices"))
    if resolved and op and len(op) >= 2:
        try:
            vals = [float(x) for x in op]
        except (TypeError, ValueError):
            return None
        if max(vals) >= 0.99:                      # a real 1/0 resolution (not live 0.5/0.5)
            mx = max(range(len(vals)), key=lambda i: vals[i])
            outs = _as_list(m.get("outcomes"))
            if outs and mx < len(outs):
                idx = _name_to_index(outs[mx])
                if idx is not None:
                    return idx
            return mx                              # assume canonical [Up, Down] order
    return None


class Polymarket:
    def __init__(self, gamma_host: str, clob_host: str, timeout: float = 8.0):
        self.gamma = gamma_host.rstrip("/")
        self.clob = clob_host.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------ discovery
    def discover_window(self, coin: str, epoch: int) -> Optional[Window]:
        slug = f"{SLUG_PREFIX[coin]}-5m-{epoch}"
        try:
            events = get_json(f"{self.gamma}/events", params={"slug": slug}, timeout=self.timeout)
        except Exception as e:
            log.debug("discover %s: %s", slug, e)
            return None
        if not events or not events[0].get("markets"):
            return None
        m = events[0]["markets"][0]
        cid = m.get("conditionId")
        try:
            outcomes = json.loads(m["outcomes"]) if isinstance(m.get("outcomes"), str) else m.get("outcomes")
            token_ids = json.loads(m["clobTokenIds"]) if isinstance(m.get("clobTokenIds"), str) else m.get("clobTokenIds")
        except Exception:
            return None
        if not cid or not outcomes or not token_ids or len(outcomes) != len(token_ids):
            return None

        idx_tokens: Dict[int, str] = {}
        idx_names: Dict[int, str] = {}
        for name, tid in zip(outcomes, token_ids):
            i = _name_to_index(name)
            if i is None:
                return None
            idx_tokens[i] = str(tid)
            idx_names[i] = name
        if UP not in idx_tokens or DOWN not in idx_tokens:
            return None
        return Window(coin=coin, epoch=epoch, condition_id=cid, token_ids=idx_tokens, outcomes=idx_names)

    # ------------------------------------------------------------------ order book
    def best_ask(self, token_id: str) -> Optional[float]:
        """Lowest ask price for `token_id` (what a buyer pays right now). None if no book."""
        try:
            book = get_json(f"{self.clob}/book", params={"token_id": token_id}, timeout=self.timeout)
        except Exception as e:
            log.debug("book %s: %s", token_id[:12], e)
            return None
        asks = book.get("asks") or []
        prices = []
        for a in asks:
            try:
                prices.append(float(a["price"]))
            except (KeyError, TypeError, ValueError):
                continue
        return min(prices) if prices else None

    # ------------------------------------------------------------------ resolution
    def get_resolution(self, condition_id: str) -> Optional[int]:
        """Winning outcome index (0=Up, 1=Down) once resolved, else None.

        Source 1: CLOB market `tokens[].winner`.  Source 2: Gamma market by condition id
        (`outcomePrices` once closed). Both bounded by the HTTP timeout.
        """
        try:
            m = get_json(f"{self.clob}/markets/{condition_id}", timeout=self.timeout)
            r = _winner_from_market(m)
            if r is not None:
                return r
        except Exception as e:
            log.debug("clob resolve %s: %s", str(condition_id)[:12], e)
        try:
            mk = get_json(f"{self.gamma}/markets",
                          params={"condition_ids": condition_id}, timeout=self.timeout)
            mk = mk[0] if isinstance(mk, list) and mk else mk
            r = _winner_from_market(mk) if mk else None
            if r is not None:
                return r
        except Exception as e:
            log.debug("gamma resolve %s: %s", str(condition_id)[:12], e)
        return None

    def get_resolution_by_slug(self, coin: str, epoch: int) -> Optional[int]:
        """Resolution via the same Gamma /events?slug path discovery uses (most reliable)."""
        slug = f"{SLUG_PREFIX[coin]}-5m-{epoch}"
        try:
            ev = get_json(f"{self.gamma}/events", params={"slug": slug}, timeout=self.timeout)
            if ev and ev[0].get("markets"):
                return _winner_from_market(ev[0]["markets"][0])
        except Exception as e:
            log.debug("slug resolve %s: %s", slug, e)
        return None
