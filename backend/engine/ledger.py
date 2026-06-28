"""Run ledger helpers — turn a finished backtest into a persistent Hall-of-Fame row.

The ``runs`` table (``store.py``) is the plan's "persistent Hall of Fame": every
backtest appends a row so results accumulate across sessions. Two things must stay
stable across days, so they live here rather than being re-derived ad hoc:

* :func:`settings_signature` — a short deterministic string identifying the run's
  *settings* (asset, window, fee, tax, capital model). The Hall of Fame only ranks
  runs that share a signature, so a lump-sum BTC run is never leaderboard-compared
  against a DCA ETH run (plan: never compare apples to oranges).
* :func:`run_row` — maps a :class:`BacktestResult` (+ the run config) onto the
  ledger's column dict, so the API and the validation tools record identical shapes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def settings_signature(
    asset: str, start: str, end: str, fee_pct: float,
    tax_sig: dict[str, Any], capital_sig: dict[str, Any],
) -> str:
    """A stable, human-readable signature of a run's comparable settings.

    Same settings → same string (so the ledger can group apples-to-apples);
    JSON with sorted keys makes it order-independent and deterministic.
    """
    payload = {
        "asset": asset,
        "range": [start, end],
        "fee_pct": round(float(fee_pct), 10),
        "tax": tax_sig,
        "capital": capital_sig,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def run_row(
    *, asset: str, strategy: str, params: dict[str, Any], start: str, end: str,
    fee_pct: float, tax_sig: dict[str, Any], capital_sig: dict[str, Any],
    settings_sig: str, score: float, after_tax_cagr: float, after_tax_final: float,
    max_drawdown: float, sharpe: float, n_trades: int, is_walkforward: bool = False,
) -> dict[str, Any]:
    """Build a ``runs`` ledger row dict (column names match ``Store._RUN_COLS``)."""
    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asset": asset,
        "strategy": strategy,
        "params_json": json.dumps(params, sort_keys=True),
        "start": start,
        "end": end,
        "fee_pct": float(fee_pct),
        "tax_json": json.dumps(tax_sig, sort_keys=True),
        "capital_json": json.dumps(capital_sig, sort_keys=True),
        "settings_sig": settings_sig,
        "standardized_score": float(score),
        "after_tax_cagr": float(after_tax_cagr),
        "after_tax_final": float(after_tax_final),
        "max_drawdown": float(max_drawdown),
        "sharpe": float(sharpe),
        "n_trades": int(n_trades),
        "is_walkforward": 1 if is_walkforward else 0,
    }
