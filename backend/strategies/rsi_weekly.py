"""Weekly RSI — an "active league" foil (plan strategy #8).

Momentum mean-reversion on a long timeframe: accumulate when the **weekly** RSI(14)
is oversold and trim when it's overbought. Using the weekly RSI (rather than daily)
keeps this in the slow, cycle-oriented lane the project cares about — it fires a
handful of times per cycle near major lows/highs, not on daily noise.

``rsi_14w`` is precomputed from completed weeks and broadcast to each day with no
look-ahead (it only ever reflects the last *closed* week). Before warmup it is
None → hold.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class RsiWeekly(Strategy):
    name = "rsi_weekly"
    description = "Accumulate when weekly RSI(14) is oversold, trim when overbought."
    universe = "single"
    thesis = (
        "Momentum mean-reversion on a slow timeframe: accumulate when the weekly RSI is "
        "oversold, trim when it is overbought. Using the weekly (not daily) RSI keeps it "
        "in the cycle lane rather than reacting to daily noise."
    )
    rule = (
        "Buy 50% of cash when the weekly RSI(14) is ≤ 30 (oversold); sell 50% of the "
        "holding when it is ≥ 70 (overbought). Thresholds and sizes tunable."
    )
    triggering = "level"  # acts every day the weekly RSI is oversold/overbought
    reads = ("weekly RSI(14)",)

    param_schema = {
        "buy_below": ParamSpec(
            min=15, max=45, step=1, default=30, type="int",
            label="Buy when weekly RSI is at/below this (oversold)",
        ),
        "sell_above": ParamSpec(
            min=55, max=85, step=1, default=70, type="int",
            label="Trim when weekly RSI is at/above this (overbought)",
        ),
        "buy_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of available cash to deploy on an oversold signal",
        ),
        "sell_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of the holding to sell on an overbought signal",
        ),
    }

    def decide(self, date, history, params, portfolio):
        rsi = history.get("rsi_14w")
        if rsi is None:
            return [], None  # weekly RSI not warmed up yet -> hold

        asset = history.asset
        if rsi <= params["buy_below"] and portfolio.cash > 0:
            return (
                [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                f"weekly RSI {rsi:.0f} (≤ {params['buy_below']:.0f}) — oversold, accumulate",
            )
        if rsi >= params["sell_above"] and portfolio.quantity(asset) > 0:
            return (
                [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                f"weekly RSI {rsi:.0f} (≥ {params['sell_above']:.0f}) — overbought, trim",
            )
        return [], None
