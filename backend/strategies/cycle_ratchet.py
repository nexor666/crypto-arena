"""Cycle Ratchet — scale out on new highs, scale in on new lows (plan strategy #10).

This encodes a patient, mechanical version of the classic "scale out of strength,
scale into weakness" cycle method: as price prints **new highs** in the late-bull
distribution window, sell a *fraction* each time (ratcheting out the higher it
goes); after the top, wait; then as price prints **new lows** in the bear
accumulation window, buy a *fraction* each time (ratcheting back in). Hold through
to the next cycle. Naturally low-turnover — it only ever fires on a *genuinely new*
extreme, not on daily noise.

**When (phase gating) keys off the HALVING clock, never a hindsight top/bottom.**
The distribution/accumulation windows are measured in days-since-the-last-halving
(a public, known-in-advance schedule — exactly why the Halving strategy is
legitimate), so there is no look-ahead: the strategy never peeks at where the real
top/bottom landed. An optional Mayer-Multiple gate adds an objective froth/value
filter (sell only when above the 200-day MA, accumulate only when not stretched) in
place of any discretionary "draw a curve and guess the top" call.

**Sizing default = FULL commitment, on purpose.** A repeated fractional sell on each
new high drives the position toward fully out by the top, and a repeated fractional
buy on each new low drives it back to fully in by the bottom. The sell/buy fractions
are tunable knobs so a *timid* version (sell less, keep a bag) can be raced against
the disciplined one — but the default commits fully, because the whole reason to
hand this to a bot is to remove the human hesitation that leaves money on the table.
"""

from __future__ import annotations

from backend.strategies.base import BUY, SELL, Order, ParamSpec, Strategy
from backend.strategies.halving_cycle import _days_since_last_halving


class CycleRatchet(Strategy):
    name = "cycle_ratchet"
    description = "Scale out on new highs (distribution), scale in on new lows (accumulation), gated to the halving clock."
    universe = "single"
    thesis = (
        "A patient, low-turnover cycle method: as price keeps making new highs late in "
        "a bull, sell a slice each time and ratchet out toward fully in cash by the top; "
        "after the top, wait; then as price makes new lows in the bear, buy a slice each "
        "time and ratchet back to fully invested by the bottom. It defaults to FULL "
        "commitment on purpose — the point of automating it is to remove the human "
        "hesitation that leaves you holding half a bag down the whole bear."
    )
    rule = (
        "When the day's close is a new high over the last 90 days AND the calendar is in "
        "the distribution window (~400–700 days after a halving) AND the Mayer Multiple "
        "is ≥ 1.0, sell 50% of the holding. When the close is a new 90-day low AND in the "
        "accumulation window (~750–1350 days after a halving) AND the Mayer Multiple is ≤ "
        "1.2, buy 50% of available cash. All windows, fractions and gates tunable. Phase "
        "timing keys off the known halving schedule — never a hindsight top/bottom."
    )
    triggering = "level"  # fires on each new extreme inside a phase window — naturally low-turnover
    reads = (
        "price (trailing high/low)",
        "the public halving schedule (days since last halving)",
        "Mayer Multiple (froth/value gate)",
    )

    param_schema = {
        "lookback_days": ParamSpec(
            min=7, max=180, step=1, default=90, type="int",
            label="Window for the trailing high/low that defines a 'new' extreme",
        ),
        "sell_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of the holding to sell on each new high (repeated → fully out by the top)",
        ),
        "buy_fraction": ParamSpec(
            min=0.1, max=1.0, step=0.05, default=0.5, type="float",
            label="Fraction of available cash to deploy on each new low (repeated → fully in by the bottom)",
        ),
        "sell_after_min": ParamSpec(
            min=300, max=700, step=10, default=400, type="int",
            label="Start the distribution (sell) window this many days after a halving",
        ),
        "sell_after_max": ParamSpec(
            min=500, max=1000, step=10, default=700, type="int",
            label="End the distribution window this many days after a halving",
        ),
        "buy_after_min": ParamSpec(
            min=600, max=1100, step=10, default=750, type="int",
            label="Start the accumulation (buy) window this many days after a halving (~a year after the top)",
        ),
        "buy_after_max": ParamSpec(
            min=1100, max=1600, step=10, default=1350, type="int",
            label="End the accumulation window this many days after a halving",
        ),
        "sell_min_mayer": ParamSpec(
            min=0.0, max=3.0, step=0.1, default=1.0, type="float",
            label="Only sell when the Mayer Multiple is at/above this — don't sell into weakness (0 = off)",
        ),
        "buy_max_mayer": ParamSpec(
            min=0.5, max=4.0, step=0.1, default=1.2, type="float",
            label="Only buy when the Mayer Multiple is at/below this — accumulate value, not froth (high = off)",
        ),
    }

    def decide(self, date, history, params, portfolio):
        days = _days_since_last_halving(history.today)
        if days is None:
            return [], None  # before the first known halving -> no cycle clock yet

        price = history.price
        if price is None:
            return [], None

        lookback = int(params["lookback_days"])
        prior = [c for c in history.window("close", lookback + 1)[:-1] if c is not None]
        if not prior:
            return [], None  # not enough history yet to define a "new" extreme

        asset = history.asset
        mayer = history.get("mayer")  # None until the 200-day MA warms up

        # Distribution: a NEW high inside the post-halving distribution window → trim.
        if (params["sell_after_min"] <= days <= params["sell_after_max"]
                and price > max(prior) and portfolio.quantity(asset) > 0):
            min_mayer = float(params["sell_min_mayer"])
            if min_mayer <= 0 or mayer is None or mayer >= min_mayer:
                return (
                    [Order(asset, SELL, fraction=float(params["sell_fraction"]))],
                    f"new {lookback}d high, {days}d since halving (distribution) — scale out",
                )

        # Accumulation: a NEW low inside the post-halving accumulation window → buy.
        if (params["buy_after_min"] <= days <= params["buy_after_max"]
                and price < min(prior) and portfolio.cash > 0):
            if mayer is None or mayer <= float(params["buy_max_mayer"]):
                return (
                    [Order(asset, BUY, fraction=float(params["buy_fraction"]))],
                    f"new {lookback}d low, {days}d since halving (accumulation) — scale in",
                )

        return [], None
