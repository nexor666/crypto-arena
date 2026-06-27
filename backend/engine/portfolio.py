"""Portfolio — cash, per-lot holdings, fees, and HPFU realized-gain accounting.

This is the *pre-tax* simulation state. Tax is deliberately NOT applied here: it
is a downstream lens (see ``tax.py`` / ``metrics.py``) that consumes the realized-
gain events this module records, so the core simulation never changes when tax is
toggled on/off (plan modularity principle 6).

Cost-basis model (plan "Tax modeling"):
  * Every BUY creates a **lot** whose per-unit cost INCLUDES the buy fee — so the
    fee is capitalized into basis, exactly as a real cost-basis would treat it.
  * Every SELL is matched against lots **highest-cost-first (HPFU = Høyeste Pris
    Først Ut)**, which minimizes the realized gain per sale (matches the user's
    real Kryptosekken filing). Sale proceeds are net of the sell fee, so the
    realized gain already accounts for both the buy and sell costs.

Fee convention (a single configurable ``fee_pct``, e.g. 0.00075 = 0.075%):
  * BUY of USD notional ``N`` (value of coins acquired): fee = N*fee_pct,
    cash out = N + fee, lot basis = N + fee for ``N/price`` coins.
  * SELL of ``q`` coins at ``price``: gross = q*price, fee = gross*fee_pct,
    cash in = gross - fee.
With ``fee_pct == 0`` a buy-and-never-sell run reproduces the raw price return
exactly — the Stage-2 golden check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Lot:
    """One acquired parcel of an asset (FIFO/HPFU bookkeeping unit)."""

    qty: float                  # coins remaining in this lot
    cost_per_unit: float        # USD basis per coin (INCLUDES the buy fee)
    acquired: str               # ISO date the lot was opened


@dataclass
class Trade:
    """A recorded executed trade (the snapshot/metrics/tax stream consumes these)."""

    date: str
    asset: str
    side: str                   # BUY | SELL
    qty: float                  # coins traded
    price: float                # execution price (daily close)
    fee: float                  # USD fee paid
    proceeds: float             # USD cash delta magnitude (out for BUY, in for SELL)
    realized_gain: float = 0.0  # SELL only: proceeds(net of fee) - basis of lots sold
    reason: str | None = None


@dataclass
class CashFlow:
    """An external cash injection (capital-model contribution). Day-0 deposit too."""

    date: str
    amount: float


class Portfolio:
    """Mutable pre-tax portfolio state advanced by the daily loop."""

    def __init__(self, fee_pct: float = 0.0) -> None:
        self.fee_pct = float(fee_pct)
        self.cash: float = 0.0
        self.lots: dict[str, list[Lot]] = {}    # asset -> open lots
        self.trades: list[Trade] = []
        self.cashflows: list[CashFlow] = []     # external money in (for IRR/metrics)

    # -- cash / capital-model hook -----------------------------------------
    def add_cash(self, amount: float, date: str) -> None:
        """Inject external cash (initial deposit OR a DCA/phased contribution).

        This is the single hook every capital model feeds. Recorded as a CashFlow
        so money-weighted return (IRR) can distinguish contributions from gains.
        """
        if amount <= 0:
            return
        self.cash += amount
        self.cashflows.append(CashFlow(date=date, amount=amount))

    # -- holdings views -----------------------------------------------------
    def quantity(self, asset: str) -> float:
        return sum(lot.qty for lot in self.lots.get(asset, ()))

    def assets_held(self) -> list[str]:
        return [a for a in self.lots if self.quantity(a) > 0]

    def market_value(self, prices: dict[str, float]) -> float:
        """USD value of all holdings at the given per-asset prices."""
        total = 0.0
        for asset, lots in self.lots.items():
            price = prices.get(asset)
            if price is None:
                continue
            total += price * sum(lot.qty for lot in lots)
        return total

    def total_value(self, prices: dict[str, float]) -> float:
        """Pre-tax mark-to-market: cash + holdings."""
        return self.cash + self.market_value(prices)

    # -- order execution ----------------------------------------------------
    def execute(
        self, order: "Any", prices: dict[str, float], date: str,
        reason: str | None = None,
    ) -> Trade | None:
        """Apply one Order at today's ``prices``; return the Trade (or None).

        Sizing modes (Order.fraction / .quote / .base) are resolved to a concrete
        coin quantity here; the rest of the method is sizing-agnostic.
        """
        price = prices.get(order.asset)
        if price is None or price <= 0:
            return None  # no tradable price today -> skip (no peeking, no crash)

        if order.side == "BUY":
            spend = self._buy_notional(order, price)        # USD value of coins (pre-fee)
            return self._buy(order.asset, spend, price, date, reason)
        else:
            qty = self._sell_quantity(order, price)
            return self._sell(order.asset, qty, price, date, reason)

    # -- buy ----------------------------------------------------------------
    def _buy_notional(self, order: "Any", price: float) -> float:
        """USD value of coins to acquire (excluding fee), from the order's sizing."""
        if order.quote is not None:
            return float(order.quote)
        if order.base is not None:
            return float(order.base) * price
        # fraction of available cash, fee-inclusive: spend f*cash total, of which
        # fee is part — so notional N satisfies N*(1+fee_pct) = f*cash.
        budget = float(order.fraction) * self.cash
        return budget / (1.0 + self.fee_pct)

    def _buy(
        self, asset: str, notional: float, price: float, date: str,
        reason: str | None,
    ) -> Trade | None:
        if notional <= 0:
            return None
        fee = notional * self.fee_pct
        total_cost = notional + fee
        # Clamp to available cash (floating-point / over-ask safety).
        if total_cost > self.cash:
            total_cost = self.cash
            notional = total_cost / (1.0 + self.fee_pct)
            fee = total_cost - notional
        if notional <= 0:
            return None
        qty = notional / price
        if qty <= 0:
            return None
        self.cash -= total_cost
        cost_per_unit = total_cost / qty            # capitalizes the fee into basis
        self.lots.setdefault(asset, []).append(
            Lot(qty=qty, cost_per_unit=cost_per_unit, acquired=date)
        )
        trade = Trade(
            date=date, asset=asset, side="BUY", qty=qty, price=price,
            fee=fee, proceeds=total_cost, realized_gain=0.0, reason=reason,
        )
        self.trades.append(trade)
        return trade

    # -- sell (HPFU) --------------------------------------------------------
    def _sell_quantity(self, order: "Any", price: float) -> float:
        held = self.quantity(order.asset)
        if order.base is not None:
            return min(float(order.base), held)
        if order.quote is not None:
            return min(float(order.quote) / price, held)
        return float(order.fraction) * held

    def _sell(
        self, asset: str, qty: float, price: float, date: str,
        reason: str | None,
    ) -> Trade | None:
        lots = self.lots.get(asset, [])
        held = sum(lot.qty for lot in lots)
        qty = min(qty, held)
        if qty <= 0:
            return None

        gross = qty * price
        fee = gross * self.fee_pct
        net_proceeds = gross - fee

        # HPFU: consume lots highest cost-per-unit first to minimize realized gain.
        basis_sold = self._consume_lots_hpfu(asset, qty)
        realized_gain = net_proceeds - basis_sold

        self.cash += net_proceeds
        trade = Trade(
            date=date, asset=asset, side="SELL", qty=qty, price=price,
            fee=fee, proceeds=net_proceeds, realized_gain=realized_gain, reason=reason,
        )
        self.trades.append(trade)
        return trade

    def _consume_lots_hpfu(self, asset: str, qty: float) -> float:
        """Remove ``qty`` coins from the highest-cost lots; return USD basis removed."""
        lots = self.lots.get(asset, [])
        # Highest cost-per-unit first (HPFU).
        order = sorted(range(len(lots)), key=lambda i: lots[i].cost_per_unit, reverse=True)
        remaining = qty
        basis = 0.0
        for i in order:
            if remaining <= 1e-18:
                break
            lot = lots[i]
            take = min(lot.qty, remaining)
            basis += take * lot.cost_per_unit
            lot.qty -= take
            remaining -= take
        # Drop emptied lots.
        self.lots[asset] = [lot for lot in lots if lot.qty > 1e-18]
        return basis
