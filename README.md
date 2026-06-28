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

The build is staged; the core engine + API are complete and the web UI now
**plays back the race** — press Play and watch the equity curves draw in over
simulated time while the leaderboard re-orders.

| Stage | What | State |
|------:|------|:-----:|
| 0 | Scaffolding & Docker (FastAPI + static frontend + health) | ✅ |
| 1 | Data layer (yfinance OHLCV, Fear & Greed, MVRV → SQLite; indicators; immutable raw snapshots) | ✅ |
| 2 | Backtest engine core (per-lot portfolio, HPFU tax lens, no-look-ahead daily loop, metrics) | ✅ |
| 3 | Full strategy library — 9 strategies + CLI leaderboard | ✅ **(MVP cut line)** |
| 4 | API layer (`/api/strategies`, `/api/events`, `POST /api/backtest`) | ✅ |
| 5 | Frontend core — static price + equity charts | ✅ |
| 6 | The race — playback + live-reordering leaderboard | ✅ |
| 7 | Polish, tuning, winner export, walk-forward + robustness | ⏳ next |
| 8 | Grid-search auto-tuner (stretch) | ⏳ |

**MVP cut line reached at Stage 3:** the CLI can already answer "which strategy
wins" over real history. Stages 4+ add the JSON API and the visual game — as of
Stage 6 the browser UI at <http://localhost:8000> runs a backtest and then
**animates it**: a timeline scrubber, play/pause, speed slider, a price-chart
playhead, equity curves that draw in over time, a current-date + cycle-phase
readout, jump-to-event buttons, and a leaderboard that re-ranks live by portfolio
value as the days advance.

**Strategies (9, auto-discovered plugins, each with a declared param schema):**
Buy & Hold, 200-Week MA, Mayer Multiple, MVRV Z-Score (BTC-only), Fear & Greed,
Halving Cycle Timing, MA Crossover (50/200), Weekly RSI, Periodic Rebalance.

Every strategy is run after-fee and (optionally) after-tax — Norwegian 22% on
realized gains with HPFU lot ordering, fully configurable/removable — and ranked
by a `standardized_score` (after-tax Calmar − turnover penalty) that rewards
risk-adjusted, low-churn returns rather than raw headline gains.

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
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

### Fetch data

Populate the SQLite store from the public feeds (writes an immutable raw snapshot
first, then upserts prices + indicators + Fear & Greed + MVRV):

```bash
python -m backend.data.refresh            # full pull
python -m backend.data.refresh --status   # row counts + date ranges
```

### Race strategies from the CLI

One command races **all** strategies over a window and prints a leaderboard
(after-tax, ranked by `standardized_score`):

```bash
python -m backend.engine.run --asset BTC --start 2018-01-01
python -m backend.engine.run --asset ETH --sort cagr --verbose
python -m backend.engine.run --golden     # Buy & Hold (fee=0) == raw price return
```

### API

| Route | Purpose |
|-------|---------|
| `GET /api/health` | Liveness + current stage |
| `GET /api/data/status` | Store row counts + date ranges |
| `GET /api/price-data/{asset}` | OHLCV + indicators for one asset |
| `GET /api/strategies` | Strategy registry + each one's parameter schema |
| `GET /api/events` | Halving dates + notable cycle tops/bottoms |
| `POST /api/backtest` | Race selected strategies → full per-day equity + trades + metrics + leaderboard |

```bash
# Race every strategy on BTC from 2018 (empty "strategies" = all):
curl -s -X POST localhost:8000/api/backtest \
  -H 'Content-Type: application/json' \
  -d '{"asset":"BTC","start":"2018-01-01","capital":10000}'

# A subset with a parameter override and tax disabled:
curl -s -X POST localhost:8000/api/backtest \
  -H 'Content-Type: application/json' \
  -d '{"asset":"BTC","tax":{"enabled":false},
       "strategies":[{"name":"mayer","params":{"buy_below":0.9}},{"name":"buy_hold"}]}'
```

### Web UI (Stage 6 — the race)

With the app running (Docker or `uvicorn`), open <http://localhost:8000>. Pick an
asset, date range, capital, fee and tax settings, tick the strategies to race, and
hit **Run race**. The page calls `POST /api/backtest` *once* and then animates the
whole result client-side — "compute once, animate in the browser", no streaming:

- a **candlestick price chart** (log-scale) with the 200-Week MA, halving + cycle
  vertical lines, bull/bear regime shading, a sweeping **playback cursor**, and the
  selected strategy's buy/sell markers (pick which strategy's trades to overlay, or
  click a leaderboard row);
- a **transport bar** — Play/Pause, a timeline scrubber, a speed slider
  (simulated days per second), a current-date + bull/bear cycle-phase readout, and
  **jump-to-event** buttons (halvings, cycle tops/bottoms, COVID crash, latest);
- a **multi-line equity chart** that **draws in over simulated time** (pre-/after-tax
  toggle, stable axis so it doesn't jump as lines grow);
- a **live leaderboard** that **re-orders** by each strategy's portfolio value at the
  current day, counting trades up as they happen, with the static `standardized_score`
  shown for reference.

Charts are [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts)
(MIT), loaded from a CDN — no build step.

### Tests

```bash
python -m pytest tests/ -q
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
  frontend/          # UI: index.html, app.js, charts.js, playback.js, styles.css (Stages 5-6)
```
