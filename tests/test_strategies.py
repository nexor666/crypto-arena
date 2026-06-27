"""Stage-3 strategy library tests.

One cheap, deterministic check per new strategy (synthetic data, no DB) proving its
signal fires the right way, plus the registry wiring and the standardized_score
formula. These complement the engine golden tests in ``test_engine.py``.
"""

from __future__ import annotations

import math

import pytest

from backend.engine import metrics as metrics_mod
from backend.engine.backtest import run_backtest
from backend.engine.capital import LumpSum
from backend.engine.history import History
from backend.engine.portfolio import Portfolio
from backend.engine.tax import TaxPolicy
from backend.strategies.base import registry
from backend.strategies.halving_cycle import HalvingCycle, _days_since_last_halving
from backend.strategies.rebalance import Rebalance


def _history(records, asset="BTC"):
    """Build a History from explicit per-day dicts (must include date+close)."""
    return History(asset, records)


def _race(strat, records, asset="BTC", capital=10_000.0):
    return run_backtest(
        _history(records, asset), strat, capital_model=LumpSum(capital),
        fee_pct=0.0, tax_policy=TaxPolicy(enabled=False),
    )


# ---------------------------------------------------------------------------
# Registry wiring — all 9 strategies discovered
# ---------------------------------------------------------------------------
def test_registry_has_all_strategies():
    reg = registry()
    expected = {
        "buy_hold", "fear_greed", "200w_ma", "mayer", "mvrv_z",
        "halving", "ma_cross", "rsi_weekly", "rebalance",
    }
    assert expected <= set(reg)
    # Every strategy declares a non-empty, well-formed param schema.
    for name, cls in reg.items():
        for key, spec in cls.param_schema.items():
            assert spec.min <= spec.default <= spec.max, f"{name}.{key} default out of bounds"


# ---------------------------------------------------------------------------
# Threshold strategies: buy on low signal, sell on high signal
# ---------------------------------------------------------------------------
def test_mayer_buys_low_sells_high():
    from backend.strategies.mayer_multiple import MayerMultiple
    recs = [
        {"date": "2020-01-01", "close": 100.0, "mayer": 0.8},   # buy (<1.0)
        {"date": "2020-01-02", "close": 150.0, "mayer": 1.5},   # hold
        {"date": "2020-01-03", "close": 300.0, "mayer": 2.6},   # sell (>2.4)
    ]
    sides = [t.side for t in _race(MayerMultiple(), recs).trades]
    assert sides[0] == "BUY" and "SELL" in sides


def test_200w_ma_buys_below_sells_above():
    from backend.strategies.two_hundred_week_ma import TwoHundredWeekMA
    recs = [
        {"date": "2020-01-01", "close": 90.0, "ma_200w": 100.0},   # ratio 0.9 -> buy
        {"date": "2020-01-02", "close": 150.0, "ma_200w": 100.0},  # ratio 1.5 -> hold
        {"date": "2020-01-03", "close": 350.0, "ma_200w": 100.0},  # ratio 3.5 -> sell
    ]
    sides = [t.side for t in _race(TwoHundredWeekMA(), recs).trades]
    assert sides[0] == "BUY" and "SELL" in sides


def test_rsi_weekly_buys_oversold_sells_overbought():
    from backend.strategies.rsi_weekly import RsiWeekly
    recs = [
        {"date": "2020-01-01", "close": 100.0, "rsi_14w": 25.0},  # oversold -> buy
        {"date": "2020-01-02", "close": 100.0, "rsi_14w": 50.0},  # hold
        {"date": "2020-01-03", "close": 100.0, "rsi_14w": 80.0},  # overbought -> sell
    ]
    sides = [t.side for t in _race(RsiWeekly(), recs).trades]
    assert sides[0] == "BUY" and "SELL" in sides


