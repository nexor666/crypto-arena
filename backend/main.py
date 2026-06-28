"""Crypto Cycle Strategy Arena — FastAPI application entrypoint.

Stage 4: the API layer over the engine. On top of the Stage-1 data routes, the
app now exposes the strategy registry + their param schemas (``/api/strategies``),
the cycle event markers (``/api/events``), and the engine itself via
``POST /api/backtest`` — which races the selected strategies over a chosen
window and returns the full per-day equity stream + trades + metrics for each, so
the (Stage-5) frontend can render and animate the race entirely client-side.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config
from backend.data.refresh import refresh as run_refresh
from backend.data.store import Store
from backend.engine import ledger, metrics as metrics_mod
from backend.engine import validation as validation_mod
from backend.engine.backtest import SCHEMA_VERSION, load_history, run_backtest
from backend.engine.capital import LumpSum
from backend.engine.tax import TaxPolicy
from backend.strategies.base import get_strategy, registry

# The plan's default *analysis* window (Fear & Greed has full coverage from here,
# so every strategy is comparable). Callers may override per request.
DEFAULT_ANALYSIS_START = "2018-01-01"

app = FastAPI(title="Crypto Cycle Strategy Arena", version="0.1.0")

# Repo layout: backend/main.py  ->  ../frontend
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# One Store instance is fine: each call opens its own short-lived connection.
store = Store()
store.init_schema()  # ensure all tables (incl. the Stage-7 runs ledger) exist


@app.get("/api/health")
def health() -> dict:
    """Liveness probe. Returns OK so we can confirm the app is up."""
    return {"status": "ok", "service": "crypto-arena", "stage": 7}


@app.get("/api/data/status")
def data_status() -> dict:
    """Row counts + date ranges per table — the Stage-1 sanity-check view."""
    store.init_schema()  # tolerate a first call before any refresh
    return store.status()


@app.get("/api/price-data/{asset}")
def price_data(
    asset: str,
    start: str | None = Query(None, description="inclusive ISO date lower bound"),
    end: str | None = Query(None, description="inclusive ISO date upper bound"),
) -> dict:
    """Return one asset's OHLCV series joined with its indicators, as JSON."""
    asset = asset.upper()
    if asset not in config.ASSETS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown asset '{asset}'; known: {sorted(config.ASSETS)}",
        )
    series = store.get_asset_series(asset, start, end)
    if not series:
        raise HTTPException(
            status_code=404,
            detail=f"no data for '{asset}' — run a refresh first (POST /api/admin/refresh)",
        )
    return {
        "asset": asset,
        "start": series[0]["date"],
        "end": series[-1]["date"],
        "count": len(series),
        "series": series,
    }


