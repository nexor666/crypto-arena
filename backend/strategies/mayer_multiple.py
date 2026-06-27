"""Mayer Multiple — a core "cycle league" candidate (plan strategy #3).

The Mayer Multiple is ``price ÷ 200-day MA``. Historically a Mayer below ~1.0
(price under its 200D MA) has clustered around bear-market value zones, while a
Mayer above ~2.4 has clustered around frothy, overextended tops. So: buy when
Mayer < 1.0, sell when Mayer > 2.4. A classic low-turnover cycle heuristic.

``mayer`` is precomputed in the indicators table (no look-ahead — it uses only the
trailing 200-day window). Before the 200D MA warms up it is None → hold.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class MayerMultiple(Strategy):
    name = "mayer"
    description = "Buy when Mayer Multiple (price/200D-MA) < 1.0, sell when > 2.4."
    universe = "single"

    param_schema = {
        "buy_below": ParamSpec(
            min=0.6, max=1.4, step=0.05, default=1.0, type="float",
            label="Buy when Mayer Multiple is at/below this (undervalued)",
        ),
        "sell_above": ParamSpec(
            min=1.6, max=4.0, step=0.1, default=2.4, type="float",
            label="Sell when Mayer Multiple is at/above this (overextended)",
        ),
        "buy_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of available cash to deploy on a buy signal",
        ),
        "sell_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of the holding to sell on a sell signal",
        ),
    }

    def decide(self, date, history, params, portfolio):
        mayer = history.get("mayer")
        if mayer is None:
            return [], None  # 200D MA not warmed up yet -> hold

        asset = history.asset
        if mayer <= params["buy_below"] and portfolio.cash > 0:
            return (
                [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                f"Mayer {mayer:.2f} (≤ {params['buy_below']:.2f}) — accumulate",
            )
        if mayer >= params["sell_above"] and portfolio.quantity(asset) > 0:
            return (
                [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                f"Mayer {mayer:.2f} (≥ {params['sell_above']:.2f}) — distribute",
            )
        return [], None
