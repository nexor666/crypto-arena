"""Strategy contract — the pinned interface every strategy (and the future live
bot) conforms to.

Plan contract (do NOT drift):

    decide(date, history_up_to_date, params, portfolio) -> (orders, reason?)

plus a declared ``param_schema`` where every parameter pins ``min``, ``max``,
``step``, ``default`` (and ``type``). The frontend builds its sliders from this
schema (Stage 5) and the grid-search walks it (Stage 8) — a slider/grid is
undefined without bounds, so bounds are mandatory, not optional.

``reason`` is reserved-but-optional (plan): a short human string explaining what
triggered the decision. We carry it through the contract so adding trade
explainability later never breaks the interface; default ``None``.

Strategies are auto-discovered plugins (plan modularity principle 1): one file per
strategy, each subclassing ``Strategy``; importing the ``strategies`` package
registers them all (see ``registry()``), so adding a strategy = drop in one file.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # avoid import cycles; these are only type hints
    from backend.engine.history import History
    from backend.engine.portfolio import Portfolio


# ---------------------------------------------------------------------------
# Orders — what a strategy asks the engine to do
# ---------------------------------------------------------------------------
BUY = "BUY"
SELL = "SELL"


@dataclass
class Order:
    """A single trade request for one asset.

    Sizing is expressed by exactly ONE of the mutually exclusive modes below.
    ``fraction`` is the natural unit for cycle strategies ("deploy half my cash",
    "trim a third of the position") and is portfolio-relative, so it stays correct
    as the balance changes. ``quote`` (absolute USD notional) and ``base``
    (absolute coin quantity) are provided for strategies/live-bot cases that need
    them; more sizing modes (e.g. target-weight rebalancing) can be added later
    without changing this contract.

    fraction semantics:
        BUY  -> fraction of currently AVAILABLE CASH to spend
        SELL -> fraction of the CURRENT HOLDING (in that asset) to sell
    """

    asset: str
    side: str                       # BUY | SELL
    fraction: float | None = None   # 0..1, portfolio-relative (see above)
    quote: float | None = None      # absolute USD notional to trade
    base: float | None = None       # absolute coin quantity to trade

    def __post_init__(self) -> None:
        if self.side not in (BUY, SELL):
            raise ValueError(f"Order.side must be BUY or SELL, got {self.side!r}")
        modes = [m for m in (self.fraction, self.quote, self.base) if m is not None]
        if len(modes) != 1:
            raise ValueError(
                "Order must specify exactly one of fraction/quote/base "
                f"(got {len(modes)})"
            )
        if self.fraction is not None and not (0.0 <= self.fraction <= 1.0):
            raise ValueError(f"Order.fraction must be in [0, 1], got {self.fraction}")


# Type alias for what decide() returns: orders + an optional reason.
DecisionResult = tuple[list[Order], str | None]


# ---------------------------------------------------------------------------
# Strategy base class
# ---------------------------------------------------------------------------
@dataclass
class ParamSpec:
    """Bounds for one tunable parameter (frontend slider + grid-search source)."""

    min: float
    max: float
    step: float
    default: float
    type: str = "float"   # "float" | "int"
    label: str | None = None


class Strategy(ABC):
    """Base class for all strategies.

    Subclasses set ``name``, ``param_schema`` (a ``{param: ParamSpec}`` mapping),
    and ``universe`` ("single" coin ↔ cash by default; "rotation" for multi-coin),
    then implement :meth:`decide`.
    """

    name: str = "base"
    description: str = ""
    universe: str = "single"               # "single" | "rotation"
    param_schema: dict[str, ParamSpec] = {}

    # -- transparency metadata (Stage 9) ------------------------------------
    # A plain-English window into the strategy so the UI can explain it without
    # anyone reading code. ``triggering`` is the level/edge/scheduled distinction
    # that explains why some strategies rack up huge trade counts ("acts every day
    # the condition holds") and others trade rarely.
    thesis: str = ""                        # what it does / why, in one or two sentences
    rule: str = ""                          # the exact entry/exit rule at default params
    triggering: str = "level"               # "level" | "edge" | "scheduled"
    reads: tuple[str, ...] = ()             # the data/indicator inputs it consults

    @abstractmethod
    def decide(
        self,
        date: str,
        history: "History",
        params: dict[str, Any],
        portfolio: "Portfolio",
    ) -> DecisionResult:
        """Return ``(orders, reason)`` for the simulated day ``date``.

        ``history`` is bounded to ``date`` (it physically cannot read the future —
        see :class:`backend.engine.history.History`). ``params`` is a plain dict of
        resolved parameter values; ``portfolio`` is read-only context the strategy
        may consult (e.g. current cash / holdings) but must not mutate.
        """
        ...

    # -- parameter helpers --------------------------------------------------
    @classmethod
    def default_params(cls) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, spec in cls.param_schema.items():
            out[key] = int(spec.default) if spec.type == "int" else float(spec.default)
        return out

    @classmethod
    def resolve_params(cls, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        """Defaults with any caller overrides applied (unknown keys rejected)."""
        params = cls.default_params()
        for key, value in (overrides or {}).items():
            if key not in cls.param_schema:
                raise KeyError(f"{cls.name}: unknown parameter {key!r}")
            spec = cls.param_schema[key]
            params[key] = int(value) if spec.type == "int" else float(value)
        return params

    @classmethod
    def info_json(cls) -> dict[str, Any]:
        """The plain-English transparency block for the UI (Stage 9)."""
        return {
            "thesis": cls.thesis,
            "rule": cls.rule,
            "triggering": cls.triggering,
            "reads": list(cls.reads),
        }

    @classmethod
    def schema_json(cls) -> dict[str, Any]:
        """JSON-serializable param schema (for the future /api/strategies route)."""
        return {
            key: {
                "min": spec.min, "max": spec.max, "step": spec.step,
                "default": spec.default, "type": spec.type,
                "label": spec.label or key,
            }
            for key, spec in cls.param_schema.items()
        }


# ---------------------------------------------------------------------------
# Registry — auto-discovery of strategy plugins
# ---------------------------------------------------------------------------
def registry() -> dict[str, type[Strategy]]:
    """Map ``name -> Strategy subclass`` for every importable strategy.

    Importing ``backend.strategies`` imports each strategy module (see that
    package's ``__init__``), which defines the subclasses; we then collect them via
    ``__subclasses__``. So registering a new strategy is purely "add a file".
    """
    import backend.strategies  # noqa: F401  (triggers submodule imports)

    found: dict[str, type[Strategy]] = {}
    _collect(Strategy, found)
    return found


def _collect(cls: type[Strategy], out: dict[str, type[Strategy]]) -> None:
    for sub in cls.__subclasses__():
        name = getattr(sub, "name", None)
        if name and name != "base":
            out[name] = sub
        _collect(sub, out)


def get_strategy(name: str) -> type[Strategy]:
    reg = registry()
    if name not in reg:
        raise KeyError(f"unknown strategy {name!r}; known: {sorted(reg)}")
    return reg[name]
