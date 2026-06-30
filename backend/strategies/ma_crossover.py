"""MA Crossover (50/200) — an "active league" foil (plan strategy #7).

The classic golden-cross / death-cross trend filter: hold the asset while the
50-day MA is above the 200-day MA (golden cross → uptrend regime), sit in cash
while it's below (death cross → downtrend regime). A binary, fully-invested-or-flat
regime rule rather than a partial-sizing value strategy.

Both MAs are precomputed (``ma_50d``, ``ma_200d``) with no look-ahead. The 50/200
windows are fixed (the indicator table only carries those), so the one tunable
knob is ``confirm_days``: require the regime to persist this many consecutive days
before acting, which damps whipsaw around the crossover. Until both MAs are warmed
up the signal is None → hold. A foil: trend-following crosses late and whipsaws in
chop, so it's a good test of whether trend-timing beats holding after fees + tax.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class MaCrossover(Strategy):
    name = "ma_cross"
    description = "Long while 50D-MA > 200D-MA (golden cross), cash while below."
    universe = "single"
    thesis = (
        "The classic trend filter: ride the asset while its 50-day MA is above its "
        "200-day MA (a golden cross), and step aside to cash when it falls below (a death "
        "cross). A foil for whether trend-timing beats simply holding."
    )
    rule = (
        "Go 100% long on a golden cross (50-day MA > 200-day MA); go 100% to cash on a "
        "death cross. An optional confirm-days delay damps whipsaw. The 50/200 windows "
        "are fixed."
    )
    triggering = "edge"  # acts only when the crossover flips — fully long or fully cash
    reads = ("50-day moving average", "200-day moving average")

    param_schema = {
        "confirm_days": ParamSpec(
            min=0, max=30, step=1, default=0, type="int",
            label="Require the regime to hold this many days before acting (anti-whipsaw)",
        ),
    }

    def decide(self, date, history, params, portfolio):
        ma_fast = history.get("ma_50d")
        ma_slow = history.get("ma_200d")
        if ma_fast is None or ma_slow is None:
            return [], None  # MAs not warmed up yet -> hold

        confirm = int(params["confirm_days"])
        bullish = ma_fast > ma_slow

        # Require the regime to have held for ``confirm`` prior days too.
        if confirm > 0:
            for back in range(1, confirm + 1):
                f = history.prev("ma_50d", back)
                s = history.prev("ma_200d", back)
                if f is None or s is None or (f > s) != bullish:
                    return [], None  # regime not yet confirmed -> hold

        asset = history.asset
        held = portfolio.quantity(asset)
        if bullish and portfolio.cash > 0:
            return [Order(asset, BUY, fraction=1.0)], "50D-MA > 200D-MA (golden cross) — go long"
        if not bullish and held > 0:
            return [Order(asset, SELL, fraction=1.0)], "50D-MA < 200D-MA (death cross) — go to cash"
        return [], None
