"""Validation — walk-forward and start-date robustness (the Stage-7 honesty gate).

The race tells you which strategy *looks* best on the full history; that is an
in-sample answer and the plan is explicit that it can be curve-fit noise. These two
checks are the cheap, on-demand filters that separate a real edge from luck:

* :func:`walk_forward` — coarse-grid-tune a strategy's params on an early (in-sample)
  window, then score it **untouched** on the next (out-of-sample) window, and roll
  forward fold by fold. An edge that survives unseen data is probably real. The grid
  is intentionally small here (Stage 8 builds the full auto-tuner); walk-forward just
  needs *a* tuning step so the OOS score means something.
* :func:`robustness` — re-run one fixed config from several different start dates and
  see how often it still beats Buy & Hold. A strategy that only wins from one lucky
  entry date is fragile.

Both reuse the real engine (:func:`run_backtest`) on sliced sub-histories, so they
inherit the no-look-ahead guarantee. Everything is computed after-tax, matching the
ranking lens used everywhere else.
"""

from __future__ import annotations

import itertools
from typing import Any

from backend.engine import metrics as metrics_mod
from backend.engine.backtest import run_backtest
from backend.engine.capital import LumpSum
from backend.engine.history import History
from backend.engine.tax import TaxPolicy
from backend.strategies.base import Strategy

MAX_GRID_COMBOS = 81   # cap so an on-demand walk-forward stays sub-second


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def slice_history(history: History, start: str, end: str) -> History:
    """A new :class:`History` holding only ``history``'s rows in ``[start, end]``.

    Indicators are stored per-row, so a slice still carries each day's precomputed
    MA/Mayer/RSI — only the lookback *length* shortens at a fold boundary, which is
    the honest behaviour for an out-of-sample window.
    """
    rows = [r for r in history._records if start <= r["date"] <= end]
    return History(history.asset, rows)


def _grid_values(spec: dict[str, Any], max_per: int = 3) -> list[float]:
    """Up to ``max_per`` values spanning a parameter's [min, max] (inclusive ends)."""
    lo, hi, is_int = float(spec["min"]), float(spec["max"]), spec.get("type") == "int"
    if hi <= lo:
        return [int(lo) if is_int else lo]
    n = max(2, min(max_per, 1 + int(round((hi - lo) / max(spec.get("step", 1) or 1, 1e-9)))))
    vals = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    if is_int:
        vals = sorted({int(round(v)) for v in vals})
    return vals


def grid_combos(schema_json: dict[str, dict], max_combos: int = MAX_GRID_COMBOS) -> list[dict[str, Any]]:
    """Coarse cartesian grid over a strategy's declared param schema (capped).

    With no params (e.g. Buy & Hold) returns ``[{}]`` — a single no-op combo, so a
    parameterless strategy still walks-forward (just with nothing to tune).
    """
    if not schema_json:
        return [{}]
    keys = list(schema_json)
    axes = [_grid_values(schema_json[k]) for k in keys]
    combos = [dict(zip(keys, vals)) for vals in itertools.product(*axes)]
    if len(combos) > max_combos:
        # thin uniformly rather than truncating, so we keep spread across the space
        stride = len(combos) / max_combos
        combos = [combos[int(i * stride)] for i in range(max_combos)]
    return combos


def _run_score(history: History, cls: type[Strategy], params: dict[str, Any],
               *, fee_pct: float, tax_policy: TaxPolicy, capital: float):
    """Run one strategy over ``history`` and return its after-tax Metrics."""
    result = run_backtest(
        history, cls(), params=cls.resolve_params(params),
        capital_model=LumpSum(capital), fee_pct=fee_pct, tax_policy=tax_policy,
    )
    return result.metrics_aftertax


