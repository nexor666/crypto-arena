"""Central configuration for the data layer (and everything downstream).

Config-driven by design (plan modularity principle 4): assets, date ranges, and
source URLs live here, never hardcoded in the fetchers/engine. Adding a coin is a
one-line edit to ``ASSETS`` (provided the provider has data) — no code change.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# config.py lives in backend/, so parent.parent is the repo root. In Docker the
# backend is at /app/backend, making DATA_DIR resolve to /app/data — the path the
# compose file bind-mounts so the SQLite db + raw cache survive rebuilds.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"          # immutable raw-fetch snapshots (never overwritten)
DB_PATH = DATA_DIR / "arena.db"     # single SQLite file: prices, indicators, F&G, MVRV

# ---------------------------------------------------------------------------
# Assets — internal symbol -> Yahoo Finance ticker (used by yfinance)
# ---------------------------------------------------------------------------
# Start the universe at BTC/ETH/BNB; extensible to the top-20 by adding rows.
ASSETS: dict[str, str] = {
    "BTC": "BTC-USD",
    "ETH": "ETH-USD",
    "BNB": "BNB-USD",
}

# ---------------------------------------------------------------------------
# Date range
# ---------------------------------------------------------------------------
# Pull ALL available history into the DB (it's only a few MB). The ~2018 default
# *analysis* window is a run-time config lever applied later by the engine — the
# data layer keeps everything (BTC ~2014, ETH ~2016, BNB ~2017) for optional
# long-vs-short regime stress-tests. So fetch from as early as possible.
FETCH_START = "2014-01-01"

# ---------------------------------------------------------------------------
# Source URLs (behind the DataProvider adapters in fetchers.py)
# ---------------------------------------------------------------------------
# alternative.me Fear & Greed: limit=0 returns the full history (since Feb 2018).
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=0&format=json"

# BGeometrics / bitcoin-data.com — free BTC on-chain MVRV Z-Score.
# NOTE: the free tier caps the response at the most recent ~1461 rows (~4 years),
# regardless of date params. We store whatever it returns; MVRV is BTC-only and
# optional, and cross-strategy ranking only runs on the date intersection where
# every compared strategy has data (plan honest-limits rule), so partial coverage
# degrades gracefully rather than corrupting results.
MVRV_URL = "https://bitcoin-data.com/v1/mvrv-zscore"

# Polite identifier for the HTTP-API providers (F&G, MVRV).
HTTP_USER_AGENT = "crypto-arena/0.1 (+research backtester; non-commercial)"
HTTP_TIMEOUT = 30  # seconds

# ---------------------------------------------------------------------------
# Bitcoin halving dates (UTC, the block-reward halvings)
# ---------------------------------------------------------------------------
# Halvings are a deterministic, publicly-scheduled supply event — known years in
# advance — so a strategy keying off "days since/until a halving" uses no
# look-ahead (everyone knew these dates before they happened). Used by the Halving
# Cycle Timing strategy. The 2028 entry is the scheduled next halving (~block
# 1,050,000); a rough estimate, only relevant once the data extends near it.
HALVING_DATES: list[str] = [
    "2012-11-28",
    "2016-07-09",
    "2020-05-11",
    "2024-04-20",
    "2028-04-01",   # scheduled estimate (refine when it lands)
]

# ---------------------------------------------------------------------------
# Notable cycle events — jump-to markers for the frontend (Stage 4 /api/events,
# Stage 6 race buttons)
# ---------------------------------------------------------------------------
# These are well-known *historical* BTC cycle tops/bottoms used purely as UI
# navigation/shading markers — NOT inputs to any strategy, so they carry no
# look-ahead concern (a strategy never sees them). Dates are the widely-cited
# close-to-close extremes; ``kind`` lets the chart colour them.
NOTABLE_EVENTS: list[dict] = [
    {"date": "2017-12-17", "label": "2017 top", "kind": "top"},
    {"date": "2018-12-15", "label": "2018 bottom", "kind": "bottom"},
    {"date": "2020-03-13", "label": "COVID crash", "kind": "crash"},
    {"date": "2021-11-10", "label": "2021 top", "kind": "top"},
    {"date": "2022-11-21", "label": "2022 bottom", "kind": "bottom"},
]
