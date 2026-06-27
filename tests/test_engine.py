"""Golden / correctness tests for the Stage-2 backtest engine.

These are the refactor safety net the plan calls for (modularity principle 7):
  * Buy & Hold (fee=0) must equal the asset's raw price return — proves no
    look-ahead and no fee bug in the core loop.
  * A hand-checked HPFU sell produces the right realized gain and 22% tax.
  * History physically cannot see the future.

All synthetic (no DB dependency) so they're deterministic and fast.
"""

from __future__ import annotations

import math

import pytest

from backend.engine.backtest import run_backtest
from backend.engine.capital import LumpSum
from backend.engine.history import History
from backend.engine.portfolio import Portfolio
from backend.engine.tax import TaxPolicy, total_tax_paid
from backend.strategies.base import BUY, SELL, Order
from backend.strategies.buy_hold import BuyHold
from backend.strategies.fear_greed import FearGreed


def _history(closes, asset="BTC", extra=None):
    """Build a History from a list of closes (dates auto-generated, daily)."""
    records = []
    for i, c in enumerate(closes):
        day = 1 + i
        rec = {"date": f"2020-01-{day:02d}", "close": float(c)}
        if extra and i < len(extra):
            rec.update(extra[i])
        records.append(rec)
    return History(asset, records)


# ---------------------------------------------------------------------------
# Golden check: Buy & Hold == raw price return (fee 0, no tax)
# ---------------------------------------------------------------------------
def test_buy_hold_matches_price_return_no_fee():
    closes = [100, 120, 90, 150, 300]
    hist = _history(closes)
    result = run_backtest(
        hist, BuyHold(), capital_model=LumpSum(10_000.0),
        fee_pct=0.0, tax_policy=TaxPolicy(enabled=False),
    )
    price_return = closes[-1] / closes[0] - 1.0
    assert math.isclose(result.metrics_pretax.total_return_pct, price_return, rel_tol=1e-12)
    # Final value should be exactly capital * price_ratio.
    assert math.isclose(result.metrics_pretax.final_value, 10_000.0 * closes[-1] / closes[0], rel_tol=1e-12)
    # No sells -> after-tax == pre-tax.
    assert math.isclose(result.metrics_aftertax.final_value, result.metrics_pretax.final_value, rel_tol=1e-12)


def test_buy_hold_fee_creates_drag():
    closes = [100, 200]
    hist = _history(closes)
    fee = 0.001
    result = run_backtest(
        hist, BuyHold(), capital_model=LumpSum(10_000.0),
        fee_pct=fee, tax_policy=TaxPolicy(enabled=False),
    )
    # One buy of fee-inclusive notional; final value = (capital/(1+fee))/p0 * p1.
    expected = (10_000.0 / (1 + fee)) / 100.0 * 200.0
    assert math.isclose(result.metrics_pretax.final_value, expected, rel_tol=1e-12)
    # And it's strictly below the no-fee result.
    assert result.metrics_pretax.final_value < 10_000.0 * 2.0


# ---------------------------------------------------------------------------
# HPFU realized gain + 22% tax — hand check
# ---------------------------------------------------------------------------
def test_hpfu_sell_gain_and_tax():
    pf = Portfolio(fee_pct=0.0)
    pf.add_cash(1000.0, "2020-01-01")

    pf.execute(Order("BTC", BUY, quote=100.0), {"BTC": 100.0}, "2020-01-01")   # lot A: 1 @ 100
    pf.execute(Order("BTC", BUY, quote=200.0), {"BTC": 200.0}, "2020-02-01")   # lot B: 1 @ 200
    assert math.isclose(pf.quantity("BTC"), 2.0)

    # Sell 1 coin at 300. HPFU sells the highest-cost lot (B @ 200) first.
    trade = pf.execute(Order("BTC", SELL, base=1.0), {"BTC": 300.0}, "2020-03-01")
    assert trade is not None and trade.side == "SELL"
    assert math.isclose(trade.realized_gain, 100.0)   # 300 proceeds - 200 basis

    # The remaining lot is the LOW-cost one (A @ 100), proving HPFU ordering.
    remaining = pf.lots["BTC"]
    assert len(remaining) == 1
    assert math.isclose(remaining[0].cost_per_unit, 100.0)
    assert math.isclose(remaining[0].qty, 1.0)

    # 22% tax on the 100 gain = 22.
    realized = [(t.date, t.realized_gain) for t in pf.trades if t.side == "SELL"]
    assert math.isclose(total_tax_paid(realized, TaxPolicy(rate=0.22)), 22.0)


def test_within_year_losses_offset_gains():
    pf = Portfolio(fee_pct=0.0)
    pf.add_cash(1000.0, "2021-01-01")
    pf.execute(Order("BTC", BUY, quote=100.0), {"BTC": 100.0}, "2021-01-01")   # lot @100
    pf.execute(Order("BTC", BUY, quote=300.0), {"BTC": 300.0}, "2021-02-01")   # lot @300
    # Sell the @300 lot at 200 -> loss of 100 (HPFU picks highest cost first).
    pf.execute(Order("BTC", SELL, base=1.0), {"BTC": 200.0}, "2021-03-01")
    # Sell the @100 lot at 250 -> gain of 150.
    pf.execute(Order("BTC", SELL, base=1.0), {"BTC": 250.0}, "2021-04-01")
    realized = [(t.date, t.realized_gain) for t in pf.trades if t.side == "SELL"]
    # Net 2021 gain = -100 + 150 = 50; tax = 11.
    assert math.isclose(total_tax_paid(realized, TaxPolicy(rate=0.22)), 11.0)


# ---------------------------------------------------------------------------
# No look-ahead — History cannot see the future
# ---------------------------------------------------------------------------
def test_history_is_bounded_to_today():
    extra = [{"marker": i} for i in range(5)]
    hist = _history([10, 11, 12, 13, 14], extra=extra)
    hist.i = 2  # "today" is index 2

    assert hist.today == "2020-01-03"
    assert hist.price == 12.0
    assert hist.get("marker") == 2
    assert hist.prev("marker") == 1
    # window never includes future bars (indices 3, 4 excluded).
    assert hist.window("marker", 10) == [0, 1, 2]
    assert [r["marker"] for r in hist.records_so_far] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Fear & Greed behaves
# ---------------------------------------------------------------------------
def test_fear_greed_buys_on_fear_sells_on_greed():
    closes = [100, 100, 100]
    extra = [{"fear_greed": 10}, {"fear_greed": 50}, {"fear_greed": 90}]
    hist = _history(closes, extra=extra)
    result = run_backtest(
        hist, FearGreed(), capital_model=LumpSum(10_000.0),
        fee_pct=0.0, tax_policy=TaxPolicy(enabled=False),
    )
    sides = [t.side for t in result.trades]
    assert sides[0] == "BUY"     # day 0 extreme fear (10 <= 25)
    assert "SELL" in sides       # day 2 extreme greed (90 >= 75)


def test_fear_greed_holds_when_index_missing():
    hist = _history([100, 100], extra=[{}, {}])  # no fear_greed field
    result = run_backtest(
        hist, FearGreed(), capital_model=LumpSum(10_000.0),
        fee_pct=0.0, tax_policy=TaxPolicy(enabled=False),
    )
    assert result.trades == []