def default_start_dates(dates: list[str], n: int = 6, min_years: float = 1.0) -> list[str]:
    """Evenly spaced candidate start dates across the first ~half of the window.

    Each leaves at least ``min_years`` of data after it, so every robustness run has
    a meaningful window to score over.
    """
    if len(dates) < 30:
        return [dates[0]] if dates else []
    from datetime import date as _d

    last = _d.fromisoformat(dates[-1])
    eligible = [d for d in dates if (last - _d.fromisoformat(d)).days / 365.0 >= min_years]
    if not eligible:
        eligible = dates[: max(1, len(dates) // 2)]
    # take from the first half of the eligible range so starts actually differ
    span = eligible[: max(1, len(eligible) // 2 + 1)]
    if len(span) <= n:
        return span
    stride = (len(span) - 1) / (n - 1)
    return [span[int(round(k * stride))] for k in range(n)]


# ---------------------------------------------------------------------------
# walk-forward
# ---------------------------------------------------------------------------
def walk_forward(
    history: History, cls: type[Strategy], *,
    n_folds: int = 4, fee_pct: float = 0.0,
    tax_policy: TaxPolicy | None = None, capital: float = 10_000.0,
) -> dict[str, Any]:
    """Anchored walk-forward: expand the train window, score the next chunk untouched.

    The full window is split into ``n_folds + 1`` contiguous chunks. For fold *i* we
    tune (coarse grid, best in-sample after-tax score) on everything up to chunk
    *i+1*, then evaluate those params on chunk *i+1* only, comparing against Buy &
    Hold over the same out-of-sample chunk.
    """
    tax_policy = tax_policy or TaxPolicy()
    dates = history.dates
    if len(dates) < (n_folds + 1) * 20:
        n_folds = max(1, len(dates) // 40)
    bounds = [round(k * len(dates) / (n_folds + 1)) for k in range(n_folds + 2)]

    bh = registry_buy_hold()
    schema = cls.schema_json()
    combos = grid_combos(schema)

    folds = []
    for i in range(n_folds):
        train_lo, test_lo, test_hi = bounds[0], bounds[i + 1], bounds[i + 2] - 1
        if test_hi <= test_lo:
            continue
        train = slice_history(history, dates[train_lo], dates[test_lo - 1])
        test = slice_history(history, dates[test_lo], dates[test_hi])
        if len(train) < 10 or len(test) < 10:
            continue

        # tune on in-sample
        best_params, best_score = combos[0], -1e18
        for combo in combos:
            m = _run_score(train, cls, combo, fee_pct=fee_pct,
                           tax_policy=tax_policy, capital=capital)
            s = metrics_mod.standardized_score(m)
            if s > best_score:
                best_params, best_score = combo, s

        # score out-of-sample with the tuned params
        oos = _run_score(test, cls, best_params, fee_pct=fee_pct,
                         tax_policy=tax_policy, capital=capital)
        oos_score = metrics_mod.standardized_score(oos)
        bh_oos = _run_score(test, bh, {}, fee_pct=fee_pct,
                            tax_policy=tax_policy, capital=capital)
        alpha = oos.total_return_pct - bh_oos.total_return_pct

        folds.append({
            "fold": i + 1,
            "train_start": dates[train_lo], "train_end": dates[test_lo - 1],
            "test_start": dates[test_lo], "test_end": dates[test_hi],
            "tuned_params": cls.resolve_params(best_params),
            "in_sample_score": round(best_score, 4),
            "oos_score": round(oos_score, 4),
            "oos_return_pct": round(oos.total_return_pct, 4),
            "bh_return_pct": round(bh_oos.total_return_pct, 4),
            "alpha_vs_bh": round(alpha, 4),
            "beats_bh": alpha > 0,
        })

    n = len(folds) or 1
    wins = sum(1 for f in folds if f["beats_bh"])
    mean_oos = sum(f["oos_score"] for f in folds) / n
    return {
        "strategy": cls.name,
        "n_folds": len(folds),
        "folds": folds,
        "mean_oos_score": round(mean_oos, 4),
        "beats_bh_folds": wins,
        "beats_bh_rate": round(wins / n, 4),
        # a positive mean OOS score AND beating B&H in most folds = the edge held up
        "verdict": "robust" if (mean_oos > 0 and wins / n >= 0.6) else "fragile",
    }


# ---------------------------------------------------------------------------
# start-date robustness
# ---------------------------------------------------------------------------
def robustness(
    history: History, cls: type[Strategy], params: dict[str, Any], *,
    start_dates: list[str] | None = None, fee_pct: float = 0.0,
    tax_policy: TaxPolicy | None = None, capital: float = 10_000.0,
) -> dict[str, Any]:
    """Re-run one fixed config from several start dates → how often it beats B&H."""
    tax_policy = tax_policy or TaxPolicy()
    dates = history.dates
    starts = start_dates or default_start_dates(dates)
    end = dates[-1]
    bh = registry_buy_hold()

    runs = []
    for s in starts:
        sub = slice_history(history, s, end)
        if len(sub) < 10:
            continue
        m = _run_score(sub, cls, params, fee_pct=fee_pct,
                       tax_policy=tax_policy, capital=capital)
        bhm = _run_score(sub, bh, {}, fee_pct=fee_pct,
                         tax_policy=tax_policy, capital=capital)
        alpha = m.total_return_pct - bhm.total_return_pct
        runs.append({
            "start": s, "end": end,
            "score": round(metrics_mod.standardized_score(m), 4),
            "cagr": round(m.cagr, 4),
            "return_pct": round(m.total_return_pct, 4),
            "bh_return_pct": round(bhm.total_return_pct, 4),
            "alpha_vs_bh": round(alpha, 4),
            "beats_bh": alpha > 0,
        })

    n = len(runs) or 1
    wins = sum(1 for r in runs if r["beats_bh"])
    scores = [r["score"] for r in runs] or [0.0]
    return {
        "strategy": cls.name,
        "params": cls.resolve_params(params),
        "n_starts": len(runs),
        "runs": runs,
        "beats_bh_rate": round(wins / n, 4),
        "score_min": round(min(scores), 4),
        "score_max": round(max(scores), 4),
        "score_mean": round(sum(scores) / len(scores), 4),
        # wins from most entry dates = the edge isn't a single-date fluke
        "verdict": "robust" if wins / n >= 0.6 else "fragile",
    }


def registry_buy_hold() -> type[Strategy]:
    """The Buy & Hold class — the universal benchmark for alpha."""
    from backend.strategies.base import registry

    reg = registry()
    return reg["buy_hold"]
