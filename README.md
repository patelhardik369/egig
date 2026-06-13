# egig — Polymarket 5‑min Up/Down paper‑trading bot

A research‑driven **paper‑trading** replica of the `egig` bot (wallet
`0x69c7…b03c`) that farms **last‑second longshot reversals** on Polymarket's
5‑minute crypto Up/Down markets.

> **Golden rule of this project:** entries are *decided* from a price feed (the
> `|lead|` filter), but **win/loss is decided strictly from Polymarket's
> resolution status** — never from Binance. No real orders are ever sent.

---

## The strategy (reverse‑engineered)

On a 5‑min window `[T, T+300]`, the market resolves **Up if price(T+300) ≥
price(T)** (Chainlink `<coin>/USD`). The edge:

1. **When** — only the final **~3–12 seconds** before close.
2. **What** — buy the **losing** side (the outcome currently behind) at **1–3¢**.
3. **Where (the brain)** — only when the move **never committed**: the underlying
   is still within **`|lead| < ~0.04%`** of the open (research‑tuned). Such windows
   flip **~10–18%** of the time while the loser is priced at ~2% → the mispricing
   is the edge. Windows where one side ran away are equally cheap but **dead** (<1%
   flip) and are skipped.
4. **Hold** to resolution, redeem winners, never sell.

Betting cheap on *every* window is ~break‑even (~2.5%); the `|lead|` filter lifts the
hit‑rate to ~6–9%. Full derivation + grid‑search in [`script/`](script/).

---

## Project layout

```
.
├── main.py              # CLI entry point (run / status / settle / config)
├── requirements.txt
├── .env.example         # copy to .env and tune
├── .gitignore
├── README.md
│
├── bot/                 # ── the bot (all main project code) ──
│   ├── config.py        #   settings from .env (every strategy knob)
│   ├── models.py        #   Window / Position / Signal
│   ├── pricefeed.py     #   Binance feed — decides ENTRIES only (the |lead| filter)
│   ├── clob_ws.py       #   CLOB WebSocket — REAL-TIME live odds (best bid/ask/last)
│   ├── clob_client.py   #   OFFICIAL py-clob-client — resolution + REST book fallback
│   ├── polymarket.py    #   Gamma discovery + resolution fallback
│   ├── strategy.py      #   the selection brain (pure function)
│   ├── paper_broker.py  #   simulated fills, settlement, P&L, persistence
│   ├── engine.py        #   the live loop (subscribes WS, fills on live odds)
│   ├── store.py         #   atomic JSON / CSV persistence
│   ├── http.py          #   retrying HTTP-JSON helper
│   └── logging_setup.py
│
├── script/              # ── research only (scripts + their data + cache) ──
│   ├── analyze_pnl.py   buys_enriched.json   pnl_curves.{csv,png}
│   ├── strategy.py      universe.json        egig_activity_7d.json
│   ├── select_brain.py  gridsearch.py        binance_cache/  ...
│
└── data/                # ── bot runtime data ONLY ──
    ├── positions.json   #   all paper positions (open + settled)
    ├── trades.csv       #   append-only fill/settle log
    ├── equity.csv       #   equity curve snapshots
    └── logs/bot.log
```

---

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     |  *nix: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # optional; defaults work out of the box
```

No API keys are required for paper trading (Gamma, CLOB and Binance reads are public).

## Run

```bash
python main.py dash              # ⭐ live full-screen dashboard (recommended)
python main.py dash --fresh      # ...starting from a clean slate (wipes positions/trades/equity)
python main.py run               # headless live paper trading (plain logs); Ctrl-C to stop
python main.py run --minutes 30  # run for 30 minutes then stop
python main.py run --fresh       # wipe state and start fresh
python main.py status            # portfolio summary (cash, P&L, win rate, open positions)
python main.py settle            # force a one-off settlement pass
python main.py config            # print effective settings
```

> **`--fresh`** deletes `data/positions.json`, `data/trades.csv` and `data/equity.csv` before
> starting (logs are kept). Use it to begin a clean paper session.

### Entry-timing safety

Entries fire only in the final `[ENTRY_LO_S, ENTRY_HI_S]` seconds, and the seconds-left is
**re-checked after the live-odds fetch** — so a slow network round-trip can never fill a window
that has already ended. Fills are stamped with `sec_left_at_entry` (visible in `trades.csv`).

### Dashboard (`dash`)

A dark, single-screen TUI (no scrolling) that shows everything live:

```
⚡ egig paper-trader        16:41:11  up 00:00:37  ● WS LIVE        EQUITY $974.95  P&L $-25.05
┌ LIVE MARKETS · 5-min Up/Down ───────────────────────────────────────────────────────────────┐
│ COIN   PRICE      OPEN       Δ LEAD     ⏱     UP bid·ask·last     DOWN bid·ask·last   SIGNAL  │
│ BTC    63,939.8   63,934.1   +0.0088%   228s  0.45·0.46·0.46      0.54·0.55·0.55      WATCH   │
│ ...                                                                                           │
└ TRADING · 0 open ───────────────┐┌ RESOLVED · from Polymarket ───────────────────────────────┘
                                   ││ BTC  Down  LOSS  $-8.35  ...
