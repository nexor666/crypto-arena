"""Tax — an after-tax ranking lens, isolated and removable.

NOT for filing (Kryptosekken does that). This module's only job is to turn the
portfolio's realized-gain events into a running tax-liability series so the race
can show the after-tax curve next to the pre-tax one (plan "Tax modeling").

Model (Norwegian default, fully configurable / jurisdiction-agnostic):
  * **22%** on **net realized gains per calendar year** (``rate`` configurable).
  * Realized **losses offset gains within the same year**; no holding-period
    distinction and **no loss carry-forward** (plan: "keeps it simple").
  * HPFU lot-matching that produces the per-sale realized gains lives in
    ``portfolio.py``; this module only aggregates those gains.
  * Toggleable: ``TaxPolicy(enabled=False)`` makes the after-tax curve equal the
    pre-tax curve (zero liability everywhere).

Running liability at date *d* = (tax finalized for every year before *d*'s year)
+ (tax on the positive net realized gain accrued **so far** within *d*'s year).
So the after-tax curve steps down as gains realize and a within-year loss can give
part of it back — realistic, and it resets the loss-offset each Jan 1.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_RATE = 0.22  # Norway: 22% on net realized capital gains


@dataclass
class TaxPolicy:
    """Configurable tax settings (part of a run's settings signature)."""

    enabled: bool = True
    rate: float = DEFAULT_RATE
    method: str = "HPFU"   # lot-matching method (applied in portfolio.py)

    def signature(self) -> dict:
        return {"enabled": self.enabled, "rate": self.rate, "method": self.method}


def _year(date: str) -> str:
    return date[:4]


def cumulative_tax_series(
    realized_events: list[tuple[str, float]],
    dates: list[str],
    policy: TaxPolicy | None = None,
) -> dict[str, float]:
    """Map each date in ``dates`` -> cumulative tax liability owed by that date.

    ``realized_events`` = ``[(date, realized_gain), ...]`` from the portfolio's
    SELL trades (gains positive, losses negative). ``dates`` is the run's sorted
    daily calendar. Within a year, only the *net positive* gain is taxed; prior
    years are finalized at their year totals.
    """
    policy = policy or TaxPolicy()
    if not policy.enabled or policy.rate <= 0:
        return {d: 0.0 for d in dates}

    # Net realized gain per year, and the cumulative within-year gain timeline.
    # We rebuild per date by scanning events up to each date — simple and exact.
    events = sorted(realized_events, key=lambda e: e[0])
    out: dict[str, float] = {}
    for d in dates:
        per_year: dict[str, float] = {}
        for ed, gain in events:
            if ed > d:
                break
            per_year[_year(ed)] = per_year.get(_year(ed), 0.0) + gain
        # Tax each year's net positive gain (losses offset within the year only).
        liability = sum(max(0.0, net) * policy.rate for net in per_year.values())
        out[d] = liability
    return out


def total_tax_paid(
    realized_events: list[tuple[str, float]],
    policy: TaxPolicy | None = None,
) -> float:
    """Total tax over the whole run = sum over years of 22% * max(0, net gain)."""
    policy = policy or TaxPolicy()
    if not policy.enabled or policy.rate <= 0:
        return 0.0
    per_year: dict[str, float] = {}
    for ed, gain in realized_events:
        per_year[_year(ed)] = per_year.get(_year(ed), 0.0) + gain
    return sum(max(0.0, net) * policy.rate for net in per_year.values())
