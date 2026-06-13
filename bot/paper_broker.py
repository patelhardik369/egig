"""Paper broker: records simulated fills, settles them from Polymarket resolution,
tracks cash/P&L, and persists everything. No real orders are ever sent.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from .config import Settings
from .models import Position, Window
from .store import Store

log = logging.getLogger("egig")


class PaperBroker:
    def __init__(self, settings: Settings, store: Store):
        self.s = settings
        self.store = store
        self.positions: Dict[str, Position] = {}
        self._last_settle = 0.0
        self._lock = threading.RLock()
        self._load()

    # ---------------------------------------------------------------- persistence
    def _load(self) -> None:
        for d in self.store.load_positions():
            try:
                p = Position(**d)
                self.positions[p.id] = p
            except Exception:
                continue
        if self.positions:
            log.info("loaded %d positions (%d open) from disk",
                     len(self.positions), len(self.open_positions()))

    def _persist(self) -> None:
        self.store.save_positions(self.positions.values())

    # ---------------------------------------------------------------- queries
    def open_positions(self) -> List[Position]:
        with self._lock:
            return [p for p in self.positions.values() if p.status == "open"]

    def recent_settled(self, n: int = 8) -> List[Position]:
        with self._lock:
            done = [p for p in self.positions.values() if p.status == "settled"]
        return sorted(done, key=lambda p: p.ts_settled or 0, reverse=True)[:n]

    def has_position(self, coin: str, epoch: int) -> bool:
        with self._lock:
            return any(p.coin == coin and p.epoch == epoch for p in self.positions.values())

    @property
    def cash(self) -> float:
        with self._lock:
            spent = sum(p.cost for p in self.positions.values())
            got = sum(p.payout for p in self.positions.values() if p.status == "settled")
        return self.s.starting_cash - spent + got

    # ---------------------------------------------------------------- actions
    def fill(self, win: Window, idx: int, ask: float, lead: float,
             sec_left: float = 0.0, shares: Optional[float] = None) -> Optional[Position]:
        if self.has_position(win.coin, win.epoch) and self.s.max_fills_per_window <= 1:
            return None
        if shares is None:
            shares = self.s.stake_usd / ask
        cost = shares * ask
        pid = f"{win.coin}-{win.epoch}-{int(time.time() * 1000)}"
        p = Position(
            id=pid, coin=win.coin, epoch=win.epoch, condition_id=win.condition_id,
            token_id=win.token_ids[idx], outcome_index=idx, outcome_name=win.outcomes[idx],
            entry_price=ask, shares=shares, cost=cost, ts_entry=time.time(), lead_at_entry=lead,
            sec_left_at_entry=sec_left,
        )
        with self._lock:
            self.positions[pid] = p
            self._persist()
        self.store.append_trade(p, "FILL")
        log.info("FILL  %-3s %-4s %7.0f sh @ %.3f  cost $%5.2f  lead=%+.4f%%  %.1fs-left",
                 p.coin.upper(), p.outcome_name, p.shares, p.entry_price, p.cost,
                 p.lead_at_entry, sec_left)
        return p

    def settlement_pass(self, resolve_fn, now: float, force: bool = False) -> None:
        """Settle any open position whose window has ended.

        `resolve_fn(condition_id) -> Optional[int]` is the ONLY thing that decides win/loss
        and must come from Polymarket's resolution status (official client / Gamma).
        """
        if not force and now - self._last_settle < self.s.settle_every_s:
            return
        self._last_settle = now
        changed = False
        for p in self.open_positions():
            if p.status != "open":
                continue
            if now < p.epoch + self.s.window_seconds + self.s.settle_buffer_s:
                continue
            try:
                winning = resolve_fn(p)                      # one bad lookup can't stop the rest
            except Exception as e:
                log.debug("resolve %s: %s", str(p.condition_id)[:12], e)
                continue
            if winning is None:
                continue                                    # not resolved yet; retry next pass
            self._settle(p, winning)
            changed = True
        if changed:
            self._persist()
            self.store.append_equity(self.summary(now))

    def _settle(self, p: Position, winning_index: int) -> None:
        with self._lock:
            if p.status != "open":                          # idempotent: never settle twice
                return
            p.status = "settled"
        p.resolved_index = winning_index
        p.payout = p.shares * 1.0 if winning_index == p.outcome_index else 0.0
        p.pnl = p.payout - p.cost
        p.status = "settled"
        p.ts_settled = time.time()
        self.store.append_trade(p, "SETTLE")
        result = "WIN " if p.payout > 0 else "LOSS"
        log.info("%s %-3s %-4s resolved=%s  payout $%6.2f  pnl $%+7.2f",
                 result, p.coin.upper(), p.outcome_name, winning_index, p.payout, p.pnl)

    # ---------------------------------------------------------------- reporting
    def summary(self, now: Optional[float] = None) -> dict:
        with self._lock:
            settled = [p for p in self.positions.values() if p.status == "settled"]
        wins = [p for p in settled if p.payout > 0]
        realized = sum(p.pnl for p in settled)
        deployed = sum(p.cost for p in self.open_positions())
        return {
            "ts": f"{(now or time.time()):.0f}",
            "cash": round(self.cash, 2),
            "open": len(self.open_positions()),
            "open_cost": round(deployed, 2),
            "settled": len(settled),
            "wins": len(wins),
            "win_rate_pct": round(len(wins) / len(settled) * 100, 2) if settled else 0.0,
            "realized_pnl": round(realized, 2),
            "equity": round(self.cash + deployed, 2),
        }
