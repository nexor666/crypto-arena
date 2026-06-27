"""Metrics — performance stats derived from the per-day snapshot stream.

A pure consumer of the backtest's output (plan modularity principle 6): give it
the dated equity series + the trade list and it produces the numbers the
leaderboard and ``standardized_score`` need. It does not touch the simulation.

CAGR is **plain** for lump-sum runs and **money-weighted (IRR/XIRR)** for any
capital model that adds money over time (DCA / phased) — so contribution timing is
never mistaken for strategy skill (plan). Stage 2 only runs lump sum, but the IRR
path is implemented so DCA drops in without metric changes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date as _date
from typing import Any

TRADING_DAYS_PER_YEAR = 365.0   # crypto trades every calendar day


@dataclass
class Metrics:
    final_value: float
    total_return_pct: float
    cagr: float
    max_drawdown: float          # negative number, e.g. -0.62 = -62%
    sharpe: float
    sortino: float
    n_trades: int
    pct_time_in_market: float
    turnover: float
    total_tax_paid: float = 0.0
    money_weighted: bool = False  # True if cagr is IRR-based

    def as_dict(self) -> dict[str, Any]:
        return {
            "final_value": self.final_value,
            "total_return_pct": self.total_return_pct,
            "cagr": self.cagr,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "sortino": self.sortino,
            "n_trades": self.n_trades,
            "pct_time_in_market": self.pct_time_in_market,
            "turnover": self.turnover,
            "total_tax_paid": self.total_tax_paid,
            "money_weighted": self.money_weighted,
        }


def _years_between(d0: str, d1: str) -> float:
    a = _date.fromisoformat(d0)
    b = _date.fromisoformat(d1)
    return max((b - a).days / TRADING_DAYS_PER_YEAR, 1e-9)


def max_drawdown(values: list[float]) -> float:
    """Largest peak-to-trough decline as a negative fraction (0 if never down)."""
    peak = -math.inf
    worst = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            dd = v / peak - 1.0
            if dd < worst:
                worst = dd
    return worst


def _daily_returns(values: list[float]) -> list[float]:
    out = []
    for prev, cur in zip(values, values[1:]):
        if prev > 0:
            out.append(cur / prev - 1.0)
    return out


def _sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    std = math.sqrt(var)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(TRADING_DAYS_PER_YEAR)


def _sortino(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    downside = [min(0.0, r) for r in returns]
    dvar = sum(d * d for d in downside) / (len(returns) - 1)
    dstd = math.sqrt(dvar)
    if dstd == 0:
        return 0.0
    return (mean / dstd) * math.sqrt(TRADING_DAYS_PER_YEAR)


def xirr(cashflows: list[tuple[str, float]], guess: float = 0.1) -> float | None:
    """Money-weighted annual return for dated cashflows (investor's-eye sign:
    contributions negative, final value positive). Bisection on NPV — robust where
    Newton diverges on volatile crypto series. Returns None if no sign change.
    """
    if len(cashflows) < 2:
        return None
    t0 = _date.fromisoformat(cashflows[0][0])
    times = [(_date.fromisoformat(d) - t0).days / TRADING_DAYS_PER_YEAR for d, _ in cashflows]
    amounts = [a for _, a in cashflows]
    if not (any(a > 0 for a in amounts) and any(a < 0 for a in amounts)):
        return None

    def npv(rate: float) -> float:
        return sum(a / (1.0 + rate) ** t for a, t in zip(amounts, times))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None  # no root bracketed in a sane range
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-7:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


# ---------------------------------------------------------------------------
# Standardized score — the single ranking number (pinned in the plan)
# ---------------------------------------------------------------------------
# Tentative weights (plan: "tune later" — pinned now so day-5 work can't invent a
# different formula). Always computed on the AFTER-TAX result by the caller.
TURNOVER_PENALTY_WEIGHT = 0.01   # penalty per 1x of turnover (100x -> -1.0)
_DD_FLOOR = 0.01                 # floor on |max DD| so Calmar can't blow up


def calmar(cagr: float, max_dd: float) -> float:
    """After-tax CAGR ÷ |max drawdown| — the risk-adjusted core of the score.

    ``max_dd`` is a negative fraction. We floor its magnitude at ``_DD_FLOOR`` so a
    (near-)zero-drawdown curve — e.g. an all-cash strategy that never traded —
    yields a finite, sane number instead of dividing by ~0.
    """
    dd = max(abs(max_dd), _DD_FLOOR)
    return cagr / dd


def standardized_score(m: "Metrics", turnover_weight: float = TURNOVER_PENALTY_WEIGHT) -> float:
    """``Calmar_after_tax − turnover_penalty`` (plan's pinned ranking formula).

    Pass the AFTER-TAX :class:`Metrics`; the turnover penalty kills churn (a
    strategy that only wins by trading constantly is fragile and tax-heavy).
    """
    return calmar(m.cagr, m.max_drawdown) - turnover_weight * m.turnover


def compute_metrics(
    snapshots: list[dict[str, Any]],
    trades: list[Any],
    contributions: list[tuple[str, float]],
    value_key: str = "after_tax_value",
    adds_money_over_time: bool = False,
    total_tax_paid: float = 0.0,
) -> Metrics:
    """Build :class:`Metrics` from the snapshot stream.

    ``snapshots`` -> per day ``{date, pre_tax_value, after_tax_value, position_qty,
    ...}``. ``contributions`` = ``[(date, amount), ...]`` external money in.
    ``value_key`` selects which equity curve to score (after-tax by default).
    """
    if not snapshots:
        return Metrics(0, 0, 0, 0, 0, 0, 0, 0, 0, total_tax_paid)

    values = [s[value_key] for s in snapshots]
    dates = [s["date"] for s in snapshots]
    invested = sum(a for _, a in contributions) or 1e-9
    final = values[-1]

    total_return_pct = final / invested - 1.0

    if adds_money_over_time:
        flows = [(d, -a) for d, a in contributions] + [(dates[-1], final)]
        irr = xirr(sorted(flows, key=lambda x: x[0]))
        cagr = irr if irr is not None else 0.0
        money_weighted = True
    else:
        years = _years_between(dates[0], dates[-1])
        cagr = (final / invested) ** (1.0 / years) - 1.0 if final > 0 else -1.0
        money_weighted = False

    returns = _daily_returns(values)
    in_market_days = sum(1 for s in snapshots if s.get("position_qty", 0) > 0)
    pct_in_market = in_market_days / len(snapshots)

    traded_notional = sum(getattr(t, "proceeds", 0.0) for t in trades)
    avg_equity = sum(values) / len(values) or 1e-9
    turnover = traded_notional / avg_equity

    return Metrics(
        final_value=final,
        total_return_pct=total_return_pct,
        cagr=cagr,
        max_drawdown=max_drawdown(values),
        sharpe=_sharpe(returns),
        sortino=_sortino(returns),
        n_trades=len(trades),
        pct_time_in_market=pct_in_market,
        turnover=turnover,
        total_tax_paid=total_tax_paid,
        money_weighted=money_weighted,
    )
