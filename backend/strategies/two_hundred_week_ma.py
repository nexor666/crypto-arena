"""200-Week MA — a core "cycle league" candidate (plan strategy #2).

The 200-week moving average has historically tracked the floor of Bitcoin's
bear-market bottoms: price near/below it has marked generational accumulation
zones, while price stretched far above it (multiples of the 200W MA) has marked
cycle tops. So: accumulate when price is at/below the 200W MA, distribute when
price runs to a high multiple of it. Low-turnover by nature — a few trades per
cycle — which is exactly the lane the user invests in.

Signal is the ratio ``price / ma_200w`` (point-in-time, no look-ahead). Before the
200W MA has enough warmup (early history) ``ma_200w`` is None → hold.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class TwoHundredWeekMA(Strategy):
    name = "200w_ma"
    description = "Accumulate at/below the 200-week MA, distribute far above it."
    universe = "single"
    thesis = (
        "Treats the 200-week moving average as Bitcoin's long-term floor: historically "
        "price near or below it has marked generational bottoms, and price stretched to "
        "a high multiple of it has marked tops. A low-turnover value approach."
    )
    rule = (
        "When price is ≤ 1.0× the 200-week MA, buy 50% of cash. When price is ≥ 3.0× the "
        "200-week MA, sell 50% of the holding. Thresholds and sizes tunable."
    )
    triggering = "level"  # acts every day price is in the buy/sell zone
    reads = ("price", "200-week moving average")

    param_schema = {
        "buy_below_ratio": ParamSpec(
            min=0.7, max=1.5, step=0.05, default=1.0, type="float",
            label="Buy when price / 200W-MA is at/below this (near the floor)",
        ),
        "sell_above_ratio": ParamSpec(
            min=2.0, max=6.0, step=0.1, default=3.0, type="float",
            label="Distribute when price / 200W-MA is at/above this (stretched)",
        ),
        "buy_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of available cash to deploy on a buy signal",
        ),
        "sell_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of the holding to sell on a distribute signal",
        ),
    }

    def decide(self, date, history, params, portfolio):
        ma = history.get("ma_200w")
        price = history.price
        if ma is None or not ma or price is None:
            return [], None  # 200W MA not warmed up yet -> hold

        ratio = price / ma
        asset = history.asset
        if ratio <= params["buy_below_ratio"] and portfolio.cash > 0:
            return (
                [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                f"price {ratio:.2f}× 200W-MA (≤ {params['buy_below_ratio']:.2f}) — accumulate",
            )
        if ratio >= params["sell_above_ratio"] and portfolio.quantity(asset) > 0:
            return (
                [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                f"price {ratio:.2f}× 200W-MA (≥ {params['sell_above_ratio']:.2f}) — distribute",
            )
        return [], None
