"""Buy & Hold — the benchmark every other strategy is measured against.

Deploys all available cash into the asset as early as possible and never sells, so
its pre-tax return equals the asset's raw price change over the window (minus fees)
— the Stage-2 golden check that proves the engine has no look-ahead and no fee bug.

The plan's "spread the lump sum over N weeks" variant is exposed via
``spread_weeks``: 0 (default) buys everything on day one; N>0 deploys an equal
slice once every 7 days for the first N weeks (entry-date-luck smoothing).
"""

from __future__ import annotations

from typing import Any

from backend.strategies.base import BUY, Order, ParamSpec, Strategy


class BuyHold(Strategy):
    name = "buy_hold"
    description = "Deploy all capital into the asset and hold (benchmark)."
    universe = "single"

    param_schema = {
        "spread_weeks": ParamSpec(
            min=0, max=52, step=1, default=0, type="int",
            label="Weeks to spread the initial buy over (0 = all at once)",
        ),
    }

    def decide(self, date, history, params, portfolio):
        asset = history.asset
        if portfolio.cash <= 0:
            return [], None

        spread_weeks = int(params["spread_weeks"])
        if spread_weeks <= 0:
            # Lump: spend everything the first day cash is available.
            return [Order(asset, BUY, fraction=1.0)], "deploy all cash (buy & hold)"

        # Phased: one slice every 7 days for the first ``spread_weeks`` weeks.
        day_index = history.i
        if day_index % 7 != 0:
            return [], None
        slice_no = day_index // 7
        if slice_no >= spread_weeks:
            return [], None
        # Spend an equal share of the ORIGINAL plan; using a fraction of remaining
        # cash that grows so the final slice clears the balance.
        remaining_slices = spread_weeks - slice_no
        frac = 1.0 / remaining_slices
        return [Order(asset, BUY, fraction=frac)], f"phased entry slice {slice_no + 1}/{spread_weeks}"
