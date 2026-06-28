"""Stage-4 API tests — the engine routes over a TestClient.

``/api/strategies`` and ``/api/events`` are pure (no DB), so they're tested
directly. ``/api/backtest`` is made hermetic by monkeypatching ``load_history`` to
return a small synthetic :class:`History`, so the test asserts the API *contract*
(shape, validation, leaderboard ordering) without depending on a populated DB.
"""

from __future__ import annotations

import datetime
import math

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.data.store import Store
from backend.engine.history import History
from backend.strategies.base import registry

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def temp_store(tmp_path, monkeypatch):
    """Point the app at a throwaway SQLite db so tests never touch the real ledger.

    The endpoints read the module-global ``store`` at call time, so swapping it here
    isolates every test (incl. the Stage-7 runs that get appended on each backtest).
    """
    s = Store(tmp_path / "test.db")
    s.init_schema()
    monkeypatch.setattr(main, "store", s)
    return s


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


def _long_history(asset="BTC", n=300):
    """A longer synthetic series (a noisy uptrend) for the validators, which slice
    the history into folds / start-date windows and so need real length."""
    d0 = datetime.date(2019, 1, 1)
    records = []
    price = 100.0
    for i in range(n):
        price *= 1.004 + 0.02 * math.sin(i / 11.0)  # drifting, oscillating
        d = (d0 + datetime.timedelta(days=i)).isoformat()
        fng = 50 + 45 * math.sin(i / 9.0)
        records.append({
            "date": d, "open": price, "high": price, "low": price,
            "close": price, "volume": 1.0, "fear_greed": fng,
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


# ---------------------------------------------------------------------------
# Stage 7 — runs ledger + Hall of Fame
# ---------------------------------------------------------------------------
def test_backtest_records_runs_and_returns_signature(monkeypatch, temp_store):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    resp = client.post("/api/backtest", json={
        "asset": "BTC", "strategies": [{"name": "buy_hold"}, {"name": "fear_greed"}],
    })
    body = resp.json()
    assert body["settings_sig"]
    # one ledger row per raced strategy
    rows = temp_store.get_runs()
    assert len(rows) == 2
    assert {r["strategy"] for r in rows} == {"buy_hold", "fear_greed"}
    assert all(r["settings_sig"] == body["settings_sig"] for r in rows)


def test_hall_of_fame_aggregates_and_ranks(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _synthetic_history())
    # race twice (accumulate tries), with one param override on the second pass
    client.post("/api/backtest", json={"strategies": [{"name": "buy_hold"},
                                                       {"name": "fear_greed"}]})
    client.post("/api/backtest", json={"strategies": [
        {"name": "buy_hold"}, {"name": "fear_greed", "params": {"buy_below": 15}}]})

    hof = client.get("/api/hall-of-fame").json()
    assert hof["settings_sig"]
    assert hof["n_runs"] == 4
    names = [e["strategy"] for e in hof["strategies"]]
    assert set(names) == {"buy_hold", "fear_greed"}
    # ranked by best_score, descending
    scores = [e["best_score"] for e in hof["strategies"]]
    assert scores == sorted(scores, reverse=True)
    bh = next(e for e in hof["strategies"] if e["strategy"] == "buy_hold")
    assert bh["times_tried"] == 2
    fg = next(e for e in hof["strategies"] if e["strategy"] == "fear_greed")
    assert len(fg["configs"]) == 2  # two distinct param sets
    # best-over-time is monotonic non-decreasing
    bot = [p["best_score"] for p in hof["best_over_time"]]
    assert bot == sorted(bot)
    assert len(hof["best_over_time"]) == 4


def test_hall_of_fame_empty_when_no_runs():
    hof = client.get("/api/hall-of-fame").json()
    assert hof["n_runs"] == 0
    assert hof["strategies"] == []


# ---------------------------------------------------------------------------
# Stage 7 — walk-forward + robustness
# ---------------------------------------------------------------------------
def test_walk_forward_shape(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _long_history())
    resp = client.post("/api/walk-forward", json={"strategy": "fear_greed", "n_folds": 4})
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "fear_greed"
    assert body["n_folds"] >= 1
    assert len(body["folds"]) == body["n_folds"]
    for f in body["folds"]:
        assert f["test_start"] < f["test_end"]
        assert "oos_score" in f and "alpha_vs_bh" in f
        assert set(f["tuned_params"]) == set(registry()["fear_greed"].default_params())
    assert body["verdict"] in {"robust", "fragile"}


def test_robustness_shape(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _long_history())
    resp = client.post("/api/robustness", json={"strategy": "buy_hold"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["strategy"] == "buy_hold"
    assert body["n_starts"] >= 1
    assert len(body["runs"]) == body["n_starts"]
    # buy_hold vs buy_hold benchmark = zero alpha everywhere
    assert all(abs(r["alpha_vs_bh"]) < 1e-9 for r in body["runs"])
    assert 0.0 <= body["beats_bh_rate"] <= 1.0
    assert body["verdict"] in {"robust", "fragile"}


def test_walk_forward_unknown_strategy_400(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _long_history())
    resp = client.post("/api/walk-forward", json={"strategy": "nope"})
    assert resp.status_code == 400


def test_robustness_explicit_start_dates(monkeypatch):
    monkeypatch.setattr(main, "load_history", lambda *a, **k: _long_history())
    resp = client.post("/api/robustness", json={
        "strategy": "fear_greed",
        "start_dates": ["2019-01-01", "2019-04-01", "2019-07-01"],
    })
    body = resp.json()
    assert resp.status_code == 200
    assert body["n_starts"] == 3
    assert [r["start"] for r in body["runs"]] == ["2019-01-01", "2019-04-01", "2019-07-01"]
