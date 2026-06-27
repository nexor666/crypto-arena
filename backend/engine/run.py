"""CLI race — Stage 2 "done when" entrypoint.

Runs the implemented strategies over an asset/window and prints pre- and after-tax
final value + metrics for each, plus the Buy & Hold golden check (with fees off,
B&H's pre-tax return must equal the asset's raw price change — proving no
look-ahead and no fee bug).

    python -m backend.engine.run                       # BTC, default 2018-> window
    python -m backend.engine.run --asset ETH --start 2020-01-01 --end 2023-01-01
    python -m backend.engine.run --fee 0.00075 --capital 10000 --no-tax
"""

from __future__ import annotations

import argparse

from backend.engine.backtest import load_history, run_backtest
from backend.engine.capital import LumpSum
from backend.engine.tax import TaxPolicy
from backend.strategies.base import registry

DEFAULT_START = "2018-01-01"   # the plan's default analysis window


def _fmt_usd(x: float) -> str:
    return f"${x:,.2f}"


def _fmt_pct(x: float) -> str:
    return f"{x * 100:,.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser(description="Race Stage-2 strategies over history.")
    ap.add_argument("--asset", default="BTC")
    ap.add_argument("--start", default=DEFAULT_START)
    ap.add_argument("--end", default=None)
    ap.add_argument("--capital", type=float, default=10_000.0)
    ap.add_argument("--fee", type=float, default=0.00075, help="trading fee fraction (0.00075 = 0.075%%)")
    ap.add_argument("--tax-rate", type=float, default=0.22)
    ap.add_argument("--no-tax", action="store_true", help="disable the after-tax lens")
    args = ap.parse_args()

    asset = args.asset.upper()
    history = load_history(asset, args.start, args.end)
    if len(history) == 0:
        raise SystemExit(f"No data for {asset} in {args.start}..{args.end} — run a refresh first.")

    tax_policy = TaxPolicy(enabled=not args.no_tax, rate=args.tax_rate)
    reg = registry()

    print(f"\nCrypto Arena — Stage 2 backtest")
    print(f"asset={asset}  window={history.dates[0]}..{history.dates[-1]}  "
          f"days={len(history)}  capital={_fmt_usd(args.capital)}  "
          f"fee={args.fee * 100:.3f}%  tax={'off' if args.no_tax else f'{args.tax_rate * 100:.0f}%'}")
    print("=" * 78)

    results = {}
    for name in sorted(reg):
        strat = reg[name]()
        result = run_backtest(
            history, strat,
            capital_model=LumpSum(args.capital),
            fee_pct=args.fee,
            tax_policy=tax_policy,
        )
        results[name] = result
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

    # -- Golden check: B&H with fees OFF == raw price return ---------------
    print("\n" + "=" * 78)
    print("Golden check — Buy & Hold (fee=0, tax off) vs raw price change")
    bh = reg["buy_hold"]()
    bh_nofee = run_backtest(
        history, bh,
        capital_model=LumpSum(args.capital),
        fee_pct=0.0,
        tax_policy=TaxPolicy(enabled=False),
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
