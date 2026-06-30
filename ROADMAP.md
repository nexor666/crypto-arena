# Roadmap & build log

Crypto Cycle Strategy Arena is built in stages — one self-contained chunk at a
time. This file tracks what's shipped and what's planned. For how to **install and
use** it, see the [README](README.md).

## Stages

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
| 8 | Grid-search auto-tuner — per-strategy params optimized **out-of-sample** | ✅ |
| 9 | Strategy transparency — plain-English info panels + data-source provenance | ✅ |
| 10 | "Cycle Ratchet" strategy (scale out on new highs / in on new lows, gated to halving phase) + gradual-scaling defaults | ✅ |
| 11 | Custom-strategy builder (declarative rule DSL, no code) + MACD indicator | 🔜 planned |
| 12 | UX & quality batch + more assets | 🔜 planned |

**MVP cut line reached at Stage 3:** the CLI can already answer "which strategy
wins" over real history. Stages 4+ add the JSON API and the visual game.

## What the full game does (Stages 5–9)

As of Stage 9 the browser UI at <http://localhost:8000> is the complete loop:

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
  curve-fit "winner" is caught before it's trusted;
- a per-strategy **grid-search auto-tuner** (Stage 8) — one ✨ click brute-forces a
  parameter grid scored only on **out-of-sample** windows (never the data it'll be
  judged on, which would just maximize overfitting), drops the best params straight
  into the sliders, and tells you honestly whether they actually **beat the
  hand-picked defaults on an untouched hold-out window** (the answer is allowed to be
  "no" — a strategy with no real edge can't be tuned into one);
- **full transparency** (Stage 9) — every strategy has an ⓘ info panel that explains,
  in plain English, what it does, its exact rule, whether it's **level-triggered**
  (acts every day its condition holds — why some trade counts get large),
  **edge-triggered** (acts once when its signal flips) or **scheduled**, and which data
  it reads; plus a "where does the data come from?" note listing each source
  (provider + live coverage window), so nothing is a black box.

## How "winning" is defined

Every strategy is run after-fee and (optionally) after-tax — a configurable flat
rate (default 22%) on realized gains with HPFU lot ordering, fully removable — and
ranked by a `standardized_score` (after-tax Calmar − turnover penalty) that rewards
risk-adjusted, low-churn returns rather than raw headline gains. The whole point is
to reward strategies that grow money steadily without big drawdowns or constant
churning, not the ones with the flashiest top-line number.

## The strategy library

10 strategies, auto-discovered plugins, each with a declared parameter schema and a
plain-English info panel in the UI:

Buy & Hold, 200-Week MA, Mayer Multiple, MVRV Z-Score (BTC-only), Fear & Greed,
Halving Cycle Timing, MA Crossover (50/200), Weekly RSI, Periodic Rebalance, and the
**Cycle Ratchet** — scale out a fraction on each new high in the post-halving
distribution window and scale in on each new low in the accumulation window, gated to
the known halving clock (never a hindsight top/bottom), defaulting to full commitment.

Adding a strategy is one file: drop a `Strategy` subclass into `backend/strategies/`
and it appears in the CLI, the API and the UI automatically.
