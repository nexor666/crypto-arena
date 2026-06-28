"""Stage-4 API tests — the engine routes over a TestClient.

``/api/strategies`` and ``/api/events`` are pure (no DB), so they're tested
directly. ``/api/backtest`` is made hermetic by monkeypatching ``load_history`` to
return a small synthetic :class:`History`, so the test asserts the API *contract*
(shape, validation, leaderboard ordering) without depending on a populated DB.
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.engine.history import History
from backend.strategies.base import registry

client = TestClient(main.app)


def _synthetic_history(asset="BTC"):
    """A short BTC series with the fields the bundled strategies read."""
    records = []
    closes = [100, 120, 90, 150, 130, 300]
    fng = [10, 50, 20, 80, 40, 90]
    for i, (c, f) in enumerate(zip(closes, fng)):
        records.append({
            "date": f"2020-01-{i + 1:02d}",
            "open": float(c), "high": float(c), "low": float(c), "close": float(c),
            "volume": 1.0, "fear_greed": f,
        })
    return History(asset, records)


# ---------------------------------------------------------------------------
# /api/strategies
# ---------------------------------------------------------------------------
def test_strategies_lists_registry_with_schema():
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    body = resp.json()
    reg = registry()
    assert body["count"] == len(reg)
    names = {s["name"] for s in body["strategies"]}
    assert names == set(reg)
    # Each entry carries the contract fields the frontend builds controls from.
    for s in body["strategies"]:
        assert set(s) >= {"name", "description", "universe", "param_schema", "default_params"}
        for key, spec in s["param_schema"].items():
            assert set(spec) >= {"min", "max", "step", "default", "type", "label"}
            assert spec["min"] <= spec["default"] <= spec["max"]


# ---------------------------------------------------------------------------
# /api/events
# ---------------------------------------------------------------------------
def test_events_returns_halvings_and_notable():
    body = client.get("/api/events").json()
    assert len(body["halvings"]) == 5
    assert body["halvings"][0]["date"] == "2012-11-28"
    assert all(h["kind"] == "halving" for h in body["halvings"])
    kinds = {e["kind"] for e in body["notable"]}
    assert kinds == {"top", "bottom", "crash"}


# ---------------------------------------------------------------------------
# /api/backtest
# ---------------------------------------------------------------------------
def test_backtest_all_strategies(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    resp = client.post("/api/backtest", json={"asset": "BTC", "capital": 10_000})
    assert resp.status_code == 200
    body = resp.json()

    assert body["schema_version"] == 1
    assert body["asset"] == "BTC"
    reg = registry()
    assert len(body["results"]) == len(reg)
    assert len(body["leaderboard"]) == len(reg)

    # Leaderboard is sorted by score, descending.
    scores = [r["score"] for r in body["leaderboard"]]
    assert scores == sorted(scores, reverse=True)

    # Each result carries the full per-day stream + trades + both metric lenses.
    one = body["results"][0]
    assert set(one) >= {"strategy", "params", "snapshots", "trades", "metrics", "score"}
    assert set(one["metrics"]) == {"pre_tax", "after_tax"}
    assert len(one["snapshots"]) == 6
    snap = one["snapshots"][0]
    assert set(snap) >= {"date", "price", "cash", "pre_tax_value", "after_tax_value"}


def test_backtest_buy_hold_matches_price_return(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    resp = client.post("/api/backtest", json={
        "asset": "BTC", "capital": 10_000, "fee_pct": 0.0,
        "tax": {"enabled": False},
        "strategies": [{"name": "buy_hold"}],
    })
    body = resp.json()
    assert len(body["results"]) == 1
    pre = body["results"][0]["metrics"]["pre_tax"]
    # 100 -> 300 close = +200% with no fee/tax (golden property, over the API).
    assert math.isclose(pre["total_return_pct"], 2.0, rel_tol=1e-9)
    assert math.isclose(pre["final_value"], 30_000.0, rel_tol=1e-9)


def test_backtest_param_override(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    resp = client.post("/api/backtest", json={
        "strategies": [{"name": "fear_greed", "params": {"buy_below": 15}}],
    })
    assert resp.status_code == 200
    assert resp.json()["results"][0]["params"]["buy_below"] == 15


def test_backtest_unknown_asset_404(monkeypatch):
    resp = client.post("/api/backtest", json={"asset": "DOGE"})
    assert resp.status_code == 404


def test_backtest_unknown_strategy_400(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    resp = client.post("/api/backtest", json={"strategies": [{"name": "nope"}]})
    assert resp.status_code == 400


def test_backtest_unknown_param_400(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    resp = client.post("/api/backtest", json={
        "strategies": [{"name": "buy_hold", "params": {"bogus": 1}}],
    })
    assert resp.status_code == 400
