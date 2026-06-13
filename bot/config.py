"""Central configuration. Reads .env (via python-dotenv) with safe, paper-only defaults.

Every strategy knob is overridable from the environment so you can tune the bot the
same way we grid-searched the research (see script/gridsearch.py)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:  # python-dotenv optional; env still works
    pass

ROOT = Path(__file__).resolve().parent.parent

# coin -> Binance spot symbol used for the price-path / |lead| decision feed
SYMBOL = {"btc": "BTCUSDT", "eth": "ETHUSDT", "sol": "SOLUSDT", "xrp": "XRPUSDT"}
# Polymarket market-slug prefix per coin: "{prefix}-5m-{epoch}"
SLUG_PREFIX = {"btc": "btc-updown", "eth": "eth-updown", "sol": "sol-updown", "xrp": "xrp-updown"}


def _f(name: str, default: float) -> float:
    try: return float(os.getenv(name, default))
    except (TypeError, ValueError): return float(default)

def _i(name: str, default: int) -> int:
    try: return int(float(os.getenv(name, default)))
    except (TypeError, ValueError): return int(default)

def _b(name: str, default: bool) -> bool:
    return str(os.getenv(name, str(default))).strip().lower() in ("1", "true", "yes", "on")

def _list(name: str, default: List[str]) -> List[str]:
    raw = os.getenv(name)
    return [x.strip().lower() for x in raw.split(",") if x.strip()] if raw else list(default)


@dataclass
class Settings:
    # --- mode / universe ---
    mode: str = os.getenv("MODE", "paper")                 # only "paper" is implemented
    coins: List[str] = field(default_factory=lambda: _list("COINS", ["btc", "sol", "xrp", "eth"]))

    # --- strategy (defaults = the research-tuned operating point) ---
    entry_lo_s: int = _i("ENTRY_LO_S", 3)                  # never fill with fewer than N secs left (latency-safe)
    entry_hi_s: int = _i("ENTRY_HI_S", 12)                 # begin acting at N secs before close
    max_lead_pct: float = _f("MAX_LEAD_PCT", 0.04)         # bet only if |lead| < this (% of open) -> still in play
    min_lead_pct: float = _f("MIN_LEAD_PCT", 0.003)        # ...and a real loser exists
    min_crossings: int = _i("MIN_CROSSINGS", 0)            # choppiness filter (>=6 = high ROI, less volume)
    max_entry_price: float = _f("MAX_ENTRY_PRICE", 0.03)   # only fill the loser at <= 3c
    min_entry_price: float = _f("MIN_ENTRY_PRICE", 0.005)  # ignore dust / 0-priced books
    stake_usd: float = _f("STAKE_USD", 8.35)               # $ per window
    max_fills_per_window: int = _i("MAX_FILLS_PER_WINDOW", 1)

    # --- engine / infra ---
    window_seconds: int = 300
    sample_interval_s: float = _f("SAMPLE_INTERVAL_S", 1.0)
    settle_buffer_s: int = _i("SETTLE_BUFFER_S", 20)       # wait this long after close before polling resolution
    settle_every_s: float = _f("SETTLE_EVERY_S", 5.0)
    open_snap_tol_s: int = _i("OPEN_SNAP_TOL_S", 5)        # trust a window's open only if first seen within N s of start
    prefetch_next: bool = _b("PREFETCH_NEXT", True)
    starting_cash: float = _f("STARTING_CASH", 1000.0)

    # --- live odds (websocket) ---
    use_websocket: bool = _b("USE_WEBSOCKET", True)        # stream real-time CLOB odds
    ws_subscribe_lead_s: int = _i("WS_SUBSCRIBE_LEAD_S", 120)  # subscribe a window this long before close
    ws_stale_s: float = _f("WS_STALE_S", 12.0)            # treat a streamed quote older than this as stale
    use_official_client: bool = _b("USE_OFFICIAL_CLIENT", True)  # py-clob-client for resolution/REST fallback
    chain_id: int = _i("CHAIN_ID", 137)

    # --- hosts / http ---
    binance_host: str = os.getenv("BINANCE_HOST", "https://data-api.binance.vision")
    gamma_host: str = os.getenv("GAMMA_HOST", "https://gamma-api.polymarket.com")
    clob_host: str = os.getenv("CLOB_HOST", "https://clob.polymarket.com")
    clob_ws_host: str = os.getenv("CLOB_WS_HOST", "wss://ws-subscriptions-clob.polymarket.com/ws/market")
    http_timeout_s: float = _f("HTTP_TIMEOUT_S", 8.0)

    # --- paths / logging ---
    data_dir: Path = Path(os.getenv("DATA_DIR", ROOT / "data"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def __post_init__(self) -> None:
        self.coins = [c for c in self.coins if c in SYMBOL] or ["btc"]
        self.data_dir = Path(self.data_dir)
        (self.data_dir / "logs").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "state").mkdir(parents=True, exist_ok=True)


settings = Settings()
