#!/usr/bin/env python3
"""egig paper-trading bot — entry point.

Usage:
  python main.py run [--minutes N]     # start live paper trading (Ctrl-C to stop)
  python main.py status                # print portfolio summary
  python main.py settle                # one-off: settle any ended windows from Polymarket
  python main.py config                # print effective settings

Paper only. Win/loss is decided strictly by Polymarket's resolution status.
"""
from __future__ import annotations

import argparse
import sys
import time

from bot.config import settings
from bot.logging_setup import setup_logging
from bot.store import Store
from bot.pricefeed import BinanceFeed
from bot.polymarket import Polymarket
from bot.clob_client import OfficialClob
from bot.clob_ws import ClobMarketStream
from bot.paper_broker import PaperBroker
from bot.engine import Engine


def _build(fresh: bool = False):
    log = setup_logging(settings.log_level, settings.data_dir / "logs")
    store = Store(settings.data_dir)
    if fresh:
        n = store.reset()                # wipe positions/trades/equity BEFORE the broker loads
        log.info("--fresh: wiped %d state file(s) in %s", n, settings.data_dir)
    feed = BinanceFeed(settings.binance_host, settings.coins, settings.http_timeout_s)
    poly = Polymarket(settings.gamma_host, settings.clob_host, settings.http_timeout_s)
    clob = OfficialClob(settings.clob_host, settings.chain_id) if settings.use_official_client else None
    stream = ClobMarketStream(settings.clob_ws_host, settings.ws_stale_s) if settings.use_websocket else None
    broker = PaperBroker(settings, store)
    return log, store, feed, poly, clob, stream, broker


def _print_summary(broker: PaperBroker) -> None:
    s = broker.summary()
    print("\n=== egig paper portfolio ===")
    for k in ("cash", "equity", "realized_pnl", "open", "open_cost", "settled", "wins", "win_rate_pct"):
        print(f"  {k:<14}: {s[k]}")
    openp = broker.open_positions()
    if openp:
        print("\n  open positions:")
        for p in sorted(openp, key=lambda x: x.epoch):
            print(f"    {p.coin.upper():<4} {p.outcome_name:<4} {p.shares:7.0f}sh @ {p.entry_price:.3f} "
                  f"(epoch {p.epoch}, lead {p.lead_at_entry:+.4f}%)")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="egig Polymarket 5-min paper bot")
    sub = ap.add_subparsers(dest="cmd")
    runp = sub.add_parser("run", help="start live paper trading (plain logs)")
    runp.add_argument("--minutes", type=float, default=None, help="run for N minutes then stop")
    runp.add_argument("--fresh", action="store_true", help="wipe positions/trades/equity before starting")
    dashp = sub.add_parser("dash", help="live full-screen dashboard (recommended)")
    dashp.add_argument("--fresh", action="store_true", help="wipe positions/trades/equity before starting")
    sub.add_parser("status", help="print portfolio summary")
    sub.add_parser("settle", help="settle ended windows once, from Polymarket resolution")
    sub.add_parser("config", help="print effective settings")
    args = ap.parse_args(argv)

    if args.cmd == "config":
        from dataclasses import asdict
        for k, v in asdict(settings).items():
            print(f"{k:<20}= {v}")
        return 0

    log, store, feed, poly, clob, stream, broker = _build(fresh=getattr(args, "fresh", False))
    engine = Engine(settings, feed, poly, broker, stream=stream, clob=clob)

    if args.cmd == "status" or args.cmd is None:
        _print_summary(broker)
        return 0

    if args.cmd == "settle":
        engine.settle_once()                 # resolution strictly from Polymarket status
        _print_summary(broker)
        return 0

    if args.cmd == "dash":
        from bot.dashboard import run_dashboard
        from bot.logging_setup import disable_console
        disable_console(log)                 # keep the TUI clean; logs go to data/logs/bot.log
        engine.always_sub_current = True     # stream all 4 coins' current odds continuously
        run_dashboard(engine)
        return 0

    if args.cmd == "run":
        engine.run(duration_s=(args.minutes * 60) if args.minutes else None)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