def test_mvrv_buys_low_sells_high_and_holds_when_missing():
    from backend.strategies.mvrv_zscore import MvrvZScore
    recs = [
        {"date": "2020-01-01", "close": 100.0, "mvrv_zscore": -0.5},  # Z<0 -> buy
        {"date": "2020-01-02", "close": 200.0, "mvrv_zscore": 8.0},   # Z>7 -> sell
    ]
    sides = [t.side for t in _race(MvrvZScore(), recs).trades]
    assert sides[0] == "BUY" and "SELL" in sides

    # No mvrv field at all -> never trades (the empty-onchain-table case).
    no_signal = [{"date": "2020-01-01", "close": 100.0},
                 {"date": "2020-01-02", "close": 200.0}]
    assert _race(MvrvZScore(), no_signal).trades == []


# ---------------------------------------------------------------------------
# MA crossover: long while fast>slow, cash while fast<slow
# ---------------------------------------------------------------------------
def test_ma_crossover_regime():
    from backend.strategies.ma_crossover import MaCrossover
    recs = [
        {"date": "2020-01-01", "close": 100.0, "ma_50d": 110.0, "ma_200d": 100.0},  # bullish -> buy
        {"date": "2020-01-02", "close": 120.0, "ma_50d": 115.0, "ma_200d": 100.0},  # still long
        {"date": "2020-01-03", "close": 100.0, "ma_50d": 90.0, "ma_200d": 100.0},   # bearish -> sell
    ]
    result = _race(MaCrossover(), recs)
    sides = [t.side for t in result.trades]
    assert sides == ["BUY", "SELL"]
    # confirm_days delays action until the regime has held N prior days.
    delayed = run_backtest(
        _history(recs), MaCrossover(), params={"confirm_days": 5},
        capital_model=LumpSum(10_000.0), fee_pct=0.0, tax_policy=TaxPolicy(enabled=False),
    )
    assert delayed.trades == []  # never confirmed within 3 days


# ---------------------------------------------------------------------------
# Halving cycle: days-since-halving windows
# ---------------------------------------------------------------------------
def test_days_since_last_halving():
    # 2024-04-20 halving; 100 days later.
    assert _days_since_last_halving("2024-07-29") == 100
    # Before the first known halving (2012-11-28) -> None.
    assert _days_since_last_halving("2010-01-01") is None


def test_halving_buys_in_accumulate_window():
    strat = HalvingCycle()
    params = strat.resolve_params()
    pf = Portfolio(fee_pct=0.0)
    pf.add_cash(1000.0, "2027-01-01")
    # ~1000 days after the 2024-04-20 halving falls in the accumulate window.
    hist = _history([{"date": "2027-01-15", "close": 100.0}])
    orders, _ = strat.decide("2027-01-15", hist, params, pf)
    assert orders and orders[0].side == "BUY"


# ---------------------------------------------------------------------------
# Rebalance: drives the coin weight to target
# ---------------------------------------------------------------------------
def test_rebalance_hits_target_weight():
    recs = [{"date": f"2020-{1 + i // 28:02d}-{1 + i % 28:02d}", "close": 100.0} for i in range(5)]
    result = _race(Rebalance(), recs, capital=1000.0)
    last = result.snapshots[-1]
    total = last["cash"] + last["holdings_value"]
    weight = last["holdings_value"] / total
    assert math.isclose(weight, 0.6, abs_tol=1e-6)  # default target_weight


# ---------------------------------------------------------------------------
# Standardized score formula
# ---------------------------------------------------------------------------
def test_standardized_score_formula():
    m = metrics_mod.Metrics(
        final_value=0, total_return_pct=0, cagr=0.20, max_drawdown=-0.40,
        sharpe=0, sortino=0, n_trades=0, pct_time_in_market=0, turnover=2.0,
    )
    # Calmar = 0.20 / 0.40 = 0.5; penalty = 0.01 * 2 = 0.02 -> 0.48.
    assert math.isclose(metrics_mod.standardized_score(m), 0.48, rel_tol=1e-9)
    # Flat all-cash strategy (no DD, no return) -> 0, not a divide-by-zero blowup.
    flat = metrics_mod.Metrics(0, 0, 0.0, 0.0, 0, 0, 0, 0, 0.0)
    assert metrics_mod.standardized_score(flat) == 0.0
