"""Periodic Rebalance — constant-mix between one coin and cash (plan strategy #9).

Holds a target coin/cash mix (e.g. 60% coin / 40% cash) and periodically trims
winners / tops up losers back to that target. This is a volatility-harvesting
"constant-mix" rule: it mechanically sells into strength and buys into weakness,
the opposite of trend-following. Rebalancing only every ``rebalance_days`` (and
only when the drift exceeds ``band``) keeps turnover — and thus fees + tax — in
check.

**Scope note (deliberate):** the plan's Periodic Rebalance ultimately envisions a
true *rotation* across BTC/ETH/BNB+cash, but the Stage-3 engine + ``History`` are
single-coin (one asset ↔ cash per run). So this is implemented as a single-coin
constant-mix (coin ↔ cash), declared ``universe="single"``. The multi-coin rotation
version drops in unchanged against this same contract once the engine carries
multiple assets per run (then it becomes ``universe="rotation"``). Implementing it
this way now keeps "race ALL strategies" complete without faking a capability the
engine doesn't have yet.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy


class Rebalance(Strategy):
    name = "rebalance"
    description = "Hold a target coin/cash mix; rebalance to it periodically."
    universe = "single"

    param_schema = {
        "target_weight": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.6, type="float",
            label="Target fraction of the portfolio held in the coin (rest is cash)",
        ),
        "rebalance_days": ParamSpec(
            min=7, max=180, step=1, default=30, type="int",
            label="Rebalance every N days",
        ),
        "band": ParamSpec(
            min=0.0, max=0.25, step=0.01, default=0.05, type="float",
            label="Only rebalance when the coin weight has drifted beyond this band",
        ),
    }

    def decide(self, date, history, params, portfolio):
        if history.i % int(params["rebalance_days"]) != 0:
            return [], None  # not a rebalance day

        price = history.price
        if price is None or price <= 0:
            return [], None

        asset = history.asset
        holdings_value = portfolio.quantity(asset) * price
        total = portfolio.cash + holdings_value
        if total <= 0:
            return [], None

        target_value = float(params["target_weight"]) * total
        delta = target_value - holdings_value          # +ve: buy more; -ve: trim
        if abs(delta) / total < float(params["band"]):
            return [], None  # within tolerance band -> leave it

        if delta > 0 and portfolio.cash > 0:
            return (
                [Order(asset, BUY, quote=min(delta, portfolio.cash))],
                f"rebalance up to {params['target_weight']:.0%} coin",
            )
        if delta < 0:
            return (
                [Order(asset, SELL, quote=-delta)],
                f"rebalance down to {params['target_weight']:.0%} coin",
            )
        return [], None
