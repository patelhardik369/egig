"""Core data structures. Outcome index convention (from Polymarket): 0 = Up, 1 = Down."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, Optional

UP, DOWN = 0, 1


@dataclass
class Window:
    """One 5-minute Up/Down market we are tracking live."""
    coin: str
    epoch: int                       # window start T (unix, multiple of 300)
    condition_id: str
    token_ids: Dict[int, str]        # outcome_index -> CLOB token id
    outcomes: Dict[int, str]         # outcome_index -> "Up"/"Down"
    open_price: Optional[float] = None
    open_ts: Optional[float] = None
    last_price: Optional[float] = None
    crossings: int = 0               # times the underlying crossed the open level (choppiness)
    chop_locked: bool = False        # True once an authoritative (1s-kline) crossing count is set
    n_samples: int = 0
    entered: bool = False
    settled: bool = False

    @property
    def start(self) -> int: return self.epoch
    @property
    def end(self) -> int: return self.epoch + 300

    def open_valid(self, tol_s: int) -> bool:
        """True only if we observed the window from ~its start (so `open_price` is meaningful)."""
        return self.open_ts is not None and abs(self.open_ts - self.epoch) <= tol_s

    def lead_pct(self) -> Optional[float]:
        if not self.open_price or self.last_price is None:
            return None
        return (self.last_price - self.open_price) / self.open_price * 100.0

    def update(self, price: float, ts: float) -> None:
        # live (jittery) estimate for the dashboard; replaced by an authoritative 1s-kline
        # count at arm time, after which we stop incrementing (chop_locked).
        if self.open_price and self.last_price is not None and not self.chop_locked:
            # sign change of (price - open) == a crossing of the open level
            if (self.last_price - self.open_price) * (price - self.open_price) < 0:
                self.crossings += 1
        self.last_price = price
        self.n_samples += 1


@dataclass
class Signal:
    losing_index: int                # the outcome the bot buys (the side currently behind)
    lead_pct: float
    crossings: int


@dataclass
class Position:
    id: str
    coin: str
    epoch: int
    condition_id: str
    token_id: str
    outcome_index: int
    outcome_name: str
    entry_price: float
    shares: float
    cost: float
    ts_entry: float
    lead_at_entry: float
    sec_left_at_entry: float = 0.0   # seconds left in the window at the moment of fill
    status: str = "open"             # open | settled
    resolved_index: Optional[int] = None
    payout: float = 0.0
    pnl: float = 0.0
    ts_settled: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)
