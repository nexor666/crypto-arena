"""Halving Cycle Timing — an "active league" foil (plan strategy #6).

Bitcoin's ~4-year supply halving has historically anchored the cycle: price tends
to bottom in the long bear *before* a halving and peak roughly a year *after* it.
This strategy keys purely off **days since the most recent halving** (a known,
scheduled clock — no look-ahead; everyone knew these dates years in advance, see
``config.HALVING_DATES``):

  * **Accumulate window** — late in the cycle, far from the last halving (the bear
    bottom that precedes the next halving): ``buy_after_min ≤ days_since ≤ buy_after_max``.
  * **Distribute window** — the post-halving bull top, roughly 1–1.5 years out:
    ``sell_after_min ≤ days_since ≤ sell_after_max``.

Defaults follow the historical rhythm (cycle ≈ 1458 days): sell ~400–600 days
after a halving (the top), accumulate ~950 days+ after (the late-bear bottom before
the next halving). Tunable. Included as a foil — it trades more than the pure
value strategies, so it's where fees + tax start to bite.
"""

from __future__ import annotations

from datetime import date as _date

from backend.config import HALVING_DATES
from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy

_HALVINGS = sorted(_date.fromisoformat(d) for d in HALVING_DATES)


def _days_since_last_halving(today: str) -> int | None:
    """Calendar days since the most recent halving on/before ``today`` (or None)."""
    d = _date.fromisoformat(today)
    last = None
    for h in _HALVINGS:
        if h <= d:
            last = h
        else:
            break
    if last is None:
        return None  # before the first known halving -> no cycle clock yet
    return (d - last).days


class HalvingCycle(Strategy):
    name = "halving"
    description = "Time accumulate/distribute windows off days-since-halving."
    universe = "single"

    param_schema = {
        "sell_after_min": ParamSpec(
            min=300, max=700, step=10, default=400, type="int",
            label="Start distributing this many days after a halving (cycle top)",
        ),
        "sell_after_max": ParamSpec(
            min=500, max=900, step=10, default=600, type="int",
            label="Stop distributing this many days after a halving",
        ),
        "buy_after_min": ParamSpec(
            min=800, max=1200, step=10, default=950, type="int",
            label="Start accumulating this many days after a halving (late bear)",
        ),
        "buy_after_max": ParamSpec(
            min=1200, max=1600, step=10, default=1500, type="int",
            label="Stop accumulating this many days after a halving",
        ),
        "buy_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of available cash to deploy in the accumulate window",
        ),
        "sell_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of the holding to sell in the distribute window",
        ),
    }

    def decide(self, date, history, params, portfolio):
        days = _days_since_last_halving(history.today)
        if days is None:
            return [], None

        asset = history.asset
        if params["buy_after_min"] <= days <= params["buy_after_max"] and portfolio.cash > 0:
            return (
                [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                f"{days}d since halving (accumulate window) — buy",
            )
        if params["sell_after_min"] <= days <= params["sell_after_max"] and portfolio.quantity(asset) > 0:
            return (
                [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                f"{days}d since halving (distribute window) — sell",
            )
        return [], None
