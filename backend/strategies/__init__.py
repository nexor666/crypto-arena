"""Strategy library — auto-discovered plugins + registry.

Importing this package imports every strategy module below, which defines the
``Strategy`` subclasses; ``backend.strategies.base.registry()`` then collects them.
So adding a strategy = drop a new module here and add one import line — it appears
in the registry (and, later, the frontend) with zero other changes.
"""

from backend.strategies import buy_hold, fear_greed  # noqa: F401

__all__ = ["buy_hold", "fear_greed"]
