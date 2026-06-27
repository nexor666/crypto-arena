"""Capital models — a swappable policy for how money enters a run.

Plan modularity principle 5: the money-injection schedule is pluggable, so the set
of capital models extends without touching the engine. The engine only ever asks a
model for ``schedule(dates) -> {date: amount}`` and feeds each amount to
``Portfolio.add_cash`` on that day (the cash-injection hook).

Stage 2 exposes **LumpSum only** (the default). DCA / phased entry are written
against the same interface later; they just return a richer schedule and set
``adds_money_over_time`` so metrics switch to money-weighted return (IRR).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class CapitalModel(ABC):
    name: str = "base"
    adds_money_over_time: bool = False   # True -> metrics use IRR, not plain CAGR

    @abstractmethod
    def schedule(self, dates: list[str]) -> dict[str, float]:
        """Return ``{date: amount}`` of cash to inject on each given run date."""
        ...

    def total_contributed(self, dates: list[str]) -> float:
        return sum(self.schedule(dates).values())

    def signature(self) -> dict:
        return {"model": self.name}


class LumpSum(CapitalModel):
    """All capital deposited once, on the first day of the run (the default)."""

    name = "lump_sum"
    adds_money_over_time = False

    def __init__(self, amount: float) -> None:
        self.amount = float(amount)

    def schedule(self, dates: list[str]) -> dict[str, float]:
        if not dates:
            return {}
        return {dates[0]: self.amount}

    def signature(self) -> dict:
        return {"model": self.name, "amount": self.amount}