TRADES 3 · WINS 0 (0%) · REALIZED $-25.05 · OPEN 0 · CASH $974.95 · RULE |lead|<0.04% ≤3c
```

- **LIVE MARKETS** — all 4 coins: underlying price, the window's open, **Δ lead %** (green = inside
  the filter, dim = dead), seconds left (red in the entry zone), and **live Polymarket Up/Down odds**
  (`bid·ask·last`, longshot side highlighted) streamed from the websocket.
- **SIGNAL** — `WATCH` → `◆ ARMED` (about to fire) → `● FILLED`.
- **TRADING** — open positions; **RESOLVED** — settled results (W/L + P&L) decided by Polymarket.
- Logs are redirected to `data/logs/bot.log` while the dashboard is up. Press `Ctrl-C` to quit.

The engine discovers each coin's current 5‑min market, snapshots its open price,
tracks the live price + choppiness, fires the strategy near the buzzer, paper‑fills
the loser at the live CLOB ask, then **settles from Polymarket resolution**.

## Tuning

Everything is in `.env` (see `bot/config.py`). The most important knobs:

| Variable | Default | Effect |
|---|---|---|
| `MAX_LEAD_PCT` | `0.04` | core filter; lower → higher win‑rate, fewer trades |
| `MIN_CROSSINGS` | `0` | set `6` for high‑ROI / low‑volume "choppy only" mode |
| `ENTRY_HI_S` / `ENTRY_LO_S` | `12` / `2` | the entry window before close |
| `MAX_ENTRY_PRICE` | `0.03` | max price (cents) to pay for the loser |
| `STAKE_USD` | `8.35` | $ per window |
| `COINS` | `btc,sol,xrp,eth` | universe (BTC/XRP/SOL carried the edge; ETH lagged) |

Re‑tune against history with `python script/gridsearch.py`.

---

## Live odds (real-time websocket)

The bot streams **actual Polymarket order-book odds** from the CLOB market channel
(`bot/clob_ws.py`):

```
wss://ws-subscriptions-clob.polymarket.com/ws/market
```

It subscribes a window's two tokens ~`WS_SUBSCRIBE_LEAD_S` seconds before close and
maintains a live `best_bid / best_ask / last` per token from the `book`, `price_change`,
`best_bid_ask` and `last_trade_price` events. **Paper fills use the live streamed best
ask** (`engine.live_ask()` → `source='ws'`); if a quote is missing/stale it falls back to
the official `py-clob-client` REST book, then to plain REST. Set `USE_WEBSOCKET=false` to
run REST-only.

## How settlement works (important)

Win/loss is the **single source of truth** and comes from Polymarket only. Settlement runs
in a **dedicated thread** (`Engine._settle_loop`) so it can never be starved or blocked by the
trading loop or the dashboard. `engine.resolve()` tries multiple sources and takes the first
definitive answer — each **bounded** so a slow/hanging endpoint can't freeze anything:

1. CLOB `GET /markets/{id}` winner **+** Gamma `outcomePrices` by condition id (REST, timed out), **then**
2. Gamma `/events?slug=` (same reliable path discovery uses), **then**
3. Official `py-clob-client` `get_market()` — run under a hard timeout guard.

A position pays `shares × $1` iff its outcome equals the resolved winner, else `$0`;
P&L = payout − cost. Resolution typically appears ~3–6 min after a window closes; until then the
dashboard shows the position with an **`⏳ AWAITING`** timer (red after 2 min). The Binance feed
is *only* used to choose entries — **never** to score wins.

> Robustness: per-coin and per-position errors are isolated, settlement is idempotent, and every
> external call has a timeout — so one bad market or a hung SDK call can't leave positions stuck open.

> The official Polymarket Python client is **`py-clob-client`** (v0.34.6, in
> `requirements.txt`). There is no separate "v2" pip package — it's a REST + order-signing
> SDK and does **not** include a websocket, which is why live odds use `bot/clob_ws.py`.

## Limitations / honesty

- Paper fills assume you get the live best ask with no slippage and full size — real
  fills depend on 1–3¢ book depth you can't always get.
- Binance is a proxy for the Chainlink resolution source (~85% directional match on
  thin moves); it only affects entry timing, not settlement.
- One‑fill‑per‑window by default (the real bot ladders; raise `MAX_FILLS_PER_WINDOW`).
- This is a research tool, **not financial advice**. Trade live at your own risk.
