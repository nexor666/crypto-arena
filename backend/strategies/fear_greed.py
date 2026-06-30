"""Fear & Greed — an "active league" foil (plan strategy #5).

Buys into extreme fear and trims into extreme greed, using the market-wide
alternative.me Fear & Greed index (0 = extreme fear, 100 = extreme greed). It is
included mainly to test whether leaving the buy-the-bear/sell-the-bull lane ever
actually beats Buy & Hold *after fees + tax* — its extra trades are exactly where
those costs bite.

Signal is consulted point-in-time via ``history`` (today's value only). Before the
index exists (pre-Feb-2018) or on a missing day the value is None → hold.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class FearGreed(Strategy):
    name = "fear_greed"
    description = "Buy extreme fear, trim extreme greed (Fear & Greed index)."
    universe = "single"
    thesis = (
        "A contrarian sentiment play: buy when the crowd is fearful, trim when it's "
        "greedy. Included mainly as a foil — a test of whether trading on sentiment "
        "actually beats simply holding once the extra fees and tax are paid."
    )
    rule = (
        "When the Fear & Greed index is ≤ 25 (extreme fear), buy 50% of available cash. "
        "When it's ≥ 75 (extreme greed), sell 50% of the holding. All thresholds tunable."
    )
    triggering = "level"  # acts every day the index is in the buy/sell zone
    reads = ("Fear & Greed index",)

    param_schema = {
        "buy_below": ParamSpec(
            min=5, max=50, step=1, default=25, type="int",
            label="Buy when Fear & Greed is at/below this (extreme fear)",
        ),
        "sell_above": ParamSpec(
            min=50, max=95, step=1, default=75, type="int",
            label="Trim when Fear & Greed is at/above this (extreme greed)",
        ),
        "buy_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of available cash to deploy on a fear signal",
        ),
        "sell_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of the holding to sell on a greed signal",
        ),
    }

    def decide(self, date, history, params, portfolio):
        fng = history.get("fear_greed")
        if fng is None:
            return [], None  # index not available yet -> hold

        asset = history.asset
        if fng <= params["buy_below"] and portfolio.cash > 0:
            return (
                [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                f"extreme fear ({fng:.0f} ≤ {params['buy_below']:.0f}) — accumulate",
            )
        if fng >= params["sell_above"] and portfolio.quantity(asset) > 0:
            return (
                [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                f"extreme greed ({fng:.0f} ≥ {params['sell_above']:.0f}) — trim",
            )
        return [], None
