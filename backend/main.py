"""Crypto Cycle Strategy Arena — FastAPI application entrypoint.

Stage 0: serves a health endpoint and the static frontend. The data layer,
backtest engine, and richer API routes arrive in later stages.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Crypto Cycle Strategy Arena", version="0.0.0")

# Repo layout: backend/main.py  ->  ../frontend
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@app.get("/api/health")
def health() -> dict:
    """Liveness probe. Returns OK so we can confirm the app is up."""
    return {"status": "ok", "service": "crypto-arena", "stage": 0}


# Mount the static frontend LAST. API routes are registered first, so they take
# precedence over this catch-all mount at "/". html=True serves index.html at "/".
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
