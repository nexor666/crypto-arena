"""History — the backward-looking market view handed to a strategy each day.

This is where **no look-ahead** is enforced by construction (plan's single most
important correctness property). The engine advances :attr:`i` to the simulated
"today" before calling ``strategy.decide``; every accessor here is clamped to
``i``, so a strategy *cannot* read a future bar — the data past today is simply
unreachable through the public API.

One History wraps the run's daily calendar for a single asset (the default
single-coin mode), with the market-wide Fear & Greed and BTC on-chain series
merged onto each day. Each record is a flat dict, e.g.::

    {"date": "2021-01-01", "open":..., "high":..., "low":..., "close":...,
     "volume":..., "ma_50d":..., "ma_200d":..., "ma_200w":..., "mayer":...,
     "rsi_14":..., "rsi_14w":..., "fear_greed":..., "mvrv_zscore":...}

Missing fields are absent/None (warmup MAs, days before Fear & Greed started,
etc.) — strategies treat a missing value as "signal not available yet" and hold.
"""

from __future__ import annotations

from typing import Any


class History:
    """Bounded view over one asset's per-day records (clamped to ``i`` = today)."""

    def __init__(self, asset: str, records: list[dict[str, Any]]) -> None:
        self.asset = asset
        self._records = records          # full window, date-sorted (engine-owned)
        self.i = 0                        # index of the simulated current day

    # -- positioning (engine-controlled) -----------------------------------
    def __len__(self) -> int:
        return len(self._records)

    @property
    def dates(self) -> list[str]:
        return [r["date"] for r in self._records]

    # -- today --------------------------------------------------------------
    @property
    def today(self) -> str:
        return self._records[self.i]["date"]

    @property
    def price(self) -> float | None:
        """Today's close (the execution price)."""
        return self._records[self.i].get("close")

    def get(self, field: str, default: Any = None) -> Any:
        """Today's value of ``field`` (e.g. 'mayer', 'fear_greed'), or ``default``."""
        val = self._records[self.i].get(field, default)
        return default if val is None else val

    # -- lookback (still clamped to today) ----------------------------------
    def prev(self, field: str = "close", back: int = 1) -> Any:
        """Value of ``field`` ``back`` days ago, or None if before the window."""
        j = self.i - back
        if j < 0:
            return None
        return self._records[j].get(field)

    def window(self, field: str, n: int | None = None) -> list[Any]:
        """The last ``n`` values of ``field`` up to and including today.

        With ``n=None`` returns the whole history-so-far. Future bars are never
        included (slice ends at ``i``).
        """
        lo = 0 if n is None else max(0, self.i - n + 1)
        return [r.get(field) for r in self._records[lo : self.i + 1]]

    @property
    def records_so_far(self) -> list[dict[str, Any]]:
        """All records up to and including today (a fresh slice — read-only use)."""
        return self._records[: self.i + 1]
