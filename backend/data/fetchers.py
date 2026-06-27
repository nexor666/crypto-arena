"""Data-source adapters — each external feed behind one common interface.

Plan modularity principle 3: every source (yfinance OHLCV, alternative.me Fear &
Greed, BGeometrics MVRV) implements the same ``DataProvider`` so a feed can be
swapped or added without the engine/store noticing. ``fetch()`` returns a
``FetchResult`` carrying BOTH the normalized rows (for SQLite) and the raw payload
(for the immutable snapshot the refresh orchestrator writes to disk).

Networking and parsing live here and nowhere else; ``store.py`` only ever sees
already-normalized ``{date: ..., value/ohlcv: ...}`` dicts.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from backend import config


@dataclass
class FetchResult:
    """One source's pull: normalized rows + the raw payload for snapshotting."""

    source: str                 # snapshot key, e.g. "prices_BTC", "fear_greed", "mvrv_zscore"
    records: list[dict[str, Any]]
    raw: Any                    # JSON-serializable, as-fetched (pre-normalization where it differs)
    kind: str                   # "prices" | "sentiment" | "onchain"
    asset: str | None = None    # set for price pulls
    metric: str | None = None   # set for on-chain pulls
    notes: list[str] = field(default_factory=list)

    @property
    def date_min(self) -> str | None:
        return self.records[0]["date"] if self.records else None

    @property
    def date_max(self) -> str | None:
        return self.records[-1]["date"] if self.records else None


class DataProvider(ABC):
    """Common contract: a named source that can ``fetch()`` itself."""

    name: str

    @abstractmethod
    def fetch(self) -> FetchResult:  # pragma: no cover - interface
        ...


# ---------------------------------------------------------------------------
# Prices — yfinance (daily OHLCV)
# ---------------------------------------------------------------------------
class PriceProvider(DataProvider):
    """Daily OHLCV for one asset via yfinance (Yahoo Finance, free, no key)."""

    def __init__(self, asset: str, yf_ticker: str, start: str = config.FETCH_START) -> None:
        self.asset = asset
        self.yf_ticker = yf_ticker
        self.start = start
        self.name = f"prices_{asset}"

    def fetch(self) -> FetchResult:
        import yfinance as yf  # imported lazily so the rest of the app loads without it

        df = yf.download(
            self.yf_ticker,
            start=self.start,
            progress=False,
            auto_adjust=False,   # we want raw OHLC, not split/div-adjusted (crypto has neither)
            threads=False,
        )
        notes: list[str] = []
        if df is None or df.empty:
            notes.append("yfinance returned no rows")
            return FetchResult(self.name, [], [], kind="prices", asset=self.asset, notes=notes)

        # Single-ticker downloads can come back with a MultiIndex on the columns
        # ((field, ticker)); flatten to just the field name.
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df = df.droplevel(axis=1, level=-1)

        records: list[dict[str, Any]] = []
        for ts, row in df.iterrows():
            close = _num(row.get("Close"))
            if close is None:
                continue  # skip NaN/blank days rather than forward-fill (no peeking)
            records.append({
                "asset": self.asset,
                "date": ts.strftime("%Y-%m-%d"),
                "open": _num(row.get("Open")),
                "high": _num(row.get("High")),
                "low": _num(row.get("Low")),
                "close": close,
                "volume": _num(row.get("Volume")),
            })
        records.sort(key=lambda r: r["date"])
        dropped = len(df) - len(records)
        if dropped:
            notes.append(f"skipped {dropped} row(s) with missing close")
        # Raw == the normalized OHLCV series (this IS the as-fetched data we must be
        # able to reproduce; storing it preserves Yahoo's numbers against later revision).
        return FetchResult(
            self.name, records, raw=records, kind="prices", asset=self.asset, notes=notes,
        )


# ---------------------------------------------------------------------------
# Sentiment — alternative.me Fear & Greed Index
# ---------------------------------------------------------------------------
class FearGreedProvider(DataProvider):
    """Market-wide Fear & Greed Index (full daily history since Feb 2018)."""

    name = "fear_greed"

    def fetch(self) -> FetchResult:
        payload = _get_json(config.FEAR_GREED_URL)
        data = payload.get("data", []) if isinstance(payload, dict) else []
        records: list[dict[str, Any]] = []
        for item in data:
            ts = item.get("timestamp")
            val = item.get("value")
            if ts is None or val is None:
                continue
            date = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            records.append({"date": date, "value": float(val)})
        records.sort(key=lambda r: r["date"])  # API returns newest-first
        return FetchResult(self.name, records, raw=payload, kind="sentiment")


# ---------------------------------------------------------------------------
# On-chain — BGeometrics / bitcoin-data.com MVRV Z-Score (BTC only)
# ---------------------------------------------------------------------------
class MvrvProvider(DataProvider):
    """BTC MVRV Z-Score. Free tier caps at ~4 years; we store what it returns."""

    name = "mvrv_zscore"
    metric = "mvrv_zscore"

    def fetch(self) -> FetchResult:
        payload = _get_json(config.MVRV_URL)
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        records: list[dict[str, Any]] = []
        for item in rows:
            date = item.get("d") or item.get("date")
            val = item.get("mvrvZscore")
            if val is None:
                val = item.get("value")
            if date is None or val is None:
                continue
            records.append({"date": date, "metric": self.metric, "value": float(val)})
        records.sort(key=lambda r: r["date"])
        notes = []
        if records:
            notes.append(
                f"free-tier coverage {records[0]['date']}..{records[-1]['date']} "
                f"({len(records)} rows) — capped to recent history, BTC-only"
            )
        return FetchResult(
            self.name, records, raw=payload, kind="onchain", metric=self.metric, notes=notes,
        )


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------
def price_providers(start: str = config.FETCH_START) -> list[PriceProvider]:
    return [PriceProvider(asset, ticker, start) for asset, ticker in config.ASSETS.items()]


def all_providers(start: str = config.FETCH_START) -> list[DataProvider]:
    return [*price_providers(start), FearGreedProvider(), MvrvProvider()]


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------
def _get_json(url: str) -> Any:
    resp = requests.get(
        url,
        headers={"User-Agent": config.HTTP_USER_AGENT, "Accept": "application/json"},
        timeout=config.HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _num(value: Any) -> float | None:
    """Coerce to float; map NaN/None/blank to None (so SQLite stores NULL)."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f
