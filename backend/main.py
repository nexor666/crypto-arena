"""Crypto Cycle Strategy Arena — FastAPI application entrypoint.

Stage 1: the data layer is wired in. Beyond the health probe and the static
frontend, the API can refresh the SQLite store from the public feeds and serve
back the stored series. The backtest engine and richer routes arrive later.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from backend import config
from backend.data.refresh import refresh as run_refresh
from backend.data.store import Store

app = FastAPI(title="Crypto Cycle Strategy Arena", version="0.1.0")

# Repo layout: backend/main.py  ->  ../frontend
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# One Store instance is fine: each call opens its own short-lived connection.
store = Store()


@app.get("/api/health")
def health() -> dict:
    """Liveness probe. Returns OK so we can confirm the app is up."""
    return {"status": "ok", "service": "crypto-arena", "stage": 1}


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


# Mount the static frontend LAST. API routes are registered first, so they take
# precedence over this catch-all mount at "/". html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
