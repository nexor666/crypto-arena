"""SQLite read/write helpers — the single source of truth for the data layer.

Schema is pinned by the plan so new data means new rows/metrics, never schema
surgery:

    prices(asset, date, open, high, low, close, volume)
    indicators(asset, date, name, value)        # 200D/200W MA, Mayer, RSI, ...
    sentiment(date, value)                       # Fear & Greed index
    onchain(date, metric, value)                 # MVRV Z-Score, future on-chain
    raw_snapshots(...)                           # index of immutable raw pulls
    meta(key, value)                             # last-refresh bookkeeping

Dates are stored as ISO ``YYYY-MM-DD`` strings (UTC daily close), which sort
lexicographically — so range queries are plain string comparisons.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    asset  TEXT NOT NULL,
    date   TEXT NOT NULL,
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    PRIMARY KEY (asset, date)
);
CREATE TABLE IF NOT EXISTS indicators (
    asset TEXT NOT NULL,
    date  TEXT NOT NULL,
    name  TEXT NOT NULL,
    value REAL,
    PRIMARY KEY (asset, date, name)
);
CREATE TABLE IF NOT EXISTS sentiment (
    date  TEXT PRIMARY KEY,
    value REAL
);
CREATE TABLE IF NOT EXISTS onchain (
    date   TEXT NOT NULL,
    metric TEXT NOT NULL,
    value  REAL,
    PRIMARY KEY (date, metric)
);
CREATE TABLE IF NOT EXISTS raw_snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    path       TEXT NOT NULL,
    row_count  INTEGER,
    date_min   TEXT,
    date_max   TEXT
);
CREATE TABLE IF NOT EXISTS runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT NOT NULL,
    asset              TEXT NOT NULL,
    strategy           TEXT NOT NULL,
    params_json        TEXT NOT NULL,
    start              TEXT NOT NULL,
    end                TEXT NOT NULL,
    fee_pct            REAL NOT NULL,
    tax_json           TEXT NOT NULL,
    capital_json       TEXT NOT NULL,
    settings_sig       TEXT NOT NULL,
    standardized_score REAL,
    after_tax_cagr     REAL,
    after_tax_final    REAL,
    max_drawdown       REAL,
    sharpe             REAL,
    n_trades           INTEGER,
    is_walkforward     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_sig ON runs (settings_sig);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class Store:
    """Thin wrapper over a SQLite connection at ``config.DB_PATH``.

    Each public method opens and commits its own short-lived connection, so the
    Store is safe to construct once and reuse (including from FastAPI handlers).
    """

    def __init__(self, db_path: Path | str = config.DB_PATH) -> None:
        self.db_path = Path(db_path)

    # -- connection plumbing ------------------------------------------------
    @contextmanager
    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Create all tables if they don't exist (idempotent)."""
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # -- writers ------------------------------------------------------------
    def upsert_prices(self, records: Iterable[dict[str, Any]]) -> int:
        rows = [
            (r["asset"], r["date"], r.get("open"), r.get("high"),
             r.get("low"), r.get("close"), r.get("volume"))
            for r in records
        ]
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO prices (asset, date, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(asset, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, "
                "close=excluded.close, volume=excluded.volume",
                rows,
            )
        return len(rows)

    def replace_indicators(self, asset: str, records: Iterable[dict[str, Any]]) -> int:
        """Replace all indicator rows for ``asset`` (recomputed wholesale each refresh)."""
        rows = [(asset, r["date"], r["name"], r.get("value")) for r in records]
        with self._connect() as conn:
            conn.execute("DELETE FROM indicators WHERE asset = ?", (asset,))
            conn.executemany(
                "INSERT INTO indicators (asset, date, name, value) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)

    def upsert_sentiment(self, records: Iterable[dict[str, Any]]) -> int:
        rows = [(r["date"], r.get("value")) for r in records]
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO sentiment (date, value) VALUES (?, ?) "
                "ON CONFLICT(date) DO UPDATE SET value=excluded.value",
                rows,
            )
        return len(rows)

    def upsert_onchain(self, records: Iterable[dict[str, Any]]) -> int:
        rows = [(r["date"], r["metric"], r.get("value")) for r in records]
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO onchain (date, metric, value) VALUES (?, ?, ?) "
                "ON CONFLICT(date, metric) DO UPDATE SET value=excluded.value",
                rows,
            )
        return len(rows)

    def record_snapshot(
        self, source: str, path: str, row_count: int,
        date_min: str | None, date_max: str | None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO raw_snapshots (source, fetched_at, path, row_count, date_min, date_max) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (source, _utcnow(), path, row_count, date_min, date_max),
            )

    def set_meta(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_meta(self, key: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # -- runs ledger (persistent Hall of Fame, Stage 7) ---------------------
    # Every backtest appends one row per strategy, so results accumulate across
    # sessions. ``settings_sig`` groups runs that are apples-to-apples comparable
    # (same asset / window / fee / tax / capital model) — the Hall of Fame only
    # ever ranks within a single signature (plan: never compare apples to oranges).
    _RUN_COLS = (
        "timestamp", "asset", "strategy", "params_json", "start", "end",
        "fee_pct", "tax_json", "capital_json", "settings_sig",
        "standardized_score", "after_tax_cagr", "after_tax_final",
        "max_drawdown", "sharpe", "n_trades", "is_walkforward",
    )

    def record_run(self, row: dict[str, Any]) -> int:
        """Append one run to the ledger; returns its new id."""
        values = [row.get(c) for c in self._RUN_COLS]
        placeholders = ", ".join("?" for _ in self._RUN_COLS)
        cols = ", ".join(self._RUN_COLS)
        with self._connect() as conn:
            cur = conn.execute(
                f"INSERT INTO runs ({cols}) VALUES ({placeholders})", values
            )
            return int(cur.lastrowid)

    def get_runs(
        self, settings_sig: str | None = None, asset: str | None = None,
        include_walkforward: bool = False,
    ) -> list[dict[str, Any]]:
        """Ledger rows (newest first), optionally filtered to one signature/asset."""
        sql = "SELECT * FROM runs WHERE 1=1"
        params: list[Any] = []
        if settings_sig is not None:
            sql += " AND settings_sig = ?"
            params.append(settings_sig)
        if asset is not None:
            sql += " AND asset = ?"
            params.append(asset)
        if not include_walkforward:
            sql += " AND is_walkforward = 0"
        sql += " ORDER BY id DESC"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def latest_settings_sig(self) -> str | None:
        """The signature of the most recently recorded (non-walkforward) run."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT settings_sig FROM runs WHERE is_walkforward = 0 "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row["settings_sig"] if row else None

    # -- readers ------------------------------------------------------------
    def get_prices(
        self, asset: str, start: str | None = None, end: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT date, open, high, low, close, volume FROM prices WHERE asset = ?"
        params: list[Any] = [asset]
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_asset_series(
        self, asset: str, start: str | None = None, end: str | None = None,
    ) -> list[dict[str, Any]]:
        """Per-day OHLCV joined with that asset's indicators — the API's series shape.

        Each row is ``{date, open, high, low, close, volume, <indicator name>: value, ...}``.
        Sentiment/on-chain are market/BTC-wide and exposed separately, not folded in here.
        """
        prices = self.get_prices(asset, start, end)
        by_date = {r["date"]: r for r in prices}

        sql = "SELECT date, name, value FROM indicators WHERE asset = ?"
        params: list[Any] = [asset]
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        with self._connect() as conn:
            for r in conn.execute(sql, params).fetchall():
                row = by_date.get(r["date"])
                if row is not None:
                    row[r["name"]] = r["value"]
        return prices  # same list objects, now indicator-enriched, still date-sorted

    def get_sentiment(
        self, start: str | None = None, end: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT date, value FROM sentiment WHERE 1=1"
        params: list[Any] = []
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def get_onchain(
        self, metric: str, start: str | None = None, end: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT date, value FROM onchain WHERE metric = ?"
        params: list[Any] = [metric]
        if start:
            sql += " AND date >= ?"
            params.append(start)
        if end:
            sql += " AND date <= ?"
            params.append(end)
        sql += " ORDER BY date"
        with self._connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def status(self) -> dict[str, Any]:
        """Row counts + date ranges per table — the Stage-1 sanity-check view."""
        out: dict[str, Any] = {"last_refresh": self.get_meta("last_refresh")}
        with self._connect() as conn:
            out["prices"] = _grouped_coverage(conn, "prices", "asset")
            out["indicators"] = {
                "rows": _count(conn, "indicators"),
                "names": sorted(
                    r["name"]
                    for r in conn.execute("SELECT DISTINCT name FROM indicators").fetchall()
                ),
            }
            out["sentiment"] = _coverage(conn, "sentiment")
            out["onchain"] = _grouped_coverage(conn, "onchain", "metric")
            out["raw_snapshots"] = _count(conn, "raw_snapshots")
            out["runs"] = _count(conn, "runs")
        return out


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def _coverage(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    row = conn.execute(
        f"SELECT COUNT(*) AS n, MIN(date) AS lo, MAX(date) AS hi FROM {table}"
    ).fetchone()
    return {"rows": row["n"], "start": row["lo"], "end": row["hi"]}


def _grouped_coverage(conn: sqlite3.Connection, table: str, group_col: str) -> dict[str, Any]:
    rows = conn.execute(
        f"SELECT {group_col} AS k, COUNT(*) AS n, MIN(date) AS lo, MAX(date) AS hi "
        f"FROM {table} GROUP BY {group_col} ORDER BY {group_col}"
    ).fetchall()
    return {r["k"]: {"rows": r["n"], "start": r["lo"], "end": r["hi"]} for r in rows}