@app.get("/api/sentiment")
def sentiment(
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    """Market-wide Fear & Greed series."""
    rows = store.get_sentiment(start, end)
    return {"metric": "fear_greed", "count": len(rows), "series": rows}


@app.get("/api/onchain/{metric}")
def onchain(
    metric: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
) -> dict:
    """On-chain series (currently BTC ``mvrv_zscore``)."""
    rows = store.get_onchain(metric, start, end)
    if not rows:
        raise HTTPException(status_code=404, detail=f"no on-chain data for metric '{metric}'")
    return {"metric": metric, "count": len(rows), "series": rows}


@app.post("/api/admin/refresh")
def admin_refresh(start: str | None = Query(None, description="earliest date to fetch")) -> dict:
    """Pull every source into SQLite (snapshotting raw payloads). Synchronous.

    A full pull is a one-time, multi-second operation (a handful of HTTP calls),
    so running it inline keeps the contract simple — no job queue needed.
    """
    return run_refresh(start=start or config.FETCH_START, store=store)


# ---------------------------------------------------------------------------
# Stage 4 — engine API
# ---------------------------------------------------------------------------
@app.get("/api/strategies")
def strategies() -> dict:
    """Every registered strategy with its declared parameter schema.

    The frontend builds its sliders/controls directly from ``param_schema`` (plan
    modularity principle 1), so a newly-added strategy appears in the UI with
    working controls and zero frontend changes.
    """
    reg = registry()
    out = []
    for name in sorted(reg):
        cls = reg[name]
        out.append({
            "name": name,
            "description": cls.description,
            "universe": cls.universe,
            "param_schema": cls.schema_json(),
            "default_params": cls.default_params(),
        })
    return {"count": len(out), "strategies": out}


@app.get("/api/events")
def events() -> dict:
    """Cycle markers for the chart / jump-to buttons: halvings + notable tops/bottoms.

    Halvings are a deterministic public schedule and the notable events are
    historical extremes — both are display/navigation aids only (no strategy ever
    reads them), so they carry no look-ahead.
    """
    halvings = [
        {"date": d, "label": f"Halving {i + 1}", "kind": "halving"}
        for i, d in enumerate(config.HALVING_DATES)
    ]
    return {"halvings": halvings, "notable": config.NOTABLE_EVENTS}


class StrategySelection(BaseModel):
    """One strategy to race, with optional parameter overrides (defaults used for
    any key omitted; unknown keys are rejected)."""

    name: str
    params: dict[str, float] = Field(default_factory=dict)


class TaxSettings(BaseModel):
    enabled: bool = True
    rate: float = 0.22


class BacktestRequest(BaseModel):
    """Body for ``POST /api/backtest`` — a full run configuration.

    ``strategies`` empty/omitted means "race every registered strategy" (mirrors
    the CLI). Only lump-sum capital is exposed for now; DCA/phased drop in here
    later via the same engine hook without changing this contract.
    """

    asset: str = "BTC"
    start: str | None = DEFAULT_ANALYSIS_START
    end: str | None = None
    capital: float = 10_000.0
    fee_pct: float = 0.00075
    tax: TaxSettings = Field(default_factory=TaxSettings)
    strategies: list[StrategySelection] = Field(default_factory=list)


@app.post("/api/backtest")
def backtest(req: BacktestRequest) -> dict:
    """Race the selected strategies over the requested window.

    Returns, per strategy, the full per-day equity stream (pre/after-tax) + trades
    + metrics (the versioned ``BacktestResult`` shape), plus a ready leaderboard
    ranked by the pinned ``standardized_score`` (after-tax). The whole result is
    computed server-side so the frontend can scrub/animate it without re-running.
    """
    asset = req.asset.upper()
    if asset not in config.ASSETS:
        raise HTTPException(
            status_code=404,
            detail=f"unknown asset '{asset}'; known: {sorted(config.ASSETS)}",
        )

    history = load_history(asset, req.start, req.end, store=store)
    if len(history) == 0:
        raise HTTPException(
            status_code=404,
            detail=f"no data for '{asset}' in {req.start}..{req.end} — refresh first",
        )

    reg = registry()
    selections = req.strategies or [StrategySelection(name=n) for n in sorted(reg)]

    tax_policy = TaxPolicy(enabled=req.tax.enabled, rate=req.tax.rate)
    capital_model = LumpSum(req.capital)
    win_start, win_end = history.dates[0], history.dates[-1]
    settings_sig = ledger.settings_signature(
        asset, win_start, win_end, req.fee_pct,
        tax_policy.signature(), capital_model.signature(),
    )

    results: list[dict] = []
    for sel in selections:
        if sel.name not in reg:
            raise HTTPException(
                status_code=400,
                detail=f"unknown strategy '{sel.name}'; known: {sorted(reg)}",
            )
        cls = reg[sel.name]
        try:
            params = cls.resolve_params(sel.params)
        except KeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        result = run_backtest(
            history, cls(), params=params,
            capital_model=capital_model,
            fee_pct=req.fee_pct, tax_policy=tax_policy,
        )
        post = result.metrics_aftertax
        score = metrics_mod.standardized_score(post)
        payload = result.as_dict()
        payload["score"] = score
        results.append(payload)

        # append this run to the persistent Hall-of-Fame ledger
        store.record_run(ledger.run_row(
            asset=asset, strategy=sel.name, params=params,
            start=win_start, end=win_end, fee_pct=req.fee_pct,
            tax_sig=tax_policy.signature(), capital_sig=capital_model.signature(),
            settings_sig=settings_sig, score=score,
            after_tax_cagr=post.cagr, after_tax_final=post.final_value,
            max_drawdown=post.max_drawdown, sharpe=post.sharpe,
            n_trades=post.n_trades,
        ))

    leaderboard = sorted(
        (
            {
                "name": r["strategy"],
                "score": r["score"],
                "after_tax_final": r["metrics"]["after_tax"]["final_value"],
                "after_tax_cagr": r["metrics"]["after_tax"]["cagr"],
                "after_tax_return_pct": r["metrics"]["after_tax"]["total_return_pct"],
                "max_drawdown": r["metrics"]["after_tax"]["max_drawdown"],
                "n_trades": r["metrics"]["after_tax"]["n_trades"],
            }
            for r in results
        ),
        key=lambda x: x["score"],
        reverse=True,
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "asset": asset,
        "start": win_start,
        "end": win_end,
        "capital": req.capital,
        "fee_pct": req.fee_pct,
        "tax": tax_policy.signature(),
        "settings_sig": settings_sig,
        "leaderboard": leaderboard,
        "results": results,
    }


# ---------------------------------------------------------------------------
# Stage 7 — persistent Hall of Fame + validation
# ---------------------------------------------------------------------------
def _hall_of_fame(rows: list[dict]) -> list[dict]:
    """Aggregate ledger rows (one settings signature) into a ranked board.

    Strategy-level by default (one entry per strategy = its best config + total
    tries), each expandable to its individual configurations. Margins are score
    deltas to the next strategy and to Buy & Hold — the plan's "how far ahead is the
    winner" view.
    """
    by_strategy: dict[str, list[dict]] = {}
    for r in rows:
        by_strategy.setdefault(r["strategy"], []).append(r)

    entries = []
    for name, runs in by_strategy.items():
        # group identical param sets into configs
        configs: dict[str, dict] = {}
        for r in runs:
            key = r["params_json"]
            c = configs.get(key)
            if c is None or r["standardized_score"] > c["score"]:
                configs[key] = {
                    "params": json.loads(r["params_json"]),
                    "score": r["standardized_score"],
                    "after_tax_cagr": r["after_tax_cagr"],
                    "max_drawdown": r["max_drawdown"],
                    "after_tax_final": r["after_tax_final"],
                    "tries": 0,
                    "last_run": r["timestamp"],
                }
            configs[key]["tries"] += 1
        best = max(runs, key=lambda r: r["standardized_score"])
        entries.append({
            "strategy": name,
            "best_score": best["standardized_score"],
            "after_tax_cagr": best["after_tax_cagr"],
            "after_tax_final": best["after_tax_final"],
            "max_drawdown": best["max_drawdown"],
            "sharpe": best["sharpe"],
            "n_trades": best["n_trades"],
            "best_params": json.loads(best["params_json"]),
            "times_tried": len(runs),
            "configs": sorted(configs.values(), key=lambda c: c["score"], reverse=True),
        })

    entries.sort(key=lambda e: e["best_score"], reverse=True)
    bh_score = next((e["best_score"] for e in entries if e["strategy"] == "buy_hold"), None)
    for i, e in enumerate(entries):
        nxt = entries[i + 1]["best_score"] if i + 1 < len(entries) else None
        e["margin_over_next"] = round(e["best_score"] - nxt, 4) if nxt is not None else None
        e["margin_over_bh"] = round(e["best_score"] - bh_score, 4) if bh_score is not None else None
    return entries


def _best_over_time(rows: list[dict]) -> list[dict]:
    """Running best standardized_score vs experiment number (oldest → newest)."""
    chrono = sorted(rows, key=lambda r: r["id"])
    out, running = [], -1e18
    for n, r in enumerate(chrono, 1):
        running = max(running, r["standardized_score"])
        out.append({"n": n, "best_score": round(running, 4),
                    "score": round(r["standardized_score"], 4),
                    "strategy": r["strategy"], "timestamp": r["timestamp"]})
    return out


@app.get("/api/hall-of-fame")
def hall_of_fame(settings_sig: str | None = Query(None)) -> dict:
    """The persistent Hall of Fame for one settings signature (defaults to latest).

    Ranks every strategy ever raced under matching settings by its best
    ``standardized_score``, with margins, best params and a times-tried counter, plus
    a best-score-over-time series for the meta-game progress chart. Runs with
    different settings are never mixed (plan: apples-to-apples only).
    """
    sig = settings_sig or store.latest_settings_sig()
    rows = store.get_runs(settings_sig=sig) if sig else []
    return {
        "settings_sig": sig,
        "n_runs": len(rows),
        "strategies": _hall_of_fame(rows),
        "best_over_time": _best_over_time(rows),
    }


class ValidationRequest(BaseModel):
    """Body for the walk-forward / robustness validators (one strategy at a time)."""

    strategy: str
    asset: str = "BTC"
    start: str | None = DEFAULT_ANALYSIS_START
    end: str | None = None
    capital: float = 10_000.0
    fee_pct: float = 0.00075
    tax: TaxSettings = Field(default_factory=TaxSettings)
    params: dict[str, float] = Field(default_factory=dict)
    n_folds: int = 4
    start_dates: list[str] = Field(default_factory=list)


def _validation_context(req: ValidationRequest):
    """Shared setup for the validators: resolve the strategy + load its history."""
    asset = req.asset.upper()
    if asset not in config.ASSETS:
        raise HTTPException(status_code=404, detail=f"unknown asset '{asset}'")
    try:
        cls = get_strategy(req.strategy)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    history = load_history(asset, req.start, req.end, store=store)
    if len(history) == 0:
        raise HTTPException(status_code=404, detail=f"no data for '{asset}' — refresh first")
    tax_policy = TaxPolicy(enabled=req.tax.enabled, rate=req.tax.rate)
    return cls, history, tax_policy


@app.post("/api/walk-forward")
def walk_forward(req: ValidationRequest) -> dict:
    """Walk-forward validate one strategy: tune in-sample, score out-of-sample, roll."""
    cls, history, tax_policy = _validation_context(req)
    return validation_mod.walk_forward(
        history, cls, n_folds=req.n_folds, fee_pct=req.fee_pct,
        tax_policy=tax_policy, capital=req.capital,
    )


@app.post("/api/robustness")
def robustness(req: ValidationRequest) -> dict:
    """Start-date robustness: re-run one fixed config from several start dates."""
    cls, history, tax_policy = _validation_context(req)
    try:
        params = cls.resolve_params(req.params)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return validation_mod.robustness(
        history, cls, params, start_dates=req.start_dates or None,
        fee_pct=req.fee_pct, tax_policy=tax_policy, capital=req.capital,
    )


# Mount the static frontend LAST. API routes are registered first, so they take
# precedence over this catch-all mount at "/". html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
