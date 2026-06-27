# Crypto Cycle Strategy Arena

A backtesting "game" that races long-term crypto trading strategies against each
other through real historical price data (BTC / ETH / BNB, daily), visualised as
a live race across full bull/bear cycles. You tune strategy parameters, watch
them compete, and discover **which strategy actually wins** by a risk-adjusted,
after-tax, walk-forward-validated definition — then export the winner's config as
a clean JSON artifact for a future live-trading bot.

**Scope:** the game only (backtesting + visualisation). No live trading, no
exchange keys, no real money. Focus is long-term halving-cycle strategies, not
day trading.

## Status

**Stage 0 — scaffolding & Docker.** FastAPI app with a health endpoint and static
frontend serving. The data layer, backtest engine, strategy library, and the
visual race arrive in later stages (see the build plan).

## Tech stack

- **Backend:** Python 3.12 + FastAPI (serves the JSON API and the static frontend)
- **Data/cache:** SQLite (prices, indicators, Fear & Greed, MVRV)
- **Engine:** custom, pandas-backed daily loop with strict no-look-ahead
- **Frontend:** plain HTML/CSS/vanilla JS + TradingView Lightweight Charts
- **Container:** Docker / docker-compose, single image, one port

## Run

With Docker:

```bash
docker compose up --build
```

Then open <http://localhost:8000> and check <http://localhost:8000/api/health>.

Without Docker (local dev):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

## Project structure

```
crypto-arena/
  docker-compose.yml
  Dockerfile
  requirements.txt
  data/              # SQLite db + raw cache (mounted volume, gitignored)
  backend/
    main.py          # FastAPI app + routes + static mount
    data/            # fetchers, indicators, sqlite store (Stage 1)
    engine/          # portfolio, tax, backtest loop, metrics (Stage 2)
    strategies/      # strategy plugins + registry (Stages 2-3)
  frontend/          # index.html, app.js, charts.js, styles.css
```
