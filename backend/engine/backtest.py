"""Backtest — the daily loop that races a strategy through history.

Flow per day (strictly forward, no look-ahead — :class:`History` enforces it):
    1. advance the simulated "today" pointer,
    2. inject any scheduled capital (capital-model hook),
    3. ask the strategy to ``decide`` using ONLY data up to today,
    4. execute the returned orders at today's close,
    5. snapshot the portfolio (pre-tax value, cash, position, any trade).

Tax is applied afterwards as an overlay (``tax.py`` consumes the realized-gain
events), and metrics are computed from the snapshot stream (``metrics.py``). The
engine itself stays pre-tax and lens-agnostic (plan modularity principle 6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.data.store import Store
from backend.engine import metrics as metrics_mod
from backend.engine import tax as tax_mod
from backend.engine.capital import CapitalModel, LumpSum
from backend.engine.history import History
from backend.engine.portfolio import Portfolio
from backend.strategies.base import Strategy

# Result JSON schema version (plan: versioned result schema so saved runs survive
# format evolution). Bump when the shape of BacktestResult.as_dict changes.
SCHEMA_VERSION = 1

ONCHAIN_METRICS = ("mvrv_zscore",)  # market/BTC-wide series merged onto each day


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def load_history(
    asset: str, start: str | None = None, end: str | None = None,
    store: Store | None = None,
) -> History:
    """Build a :class:`History` for ``asset`` over ``[start, end]`` from the store.

    OHLCV + that asset's indicators, with the market-wide Fear & Greed and BTC
    on-chain series merged onto each day under flat keys (``fear_greed``,
    ``mvrv_zscore``). Days are exactly the asset's trading days (missing days are
    skipped, never forward-filled — plan missing-data rule).
    """
    store = store or Store()
    series = store.get_asset_series(asset, start, end)

    sentiment = {r["date"]: r["value"] for r in store.get_sentiment(start, end)}
    onchain: dict[str, dict[str, float]] = {}
    for metric in ONCHAIN_METRICS:
        for r in store.get_onchain(metric, start, end):
            onchain.setdefault(r["date"], {})[metric] = r["value"]

    for row in series:
        d = row["date"]
        if d in sentiment:
            row["fear_greed"] = sentiment[d]
        for metric, value in onchain.get(d, {}).items():
            row[metric] = value
    return History(asset, series)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------
@dataclass
class BacktestResult:
    strategy: str
    asset: str
    params: dict[str, Any]
    start: str
    end: str
    snapshots: list[dict[str, Any]]      # per-day equity stream
    trades: list[Any]                    # Trade objects
    metrics_pretax: metrics_mod.Metrics
    metrics_aftertax: metrics_mod.Metrics
    signature: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "strategy": self.strategy,
            "asset": self.asset,
            "params": self.params,
            "start": self.start,
            "end": self.end,
            "signature": self.signature,
            "metrics": {
                "pre_tax": self.metrics_pretax.as_dict(),
                "after_tax": self.metrics_aftertax.as_dict(),
            },
            "trades": [vars(t) for t in self.trades],
            "snapshots": self.snapshots,
        }


# ---------------------------------------------------------------------------
# The daily loop
# ---------------------------------------------------------------------------
def run_backtest(
    history: History,
    strategy: Strategy,
    params: dict[str, Any] | None = None,
    *,
    capital_model: CapitalModel | None = None,
    fee_pct: float = 0.0,
    tax_policy: tax_mod.TaxPolicy | None = None,
) -> BacktestResult:
    """Race one strategy over ``history`` and return its full result."""
    if len(history) == 0:
        raise ValueError("cannot backtest on an empty history")

    params = params if params is not None else strategy.resolve_params()
    capital_model = capital_model or LumpSum(10_000.0)
    tax_policy = tax_policy or tax_mod.TaxPolicy()
    asset = history.asset

    portfolio = Portfolio(fee_pct=fee_pct)
    schedule = capital_model.schedule(history.dates)
    snapshots: list[dict[str, Any]] = []

    for i in range(len(history)):
        history.i = i
        date = history.today
        price = history.price
        if price is None:
            continue  # no tradable close today -> skip the day entirely
        prices = {asset: price}

        # (2) capital-model injection
        if date in schedule:
            portfolio.add_cash(schedule[date], date)

        # (3) strategy decision on data up to today only
        orders, reason = strategy.decide(date, history, params, portfolio)

        # (4) execute at today's close
        for order in orders:
            portfolio.execute(order, prices, date, reason)

        # (5) snapshot
        position_qty = portfolio.quantity(asset)
        snapshots.append({
            "date": date,
            "price": price,
            "cash": portfolio.cash,
            "position_qty": position_qty,
            "holdings_value": position_qty * price,
            "pre_tax_value": portfolio.total_value(prices),
        })

    # -- tax overlay (after-tax curve) -------------------------------------
    realized = [(t.date, t.realized_gain) for t in portfolio.trades if t.side == "SELL"]
    dates = [s["date"] for s in snapshots]
    tax_series = tax_mod.cumulative_tax_series(realized, dates, tax_policy)
    for s in snapshots:
        liability = tax_series.get(s["date"], 0.0)
        s["tax_liability"] = liability
        s["after_tax_value"] = s["pre_tax_value"] - liability

    total_tax = tax_mod.total_tax_paid(realized, tax_policy)
    contributions = list(schedule.items())
    adds_over_time = capital_model.adds_money_over_time

    metrics_pretax = metrics_mod.compute_metrics(
        snapshots, portfolio.trades, contributions,
        value_key="pre_tax_value", adds_money_over_time=adds_over_time,
        total_tax_paid=0.0,
    )
    metrics_aftertax = metrics_mod.compute_metrics(
        snapshots, portfolio.trades, contributions,
        value_key="after_tax_value", adds_money_over_time=adds_over_time,
        total_tax_paid=total_tax,
    )

    signature = {
        "fee_pct": fee_pct,
        "tax": tax_policy.signature(),
        "capital_model": capital_model.signature(),
        "date_range": [history.dates[0], history.dates[-1]],
    }

    return BacktestResult(
        strategy=strategy.name,
        asset=asset,
        params=params,
        start=snapshots[0]["date"] if snapshots else history.dates[0],
        end=snapshots[-1]["date"] if snapshots else history.dates[-1],
        snapshots=snapshots,
        trades=portfolio.trades,
        metrics_pretax=metrics_pretax,
        metrics_aftertax=metrics_aftertax,
        signature=signature,
    )
