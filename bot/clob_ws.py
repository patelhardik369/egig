"""Real-time Polymarket odds via the CLOB WebSocket market channel.

  wss://ws-subscriptions-clob.polymarket.com/ws/market

Maintains a thread-safe live best_bid / best_ask / last per token from:
  - `book`            (snapshot on subscribe)
  - `price_change`    (carries best_bid / best_ask per asset)
  - `best_bid_ask`    (direct, when custom_feature_enabled)
  - `last_trade_price`(live traded price)

Auto-reconnects with backoff and re-subscribes the active token set. The engine
subscribes a window's two tokens shortly before its close and unsubscribes after,
so the stream stays lean and focused on the actual decision windows.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Dict, List, Optional

import websocket  # websocket-client

log = logging.getLogger("egig")


class ClobMarketStream:
    def __init__(self, url: str, stale_s: float = 12.0):
        self.url = url
        self.stale_s = stale_s
        self._tokens: set[str] = set()
        self._book: Dict[str, dict] = {}          # asset_id -> {bid, ask, last, ts}
        self._lock = threading.RLock()
        self._ws: Optional[websocket.WebSocketApp] = None
        self._connected = threading.Event()
        self._stop = False
        self._thread: Optional[threading.Thread] = None

    # ----------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="clob-ws", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop = True
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    # ----------------------------------------------------------------- subs
    def subscribe(self, assets: List[str]) -> None:
        with self._lock:
            new = [a for a in assets if a not in self._tokens]
            self._tokens.update(assets)
        if new and self._connected.is_set():
            self._send({"operation": "subscribe", "assets_ids": new,
                        "type": "market", "custom_feature_enabled": True})

    def unsubscribe(self, assets: List[str]) -> None:
        with self._lock:
            rem = [a for a in assets if a in self._tokens]
            for a in assets:
                self._tokens.discard(a)
                self._book.pop(a, None)
        if rem and self._connected.is_set():
            self._send({"operation": "unsubscribe", "assets_ids": rem, "type": "market"})

    def _send(self, obj: dict) -> None:
        try:
            if self._ws:
                self._ws.send(json.dumps(obj))
        except Exception as e:
            log.debug("ws send: %s", e)

    # ----------------------------------------------------------------- reads
    def best_ask(self, asset_id: str) -> Optional[float]:
        return self._field(asset_id, "ask")

    def best_bid(self, asset_id: str) -> Optional[float]:
        return self._field(asset_id, "bid")

    def last(self, asset_id: str) -> Optional[float]:
        return self._field(asset_id, "last")

    def _field(self, asset_id: str, key: str) -> Optional[float]:
        with self._lock:
            d = self._book.get(asset_id)
            if not d or (time.time() - d.get("ts", 0)) > self.stale_s:
                return None                       # no quote / stale -> caller falls back to REST
            v = d.get(key)
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    def connected(self) -> bool:
        return self._connected.is_set()

    # ----------------------------------------------------------------- ws callbacks
    def _on_open(self, ws) -> None:
        with self._lock:
            toks = list(self._tokens)
        if toks:
            ws.send(json.dumps({"assets_ids": toks, "type": "market",
                                "initial_dump": True, "custom_feature_enabled": True}))
        self._connected.set()
        log.info("ws connected (%d tokens)", len(toks))

    def _on_message(self, ws, msg: str) -> None:
        try:
            data = json.loads(msg)
        except Exception:
            return
        for ev in (data if isinstance(data, list) else [data]):
            if isinstance(ev, dict):
                self._apply(ev)

    def _apply(self, ev: dict) -> None:
        et = ev.get("event_type")
        now = time.time()
        with self._lock:
            if et == "book":
                aid = ev.get("asset_id")
                if not aid:
                    return
                d = self._book.setdefault(aid, {})
                asks = ev.get("asks") or []
                bids = ev.get("bids") or []
                try:
                    if asks: d["ask"] = min(float(a["price"]) for a in asks)
                    if bids: d["bid"] = max(float(b["price"]) for b in bids)
                except Exception:
                    pass
                d["ts"] = now
            elif et == "price_change":
                for pc in ev.get("price_changes", []):
                    aid = pc.get("asset_id")
                    if not aid:
                        continue
                    d = self._book.setdefault(aid, {})
                    if pc.get("best_ask") is not None: d["ask"] = pc["best_ask"]
                    if pc.get("best_bid") is not None: d["bid"] = pc["best_bid"]
                    d["ts"] = now
            elif et == "best_bid_ask":
                aid = ev.get("asset_id")
                if aid:
                    d = self._book.setdefault(aid, {})
                    if ev.get("best_ask") is not None: d["ask"] = ev["best_ask"]
                    if ev.get("best_bid") is not None: d["bid"] = ev["best_bid"]
                    d["ts"] = now
            elif et == "last_trade_price":
                aid = ev.get("asset_id")
                if aid:
                    d = self._book.setdefault(aid, {})
                    d["last"] = ev.get("price")
                    d["ts"] = now

    # ----------------------------------------------------------------- run loop
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            self._connected.clear()
            try:
                self._ws = websocket.WebSocketApp(
                    self.url,
                    on_open=self._on_open,
                    on_message=self._on_message,
                    on_error=lambda w, e: log.debug("ws error: %s", e),
                    on_close=lambda w, *a: log.debug("ws closed"),
                )
                self._ws.run_forever(ping_interval=10, ping_timeout=8)
            except Exception as e:
                log.debug("ws run_forever: %s", e)
            if self._stop:
                break
            time.sleep(min(backoff, 15))
            backoff = min(backoff * 2, 15)
