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

The build is staged; the core engine + API are complete and the web UI is the full
game — **race, tune, declare a winner, validate it out-of-sample, and export its
config** — backed by a persistent Hall of Fame that accumulates across sessions.

| Stage | What | State |
|------:|------|:-----:|
| 0 | Scaffolding & Docker (FastAPI + static frontend + health) | ✅ |
| 1 | Data layer (yfinance OHLCV, Fear & Greed, MVRV → SQLite; indicators; immutable raw snapshots) | ✅ |
| 2 | Backtest engine core (per-lot portfolio, HPFU tax lens, no-look-ahead daily loop, metrics) | ✅ |
| 3 | Full strategy library — 9 strategies + CLI leaderboard | ✅ **(MVP cut line)** |
| 4 | API layer (`/api/strategies`, `/api/events`, `POST /api/backtest`) | ✅ |
| 5 | Frontend core — static price + equity charts | ✅ |
| 6 | The race — playback + live-reordering leaderboard | ✅ |
| 7 | Polish — param sliders, winner card, JSON export, persistent Hall of Fame, walk-forward + robustness | ✅ |
| 8 | Grid-search auto-tuner (stretch) | ⏳ next |

**MVP cut line reached at Stage 3:** the CLI can already answer "which strategy
wins" over real history. Stages 4+ add the JSON API and the visual game. As of
Stage 7 the browser UI at <http://localhost:8000> is the complete loop:

- **race** all strategies with **per-strategy parameter sliders** (built from each
  strategy's declared schema) and animated playback (scrubber, play/pause, speed,
  jump-to-event, a price-chart playhead, equity curves that draw in, a live
  re-ranking leaderboard) — Stages 5–6;
- a **winner card** that declares the best strategy by the after-tax, risk-adjusted
  `standardized_score` (not raw return), with alpha vs Buy & Hold, tax paid and max
  drawdown, plus a one-click **export of the winner's config as a JSON artifact**
  (the bridge to a future live bot);
- a **persistent Hall of Fame** — every backtest appends to a `runs` ledger, and a
  ranked table (expandable to each strategy's individual configs, with a times-tried
  counter and margins over the next strategy / Buy & Hold) shows the standings for
  the current settings, alongside a ranked-score bar chart and a best-score-over-time
  line as the meta-game accumulates;
- two **out-of-sample honesty checks** — **walk-forward** (tune params on each fold's
  past, score its untouched future, roll forward) and **start-date robustness**
  (re-run from several entry dates) — each returning a robust/fragile verdict so a
  curve-fit "winner" is caught before it's trusted.

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

**New to all this? Don't worry — this section assumes zero prior setup.** There are
two ways to run the app; pick one:

- **🟢 Option A — Just run it (easiest).** Download a ready-made package and start it
  with a single command. No code, no building, data already included. Best for
  trying it out.
- **🔧 Option B — Build it yourself from the source code.** Get the code and build
  the package on your own machine. Best if you want to read or change the code.

**Both options need Docker** — a free tool that runs the app in a self-contained box
so you don't have to install Python, databases, or anything else by hand. Install it
once (Step 1), then follow Option A *or* B.

### Step 1 — Install Docker (one-time)

**On Linux** (Ubuntu, Debian, Fedora, Mint, and most others) — open a terminal and
paste these three lines, one at a time:

```bash
curl -fsSL https://get.docker.com | sh      # installs Docker (asks for your password)
sudo usermod -aG docker $USER               # lets you run docker without sudo
newgrp docker                               # apply that now (or just log out and back in)
```

<details><summary>On Arch / Manjaro / CachyOS instead</summary>

```bash
sudo pacman -S docker                        # install
sudo systemctl enable --now docker           # start it now + on every boot
sudo usermod -aG docker $USER && newgrp docker
```
</details>

**On Windows or macOS:** download **Docker Desktop** from
<https://www.docker.com/products/docker-desktop/>, install it, and open it once so
it's running. Then use the same commands below in PowerShell (Windows) or Terminal
(macOS).

**Check it works** (should print "Hello from Docker!"):

```bash
docker run hello-world
```

> If that last command says "permission denied", you haven't applied the group
> change yet — close the terminal and open a new one (or just reboot), then retry.

### 🟢 Option A — Run the ready-made package (no code, no building)

The published image already contains the app **and** a market-data database, so this
one command downloads it and starts everything:

```bash
docker run -p 8000:8000 ghcr.io/nexor666/crypto-arena
```

The first run downloads the image (a minute or two); after that it's instant. When
it prints that it's running, open your web browser to **<http://localhost:8000>** —
that's the app. (Sanity check: <http://localhost:8000/api/health> should show
`{"status":"ok",...}`.) **To stop it**, press `Ctrl+C` in the terminal.

Your Hall of Fame starts empty and fills as you race strategies. By default those
results disappear when the container is removed; to **keep them** across restarts,
add a storage volume:

```bash
docker run -p 8000:8000 -v arena-data:/app/data ghcr.io/nexor666/crypto-arena
```

### 🔧 Option B — Build it yourself from source

You'll also need **git** (to download the code). Install it if you don't have it:
`sudo apt install git` (Ubuntu/Debian), `sudo pacman -S git` (Arch), or from
<https://git-scm.com> (Windows/macOS). Then:

