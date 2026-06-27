"""CLI race — the Stage-3 "done when": one command races ALL strategies over the
full history and prints a leaderboard table.

Each strategy in the registry is run (lump-sum, configurable fee + tax) over the
chosen asset/window, then ranked by the pinned ``standardized_score``
(``Calmar_after_tax − turnover_penalty``) or by after-tax CAGR. The detailed
per-strategy dump from Stage 2 is still available via ``--verbose``, and the Buy &
Hold golden check (fee=0 pre-tax return == raw price change) via ``--golden``.

    python -m backend.engine.run                          # BTC, default 2018-> window
    python -m backend.engine.run --asset ETH --start 2020-01-01 --end 2023-01-01
    python -m backend.engine.run --sort cagr --verbose --golden
    python -m backend.engine.run --fee 0.001 --capital 10000 --no-tax
"""

from __future__ import annotations

import argparse

from backend.engine import metrics as metrics_mod
from backend.engine.backtest import load_history, run_backtest
from backend.engine.capital import LumpSum
from backend.engine.tax import TaxPolicy
from backend.strategies.base import registry

DEFAULT_START = "2018-01-01"   # the plan's default analysis window


def _fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:,.1f}%"


def _print_verbose(name: str, result) -> None:
    pre, post = result.metrics_pretax, result.metrics_aftertax
    print(f"\n▶ {name}   params={result.params}")
    print(f"   final value      pre-tax {_fmt_usd(pre.final_value):>16}   "
          f"after-tax {_fmt_usd(post.final_value):>16}")
    print(f"   total return     pre-tax {_fmt_pct(pre.total_return_pct):>16}   "
          f"after-tax {_fmt_pct(post.total_return_pct):>16}")
    print(f"   CAGR             pre-tax {_fmt_pct(pre.cagr):>16}   "
          f"after-tax {_fmt_pct(post.cagr):>16}")
    print(f"   max drawdown     {_fmt_pct(post.max_drawdown):>24}")
    print(f"   Sharpe / Sortino {post.sharpe:>10.2f} / {post.sortino:.2f}")
    print(f"   trades / turnover / %in-market   "
          f"{post.n_trades}  /  {post.turnover:.2f}x  /  {_fmt_pct(post.pct_time_in_market)}")
    print(f"   total tax paid   {_fmt_usd(post.total_tax_paid):>24}")


def _print_leaderboard(rows: list[dict], sort_key: str) -> None:
    label = "score" if sort_key == "score" else "aftertax CAGR"
    header = (
        f"{'#':>2}  {'strategy':<14}{'after-tax $':>15}{'aftertax%':>11}"
        f"{'aftxCAGR':>10}{'maxDD':>9}{'Sharpe':>8}{'trades':>7}"
        f"{'turn':>7}{'tax $':>12}{'score':>9}"
    )
    print(f"\nLeaderboard (ranked by {label}, after-tax)")
    print("=" * len(header))
    print(header)
    print("-" * len(header))
    for rank, r in enumerate(rows, 1):
        print(
            f"{rank:>2}  {r['name']:<14}{_fmt_usd(r['final']):>15}"
            f"{_fmt_pct(r['ret']):>11}{_fmt_pct(r['cagr']):>10}"
            f"{_fmt_pct(r['max_dd']):>9}{r['sharpe']:>8.2f}{r['trades']:>7}"
            f"{r['turnover']:>6.1f}x{_fmt_usd(r['tax']):>12}{r['score']:>9.2f}"
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Race ALL strategies over history -> leaderboard.")
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--fee", type=float, default=0.00075, help="trading fee fraction (0.00075 = 0.075%%)")
    ap.add_argument("--tax-rate", type=float, default=0.22)
    ap.add_argument("--no-tax", action="store_true", help="disable the after-tax lens")
    ap.add_argument("--sort", choices=["score", "cagr"], default="score",
                    help="rank by standardized_score (default) or after-tax CAGR")
    ap.add_argument("--verbose", action="store_true", help="also print each strategy's full metrics")
    ap.add_argument("--golden", action="store_true", help="run the Buy & Hold golden check")
    args = ap.parse_args()

    asset = args.asset.upper()
    history = load_history(asset, args.start, args.end)
    if len(history) == 0:
        raise SystemExit(f"No data for {asset} in {args.start}..{args.end} — run a refresh first.")

    tax_policy = TaxPolicy(enabled=not args.no_tax, rate=args.tax_rate)
    reg = registry()

    print(f"\nCrypto Arena — Stage 3 strategy race")
    print(f"asset={asset}  window={history.dates[0]}..{history.dates[-1]}  "
          f"days={len(history)}  capital={_fmt_usd(args.capital)}  "
          f"fee={args.fee * 100:.3f}%  tax={'off' if args.no_tax else f'{args.tax_rate * 100:.0f}%'}  "
          f"strategies={len(reg)}")

    rows = []
    for name in sorted(reg):
        strat = reg[name]()
        result = run_backtest(
            history, strat,
            capital_model=LumpSum(args.capital),
            fee_pct=args.fee,
            tax_policy=tax_policy,
        )
        if args.verbose:
            _print_verbose(name, result)
        post = result.metrics_aftertax
        rows.append({
            "name": name,
            "final": post.final_value,
            "ret": post.total_return_pct,
            "cagr": post.cagr,
            "max_dd": post.max_drawdown,
            "sharpe": post.sharpe,
            "trades": post.n_trades,
            "turnover": post.turnover,
            "tax": post.total_tax_paid,
            "score": metrics_mod.standardized_score(post),
        })

    rows.sort(key=lambda r: r["score"] if args.sort == "score" else r["cagr"], reverse=True)
    _print_leaderboard(rows, args.sort)

    if args.golden:
        print("\n" + "=" * 78)
        print("Golden check — Buy & Hold (fee=0, tax off) vs raw price change")
        bh_nofee = run_backtest(
            history, reg["buy_hold"](),
            capital_model=LumpSum(args.capital),
            fee_pct=0.0, tax_policy=TaxPolicy(enabled=False),
        )
        start_price = history._records[0]["close"]
        end_price = history._records[-1]["close"]
        price_return = end_price / start_price - 1.0
        bh_return = bh_nofee.metrics_pretax.total_return_pct
        diff = abs(bh_return - price_return)
        print(f"   price {_fmt_usd(start_price)} -> {_fmt_usd(end_price)}   "
              f"price return = {_fmt_pct(price_return)}")
        print(f"   B&H pre-tax return (fee=0) = {_fmt_pct(bh_return)}   "
              f"|diff| = {diff:.2e}   {'PASS ✅' if diff < 1e-9 else 'FAIL ❌'}")
    print()


if __name__ == "__main__":
    main()
