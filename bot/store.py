"""Durable storage for paper state: atomic JSON for positions, append-only CSV logs.

Everything lives under data/ — this folder holds bot runtime data ONLY.
"""
from __future__ import annotations

import csv
import json
import os
import tempfile
from pathlib import Path
from typing import Iterable, List

from .models import Position


class Store:
    def __init__(self, data_dir: Path):
        self.dir = Path(data_dir)
        self.positions_path = self.dir / "positions.json"
        self.trades_path = self.dir / "trades.csv"
        self.equity_path = self.dir / "equity.csv"
        self.dir.mkdir(parents=True, exist_ok=True)

    def reset(self) -> int:
        """Wipe paper state (positions / trades / equity). Returns files removed."""
        removed = 0
        for p in (self.positions_path, self.trades_path, self.equity_path):
            if p.exists():
                p.unlink()
                removed += 1
        return removed

    # ---- atomic json ----
    def _atomic_write(self, path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=str(self.dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, path)            # atomic on the same filesystem
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def load_positions(self) -> List[dict]:
        if not self.positions_path.exists():
            return []
        try:
            return json.loads(self.positions_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def save_positions(self, positions: Iterable[Position]) -> None:
        self._atomic_write(self.positions_path,
                           json.dumps([p.to_dict() for p in positions], indent=2))

    # ---- append-only logs ----
    def append_trade(self, p: Position, event: str) -> None:
        new = not self.trades_path.exists()
        with open(self.trades_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["event", "ts", "id", "coin", "epoch", "outcome", "entry_price",
                            "shares", "cost", "lead_at_entry", "resolved_index", "payout", "pnl"])
            w.writerow([event, f"{p.ts_settled or p.ts_entry:.0f}", p.id, p.coin, p.epoch,
                        p.outcome_name, f"{p.entry_price:.4f}", f"{p.shares:.2f}", f"{p.cost:.2f}",
                        f"{p.lead_at_entry:.4f}", p.resolved_index, f"{p.payout:.2f}", f"{p.pnl:.2f}"])

    def append_equity(self, snapshot: dict) -> None:
        new = not self.equity_path.exists()
        with open(self.equity_path, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(list(snapshot.keys()))
            w.writerow(list(snapshot.values()))
