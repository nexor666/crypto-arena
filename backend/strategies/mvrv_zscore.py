"""MVRV Z-Score — a core "cycle league" candidate, BTC-only (plan strategy #4).

The MVRV Z-Score compares market value to realized value (aggregate on-chain cost
basis), normalized. A low Z (< 0) means price is below the network's average cost
basis — historically deep-value accumulation; a high Z (> 7) means price is far
above it — historically euphoric cycle tops. So: accumulate when Z < 0, distribute
when Z > 7.

**BTC-only:** there is no free per-altcoin on-chain data, so ``mvrv_zscore`` is
merged onto BTC days only (it is None for ETH/BNB and on any day the series doesn't
cover). A missing value → hold. (If the on-chain table hasn't been backfilled yet,
this strategy simply never trades and sits in cash — an uninformative but honest
leaderboard row, not a crash.)

Honest caveat (plan): on-chain series are revised retroactively, so historical MVRV
may not equal what was knowable in real time — a subtle look-ahead the price golden
test can't catch. Treat MVRV results with extra skepticism.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class MvrvZScore(Strategy):
    name = "mvrv_z"
    description = "Accumulate when MVRV Z-Score < 0, distribute when > 7 (BTC-only)."
    universe = "single"
    thesis = (
        "An on-chain valuation gauge (Bitcoin only): it compares the market price to the "
        "network's aggregate cost basis. Deeply negative means price is below what the "
        "average holder paid — historically capitulation value; very high means euphoria."
    )
    rule = (
        "Buy 50% of cash when the MVRV Z-Score is ≤ 0; sell 50% of the holding when it is "
        "≥ 7. BTC only — it sits in cash on other coins, or if the on-chain series isn't "
        "loaded. Thresholds tunable."
    )
    triggering = "level"  # acts every day the Z-Score is in the buy/sell zone
    reads = ("MVRV Z-Score (on-chain)",)

    param_schema = {
        "buy_below": ParamSpec(
            min=-1.0, max=2.0, step=0.1, default=0.0, type="float",
            label="Buy when MVRV Z-Score is at/below this (deep value)",
        ),
        "sell_above": ParamSpec(
            min=4.0, max=10.0, step=0.25, default=7.0, type="float",
            label="Sell when MVRV Z-Score is at/above this (euphoria)",
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
        z = history.get("mvrv_zscore")
        if z is None:
            return [], None  # not BTC, or series doesn't cover this day -> hold

        asset = history.asset
        if z <= params["buy_below"] and portfolio.cash > 0:
            return (
                [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                f"MVRV Z {z:.2f} (≤ {params['buy_below']:.2f}) — accumulate",
            )
        if z >= params["sell_above"] and portfolio.quantity(asset) > 0:
            return (
                [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                f"MVRV Z {z:.2f} (≥ {params['sell_above']:.2f}) — distribute",
            )
        return [], None
