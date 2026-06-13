"""The selection brain, as a pure function.

Replicates the egig edge found in research (see script/select_brain.py, script/gridsearch.py):
near the buzzer, bet the *losing* side ONLY when the move never committed, i.e. the
underlying is still hugging the window's open price (|lead| small) and optionally choppy.
The cheapness check (loser actually offered <= 3c) is applied by the engine via the book.

Returns a Signal (which side to buy) or None to skip the window.
"""
from __future__ import annotations

from typing import Optional

from .config import Settings
from .models import Window, Signal, UP, DOWN


def evaluate(win: Window, now: float, s: Settings) -> Optional[Signal]:
    # 1) timing: only the final seconds, and only if we trust this window's open
    sec_left = win.end - now
    if not (s.entry_lo_s <= sec_left <= s.entry_hi_s):
        return None
    if win.entered or not win.open_valid(s.open_snap_tol_s):
        return None

    lead = win.lead_pct()
    if lead is None:
        return None
    mag = abs(lead)

    # 2) the core filter: the move must be SMALL (still in play) but non-zero (a loser exists)
    if mag > s.max_lead_pct or mag < s.min_lead_pct:
        return None

    # 3) optional choppiness filter (higher win-rate / lower volume)
    if win.crossings < s.min_crossings:
        return None

    # buy the side currently behind:
    #   lead > 0  -> underlying above open -> Up winning -> buy DOWN
    #   lead < 0  -> underlying below open -> Down winning -> buy UP
    losing_index = DOWN if lead > 0 else UP
    return Signal(losing_index=losing_index, lead_pct=lead, crossings=win.crossings)