```bash
git clone https://github.com/nexor666/crypto-arena.git
cd crypto-arena
docker compose up --build
```

That builds the image locally (the build automatically fetches a fresh data
snapshot — needs internet, takes a few minutes the first time) and serves it at
**<http://localhost:8000>**. Stop it with `Ctrl+C`. Your data and Hall of Fame live
in the `data/` folder next to the code, so they persist between runs.

<details><summary>Advanced — run from source without Docker (Python dev)</summary>

The data libraries live in `requirements.txt`; install them into a virtual env.
Call the venv's interpreter directly (works in bash, zsh and fish):

```bash
python -m venv venv
venv/bin/pip install -r requirements.txt
venv/bin/python -m backend.data.refresh        # populate the SQLite store first
venv/bin/uvicorn backend.main:app --reload --port 8000
```

Prefer to "activate" the venv first? `source venv/bin/activate` (bash/zsh) or
`source venv/bin/activate.fish` (fish) — then drop the `venv/bin/` prefixes.
</details>

### Updating the market data

The baked/seeded data is a snapshot from build time. To pull the latest prices,
indicators, Fear & Greed and MVRV (writes an immutable raw snapshot first, then
upserts), refresh in place — no rebuild needed:

```bash
# Running container (any of the above), or via the API:
curl -X POST localhost:8000/api/admin/refresh
docker exec crypto-arena python -m backend.data.refresh   # compose service name

# Local dev:
venv/bin/python -m backend.data.refresh            # full pull
venv/bin/python -m backend.data.refresh --status   # row counts + date ranges
```

> Refreshes persist only if `/app/data` is on a mounted volume (compose does this;
> for `docker run` add `-v arena-data:/app/data`). The published image is also
> rebuilt weekly, so a freshly pulled image already carries recent data.

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
| `POST /api/backtest` | Race selected strategies → full per-day equity + trades + metrics + leaderboard (and append each to the runs ledger) |
| `GET /api/hall-of-fame` | Persistent ledger ranked for one settings signature (best score, configs, times-tried, best-over-time) |
| `POST /api/walk-forward` | Walk-forward validate one strategy: tune in-sample → score out-of-sample, roll forward |
| `POST /api/robustness` | Re-run one fixed config from several start dates → how often it beats Buy & Hold |

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

### Web UI (Stage 7 — the full game)

With the app running (Docker or `uvicorn`), open <http://localhost:8000>. The dates
default to the full window (2018 → latest data), so just tick the strategies to race
(open ⚙ on any strategy to **tune its parameters** with sliders built from its
declared schema) and hit **Run race**. The page calls `POST /api/backtest` *once* and
then plays the whole thing back client-side — "compute once, animate in the browser",
no streaming. It **starts at the starting line**: everyone tied at the opening
capital and the charts blank, so pressing **Play** is a *reveal*, not a replay:

- a **candlestick price chart** (log-scale) whose candles, 200-Week MA, halving +
  cycle lines and bull/bear regime shading **reveal in sync with the clock** — so you
  discover how Bitcoin actually moved at the same moment as the bots, nothing ahead of
  "now" given away. The selected strategy's **buy/sell arrows appear as the playhead
  reaches each trade** (overlay defaults to the winner; switch via the Trades selector
  or by clicking a leaderboard row);
- a **play-by-play feed** — the overlaid strategy's trades scroll past with date,
  side, size and the strategy's own reason as the race reaches them;
- a **transport bar** — Play/Pause, a timeline scrubber, a speed slider (default
  25 simulated days/sec, up to 400), a current-date + bull/bear cycle-phase readout,
  and **jump-to-event** buttons (halvings, cycle tops/bottoms, COVID crash, latest);
- a **multi-line equity chart** that **draws in over simulated time** (pre-/after-tax
  toggle, a clickable legend to show/hide lines, stable axis so it doesn't jump);
- a **live leaderboard** that **re-orders** by each strategy's portfolio value at the
  current day; when the race crosses the **finish line** the **winner card** is
  revealed (best by after-tax `standardized_score`, with alpha vs Buy & Hold, tax
  paid, max drawdown) alongside an **Export JSON** button;
- **Validate the winner** — one click runs **walk-forward** and **start-date
  robustness** and shows a robust/fragile verdict with per-fold / per-start detail;
- a **persistent Hall of Fame** — a ranked table (expandable to each strategy's
  configs, times-tried, margins), a ranked-score bar chart, and a best-score-over-time
  line that grows as you experiment across sessions.

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
  Dockerfile               # bakes a fresh seed DB into the image at build time
  docker-entrypoint.sh     # seeds /app/data on first boot (else live fetch)
  .github/workflows/       # CI: build + publish the image to GHCR (weekly rebuild)
  requirements.txt
  data/              # SQLite db + raw cache (mounted volume, gitignored)
  backend/
    main.py          # FastAPI app + routes + static mount
    data/            # fetchers, indicators, sqlite store + runs ledger (Stages 1, 7)
    engine/          # portfolio, tax, backtest loop, metrics, ledger, validation (Stages 2, 7)
    strategies/      # strategy plugins + registry (Stages 2-3)
  frontend/          # UI: index.html, app.js, charts.js, playback.js, arena_extras.js, styles.css (Stages 5-7)
```
