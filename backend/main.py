"""Crypto Cycle Strategy Arena — FastAPI application entrypoint.

Stage 4: the API layer over the engine. On top of the Stage-1 data routes, the
app now exposes the strategy registry + their param schemas (``/api/strategies``),
the cycle event markers (``/api/events``), and the engine itself via
``POST /api/backtest`` — which races the selected strategies over a chosen
window and returns the full per-day equity stream + trades + metrics for each, so
the (Stage-5) frontend can render and animate the race entirely client-side.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config
from backend.data.refresh import refresh as run_refresh
from backend.data.store import Store
from backend.engine import metrics as metrics_mod
from backend.engine.backtest import SCHEMA_VERSION, load_history, run_backtest
from backend.engine.capital import LumpSum
from backend.engine.tax import TaxPolicy
from backend.strategies.base import registry

# The plan's default *analysis* window (Fear & Greed has full coverage from here,
# so every strategy is comparable). Callers may override per request.
DEFAULT_ANALYSIS_START = "2018-01-01"

app = FastAPI(title="Crypto Cycle Strategy Arena", version="0.1.0")

# Repo layout: backend/main.py  ->  ../frontend
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# One Store instance is fine: each call opens its own short-lived connection.
store = Store()


@app.get("/api/health")
def health() -> dict:
    """Liveness probe. Returns OK so we can confirm the app is up."""
    return {"status": "ok", "service": "crypto-arena", "stage": 4}


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
            capital_model=LumpSum(req.capital),
            fee_pct=req.fee_pct, tax_policy=tax_policy,
        )
        payload = result.as_dict()
        payload["score"] = metrics_mod.standardized_score(result.metrics_aftertax)
        results.append(payload)

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
        "start": history.dates[0],
        "end": history.dates[-1],
        "capital": req.capital,
        "fee_pct": req.fee_pct,
        "tax": tax_policy.signature(),
        "leaderboard": leaderboard,
        "results": results,
    }


# Mount the static frontend LAST. API routes are registered first, so they take
# precedence over this catch-all mount at "/". html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
