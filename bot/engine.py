"""Live paper-trading engine — trades on REAL-TIME Polymarket odds.

Per tick (~1s):
  1. pull underlying prices (Binance) for all coins        -> decides which side / when (|lead|)
  2. ensure the current/next 5-min window is discovered + snapshot its open price
  3. sample each live window -> update last price + choppiness
  4. shortly before close, subscribe the window's tokens to the CLOB WEBSOCKET (live odds)
  5. near the buzzer, run the strategy; if it fires, paper-fill at the LIVE streamed best ask
  6. settle ended windows from Polymarket resolution status (official py-clob-client, Gamma fallback)

Every external call is guarded; one API hiccup never kills the loop.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, Optional, Tuple

from . import strategy
from .clob_client import OfficialClob
from .clob_ws import ClobMarketStream
from .config import Settings
from .models import Window
from .paper_broker import PaperBroker
from .polymarket import Polymarket
from .pricefeed import BinanceFeed

log = logging.getLogger("egig")


def _bounded(fn, timeout: float, default=None):
    """Run fn() but never block longer than `timeout` (guards SDK calls with no timeout)."""
    box = {}
    t = threading.Thread(target=lambda: box.__setitem__("v", _safe(fn)), daemon=True)
    t.start()
    t.join(timeout)
    return box.get("v", default)


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


class Engine:
    def __init__(self, settings: Settings, feed: BinanceFeed, poly: Polymarket,
                 broker: PaperBroker, stream: Optional[ClobMarketStream] = None,
                 clob: Optional[OfficialClob] = None):
        self.s = settings
        self.feed = feed
        self.poly = poly
        self.broker = broker
        self.stream = stream
        self.clob = clob
        self.windows: Dict[Tuple[str, int], Window] = {}
        self._subbed: set[Tuple[str, int]] = set()
        self._running = False
        self.last_prices: Dict[str, float] = {}
        self.always_sub_current = False          # dashboard mode: stream current window the whole time
        self.started_at = time.time()
        self.halted = False                      # latched True if the drawdown guard trips

    # ------------------------------------------------------------------ resolution
    def resolve(self, condition_id: str, coin: Optional[str] = None,
                epoch: Optional[int] = None) -> Optional[int]:
        """Winning outcome index from Polymarket ONLY. Multi-source, each bounded so a slow
        endpoint can never hang the caller: CLOB+Gamma(by id) -> Gamma(by slug) -> official SDK."""
        r = self.poly.get_resolution(condition_id)                 # bounded REST (proven reliable)
        if r is not None:
            return r
        if coin and epoch is not None:
            r = self.poly.get_resolution_by_slug(coin, epoch)      # bounded REST, slug path
            if r is not None:
                return r
        if self.clob:                                              # SDK has no timeout -> bound it
            r = _bounded(lambda: self.clob.resolution(condition_id), 6.0)
            if r is not None:
                return r
        return None

    def live_ask(self, token_id: str) -> Tuple[Optional[float], str]:
        """Live best ask: websocket first (real-time), then bounded REST fallbacks."""
        if self.stream:
            a = self.stream.best_ask(token_id)
            if a is not None:
                return a, "ws"
        a = self.poly.best_ask(token_id)                           # REST with timeout
        if a is not None:
            return a, "rest"
        if self.clob:
            a = _bounded(lambda: self.clob.best_ask(token_id), 4.0)
            if a is not None:
                return a, "sdk"
        return None, "none"

    # ------------------------------------------------------------------ windows
    def _epoch_for(self, now: float) -> int:
        w = self.s.window_seconds
        return int(now // w) * w

    def _ensure_window(self, coin: str, epoch: int, price: Optional[float], now: float) -> Optional[Window]:
        key = (coin, epoch)
        if key in self.windows:
            return self.windows[key]
        win = self.poly.discover_window(coin, epoch)
        self.windows[key] = win  # type: ignore  # may be None -> caches the miss
        if win and price is not None:
            win.open_price, win.open_ts = price, now
        return win

    def _maybe_subscribe(self, win: Window, now: float) -> None:
        if not self.stream:
            return
        key = (win.coin, win.epoch)
        if key in self._subbed:
            return
        near = (win.end - now) <= self.s.ws_subscribe_lead_s
        if self.always_sub_current or (win.open_valid(self.s.open_snap_tol_s) and near):
            self.stream.subscribe([win.token_ids[0], win.token_ids[1]])
            self._subbed.add(key)

    def _prune(self, now: float) -> None:
        cutoff = now - 2 * self.s.window_seconds
        for key in [k for k, w in self.windows.items() if k[1] < cutoff]:
            win = self.windows.pop(key, None)
            if self.stream and key in self._subbed and win:
                self.stream.unsubscribe([win.token_ids[0], win.token_ids[1]])
            self._subbed.discard(key)

    # ------------------------------------------------------------------ one tick
    def tick(self, now: float) -> None:
        prices = self.feed.prices()
        for c, p in prices.items():
            self.last_prices[c] = p
        epoch = self._epoch_for(now)
        for coin in self.s.coins:
            try:                                          # one bad coin must not break the others
                price = prices.get(coin)
                win = self._ensure_window(coin, epoch, price, now)
                if self.s.prefetch_next:
                    self._ensure_window(coin, epoch + self.s.window_seconds, None, now)
                if not win or price is None:
                    continue
                if win.open_price is None:
                    win.open_price, win.open_ts = price, now
                win.update(price, now)
                self._maybe_subscribe(win, now)
                self._maybe_enter(win, now)
            except Exception as e:
                log.debug("tick %s: %s", coin, e)
        self._prune(now)

    # ------------------------------------------------------------------ settlement loop
    def _settle_resolver(self, p) -> Optional[int]:
        return self.resolve(p.condition_id, p.coin, p.epoch)

    def settle_once(self, now: Optional[float] = None) -> None:
        self.broker.settlement_pass(self._settle_resolver, now or time.time(), force=True)

    def _settle_loop(self) -> None:
        """Dedicated thread: settlement progresses independently of the trading loop / UI."""
        while self._running:
            try:
                self.settle_once()
            except Exception as e:
                log.warning("settle loop: %s", e)
            end = time.time() + self.s.settle_every_s
            while self._running and time.time() < end:
                time.sleep(0.3)

    def _maybe_enter(self, win: Window, now: float) -> None:
        if win.entered or self.broker.has_position(win.coin, win.epoch):
            return
        sig = strategy.evaluate(win, now, self.s)
        if sig is None:
            return
        if self._drawdown_halt(now):                          # risk guard: stop bleeding a -EV regime
            return
        token = win.token_ids[sig.losing_index]
        ask, src = self.live_ask(token)                       # may incur network latency
        # RE-CHECK timing after the odds fetch: never fill a window that has effectively ended
        sec_left = win.end - time.time()
        if sec_left < self.s.entry_lo_s:
            log.debug("skip late fill %s: only %.1fs left at fill time", win.coin, sec_left)
            return
        if ask is None or not (self.s.min_entry_price <= ask <= self.s.max_entry_price):
            return                                            # loser not cheap enough yet (live odds)
        if self.broker.fill(win, sig.losing_index, ask, sig.lead_pct, sec_left=sec_left):
            win.entered = True
            log.debug("entry odds source=%s ask=%.3f %.1fs-left", src, ask, sec_left)

    def _drawdown_halt(self, now: float) -> bool:
        if self.s.max_drawdown_pct >= 100:
            return False
        if self.halted:
            return True
        realized = self.broker.summary(now)["realized_pnl"]
        if realized <= -self.s.starting_cash * self.s.max_drawdown_pct / 100.0:
            self.halted = True
            log.warning("RISK HALT: drawdown %.0f%% hit (realized $%.2f) — no new entries; "
                        "settlement continues. Restart with --fresh after tuning.",
                        self.s.max_drawdown_pct, realized)
            return True
        return False

    # ------------------------------------------------------------------ dashboard snapshot
    def _odds(self, token_id: str) -> dict:
        if not self.stream:
            return {}
        return {"bid": self.stream.best_bid(token_id),
                "ask": self.stream.best_ask(token_id),
                "last": self.stream.last(token_id)}

    def snapshot(self, now: float) -> dict:
        """Thread-safe view of live state for the dashboard."""
        from . import strategy
        epoch = self._epoch_for(now)
        coins = []
        for coin in self.s.coins:
            win = self.windows.get((coin, epoch))
            row = {"coin": coin, "price": self.last_prices.get(coin),
                   "open": None, "lead": None, "sec_left": None, "crossings": 0,
                   "up": {}, "down": {}, "state": "—", "losing": None}
            if win:
                row["open"] = win.open_price
                row["lead"] = win.lead_pct()
                row["sec_left"] = int(win.end - now)
                row["crossings"] = win.crossings
                row["up"] = self._odds(win.token_ids[0])
                row["down"] = self._odds(win.token_ids[1])
                lead = win.lead_pct()
                if lead is not None:
                    row["losing"] = 1 if lead > 0 else 0      # 1=Down behind, 0=Up behind
                try:
                    sig = strategy.evaluate(win, now, self.s)
                except Exception:
                    sig = None
                if win.entered:
                    row["state"] = "FILLED"
                elif sig is not None:
                    row["state"] = "ARMED"
                elif lead is not None and abs(lead) <= self.s.max_lead_pct and win.open_valid(self.s.open_snap_tol_s):
                    row["state"] = "WATCH"
                elif lead is not None:
                    row["state"] = "skip"
            coins.append(row)
        return {
            "coins": coins,
            "summary": self.broker.summary(now),
            "open": self.broker.open_positions(),
            "settled": self.broker.recent_settled(8),
            "ws": bool(self.stream and self.stream.connected()),
            "uptime": now - self.started_at,
            "halted": self.halted,
        }

    # ------------------------------------------------------------------ run loop
    def run(self, duration_s: Optional[float] = None) -> None:
        self._running = True
        if self.stream:
            self.stream.start()
        settle_thread = threading.Thread(target=self._settle_loop, name="settle", daemon=True)
        settle_thread.start()                              # settlement runs independently
        t_end = (time.time() + duration_s) if duration_s else None
        log.info("paper engine | coins=%s | rule |lead|<%.3f%% cross>=%d entry[%d-%ds] <=%.0fc | "
                 "odds=%s | stake $%.2f",
                 ",".join(self.s.coins), self.s.max_lead_pct, self.s.min_crossings,
                 self.s.entry_lo_s, self.s.entry_hi_s, self.s.max_entry_price * 100,
                 "websocket" if self.stream else "REST", self.s.stake_usd)
        try:
            while self._running:
                start = time.time()
                if t_end and start >= t_end:
                    break
                try:
                    self.tick(start)
                except Exception as e:
                    log.warning("tick error: %s", e)
                time.sleep(max(0.0, self.s.sample_interval_s - (time.time() - start)))
        except KeyboardInterrupt:
            log.info("interrupted")
        finally:
            self._running = False
            if self.stream:
                self.stream.stop()
            s = self.broker.summary()
            log.info("stopped | settled=%d wins=%d win%%=%.1f realized=$%.2f equity=$%.2f",
                     s["settled"], s["wins"], s["win_rate_pct"], s["realized_pnl"], s["equity"])

    def stop(self) -> None:
        self._running = False
