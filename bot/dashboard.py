"""Full-screen dark TUI dashboard (rich). One screen, no scrolling.

Shows, live and in place:
  - 4 coins: underlying price, window open, Δ lead %, seconds left, live Polymarket
    Up/Down odds (bid·ask·last), and the bot's signal state per market
  - open positions (what it's trading) and recently resolved results
  - rolling P&L / equity / win-rate footer

Runs the engine in a background thread; renders the shared snapshot in the main thread.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

ACCENT = "bright_cyan"
BORDER = "grey37"
GOOD = "bright_green"
BAD = "bright_red"
WARN = "yellow"
DIM = "grey50"
BG = "grey7"


# ----------------------------------------------------------------- formatters
def fmt_price(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v >= 1000:
        return f"{v:,.1f}"
    if v >= 100:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    return f"{v:.5f}"


def fmt_odd(v) -> str:
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "·"


def lead_text(lead: Optional[float], max_lead: float) -> Text:
    if lead is None:
        return Text("—", style=DIM)
    style = GOOD if abs(lead) <= max_lead else (WARN if abs(lead) <= max_lead * 2 else DIM)
    return Text(f"{lead:+.4f}%", style=f"bold {style}")


def secs_text(s: Optional[int], lo: int, hi: int) -> Text:
    if s is None:
        return Text("—", style=DIM)
    if s <= hi:
        return Text(f"{s:>3}s", style="bold bright_red")       # in the entry zone
    if s <= 45:
        return Text(f"{s:>3}s", style=WARN)
    return Text(f"{s:>3}s", style=DIM)


def odds_cell(o: dict, highlight: bool) -> Text:
    bid, ask, last = fmt_odd(o.get("bid")), fmt_odd(o.get("ask")), fmt_odd(o.get("last"))
    style = f"bold {WARN}" if highlight else "grey85"
    return Text(f"{bid} · {ask} · {last}", style=style)


def chop_text(n: int, min_cross: int) -> Text:
    # green once the window is choppy enough to qualify (the selection brain)
    return Text(f"{n:>2}", style=f"bold {GOOD}" if n >= min_cross else DIM)


def state_text(state: str) -> Text:
    return {
        "FILLED": Text("● FILLED", style=f"bold {GOOD}"),
        "ARMED":  Text("◆ ARMED", style="bold yellow"),
        "WATCH":  Text("WATCH", style=ACCENT),
        "skip":   Text("·", style=DIM),
    }.get(state, Text("—", style=DIM))


def money(v: float, signed: bool = False) -> Text:
    style = GOOD if v > 0 else BAD if v < 0 else "grey85"
    s = f"{v:+,.2f}" if signed else f"{v:,.2f}"
    return Text(f"${s}", style=f"bold {style}")


# ----------------------------------------------------------------- panels
def _markets_panel(snap: dict, s) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False, padding=(0, 1))
    t.add_column("COIN", style="bold white", no_wrap=True)
    t.add_column("PRICE", justify="right", no_wrap=True)
    t.add_column("OPEN", justify="right", style=DIM, no_wrap=True)
    t.add_column("Δ LEAD", justify="right", no_wrap=True)
    t.add_column("⏱", justify="right", no_wrap=True)
    t.add_column("↕CHOP", justify="right", no_wrap=True)
    t.add_column("UP  bid·ask·last", justify="center", no_wrap=True)
    t.add_column("DOWN  bid·ask·last", justify="center", no_wrap=True)
    t.add_column("SIGNAL", justify="left", no_wrap=True)
    for c in snap["coins"]:
        losing = c.get("losing")
        t.add_row(
            Text(c["coin"].upper(), style=f"bold {ACCENT}"),
            Text(fmt_price(c["price"]), style="bold white"),
            fmt_price(c["open"]),
            lead_text(c["lead"], s.max_lead_pct),
            secs_text(c["sec_left"], s.entry_lo_s, s.entry_hi_s),
            chop_text(c.get("crossings", 0), s.min_crossings),
            odds_cell(c["up"], highlight=(losing == 0)),
            odds_cell(c["down"], highlight=(losing == 1)),
            state_text(c["state"]),
        )
    return Panel(t, title="[bold]LIVE MARKETS · 5-min Up/Down[/]", title_align="left",
                 border_style=BORDER, box=box.ROUNDED, padding=(0, 1))


def _status_text(p) -> Text:
    age = time.time() - (p.epoch + 300)
    if age < 0:
        return Text(f"live {int(-age)}s", style=ACCENT)          # window still running
    m, s = int(age) // 60, int(age) % 60
    label = f"⏳ {m}m{s:02d}s" if m else f"⏳ {s}s"
    return Text(label, style=WARN if age < 120 else BAD)         # awaiting resolution


def _positions_panel(snap: dict) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False, padding=(0, 1))
    for col, j in (("COIN", "left"), ("SIDE", "left"), ("SH", "right"),
                   ("ENTRY", "right"), ("COST", "right"), ("AWAITING", "right")):
        t.add_column(col, justify=j, no_wrap=True)
    rows = sorted(snap["open"], key=lambda p: p.epoch, reverse=True)[:9]
    for p in rows:
        side_style = GOOD if p.outcome_name == "Up" else BAD
        t.add_row(Text(p.coin.upper(), style=f"bold {ACCENT}"),
                  Text(p.outcome_name, style=f"bold {side_style}"),
                  f"{p.shares:,.0f}", f"{p.entry_price:.3f}", f"${p.cost:,.2f}",
                  _status_text(p))
    if not rows:
        t.add_row(Text("no open positions", style=DIM), "", "", "", "", "")
    return Panel(t, title=f"[bold]TRADING · {len(snap['open'])} open[/]", title_align="left",
                 border_style=BORDER, box=box.ROUNDED, padding=(0, 1))


def _results_panel(snap: dict) -> Panel:
    t = Table(box=box.SIMPLE_HEAD, expand=True, pad_edge=False, padding=(0, 1))
    for col, j in (("COIN", "left"), ("SIDE", "left"), ("RESULT", "left"), ("PNL", "right")):
        t.add_column(col, justify=j, no_wrap=True)
    for p in snap["settled"]:
        win = p.payout > 0
        t.add_row(Text(p.coin.upper(), style=f"bold {ACCENT}"),
                  p.outcome_name,
                  Text("WIN " if win else "LOSS", style=f"bold {GOOD if win else BAD}"),
                  money(p.pnl, signed=True))
    if not snap["settled"]:
        t.add_row(Text("no results yet", style=DIM), "", "", "")
    return Panel(t, title="[bold]RESOLVED · from Polymarket[/]", title_align="left",
                 border_style=BORDER, box=box.ROUNDED, padding=(0, 1))


def _header(snap: dict, s) -> Panel:
    sm = snap["summary"]
    up = int(snap["uptime"])
    ws = Text("● WS LIVE", style=f"bold {GOOD}") if snap["ws"] else Text("○ WS down", style=f"bold {BAD}")
    g = Table.grid(expand=True)
    g.add_column(justify="left"); g.add_column(justify="center"); g.add_column(justify="right")
    left = Text.assemble(("⚡ egig ", f"bold {ACCENT}"), ("paper-trader", "bold white on dark_cyan"))
    halt = Text("  ⛔ HALTED", style=f"bold {BAD}") if snap.get("halted") else Text("")
    mid = Text.assemble((time.strftime("%H:%M:%S"), "bold white"), ("  up ", DIM),
                        (f"{up//3600:02d}:{up%3600//60:02d}:{up%60:02d}  ", "white"), ws) + halt
    right = Text.assemble(("EQUITY ", DIM), (f"${sm['equity']:,.2f}", "bold white"),
                          ("   P&L ", DIM)) + money(sm["realized_pnl"], signed=True)
    g.add_row(left, mid, right)
    return Panel(g, border_style=ACCENT, box=box.HEAVY, padding=(0, 1))


def _footer(snap: dict, s) -> Panel:
    sm = snap["summary"]
    g = Table.grid(expand=True)
    for _ in range(6):
        g.add_column(justify="center", ratio=1)
    def cell(label, val):
        return Text.assemble((f"{label}\n", DIM), val if isinstance(val, Text) else (str(val), "bold white"))
    g.add_row(
        cell("TRADES", str(sm["settled"] + snap["summary"]["open"])),
        cell("WINS", Text(f"{sm['wins']}  ({sm['win_rate_pct']:.0f}%)", style=f"bold {GOOD}")),
        cell("REALIZED", money(sm["realized_pnl"], signed=True)),
        cell("OPEN", Text(f"{sm['open']}  (${sm['open_cost']:,.0f})", style="bold white")),
        cell("CASH", Text(f"${sm['cash']:,.2f}", style="bold white")),
        cell("RULE", Text(f"|lead|<{s.max_lead_pct:.02f}% ≤{s.max_entry_price*100:.0f}c", style=ACCENT)),
    )
    return Panel(g, border_style=BORDER, box=box.ROUNDED, padding=(0, 1))


def render(engine, now: float) -> Layout:
    snap = engine.snapshot(now)
    s = engine.s
    root = Layout()
    root.split_column(
        Layout(_header(snap, s), name="header", size=3),
        Layout(_markets_panel(snap, s), name="markets", size=len(snap["coins"]) + 5),
        Layout(name="mid"),
        Layout(_footer(snap, s), name="footer", size=4),
    )
    root["mid"].split_row(
        Layout(_positions_panel(snap), name="pos"),
        Layout(_results_panel(snap), name="res"),
    )
    return root


# ----------------------------------------------------------------- runner
def run_dashboard(engine, refresh_per_second: int = 8) -> None:
    console = Console()
    t = threading.Thread(target=engine.run, kwargs={"duration_s": None}, daemon=True, name="engine")
    t.start()
    time.sleep(0.3)
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while t.is_alive():
                try:
                    live.update(render(engine, time.time()))
                    live.refresh()
                except Exception:
                    pass
                time.sleep(1.0 / max(1, refresh_per_second))
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        t.join(timeout=5)
        console.print("[bold cyan]dashboard stopped.[/] paper state saved to data/.")
