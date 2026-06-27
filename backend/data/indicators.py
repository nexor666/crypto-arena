"""Indicator pipeline — precompute the price-derived signals the strategies need.

Computed per asset from its daily close and stored as ``(asset, date, name, value)``
rows. Everything here is point-in-time / no-look-ahead: a value at date *d* uses
only closes up to and including *d* (rolling means and Wilder RSI are causal), and
the weekly RSI maps each day to the most recently *completed* week. Warmup days
where an indicator is undefined (e.g. the first 199 days for a 200-day MA) are
simply not emitted — the strategy treats a missing value as "not yet available".

Indicators produced (plan Stage 1 list):
    ma_50d, ma_200d   — 50/200-day simple MAs (golden/death cross inputs)
    ma_200w           — 200-week MA (≈ 1400-day SMA of daily close)
    mayer             — Mayer Multiple = close / 200-day MA
    rsi_14            — daily Wilder RSI(14)
    rsi_14w           — weekly Wilder RSI(14), aligned to daily (last completed week)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# Indicator parameters (kept here, not magic numbers in the loop).
MA_SHORT = 50
MA_LONG = 200
MA_200W_DAYS = 200 * 7   # 200 weeks expressed in trading-free daily bars (crypto = 7d/wk)
RSI_PERIOD = 14


def compute_indicators(price_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return ``[{date, name, value}, ...]`` for one asset's price history.

    ``price_records`` is the date-sorted output of the price fetcher/store
    (each a dict with at least ``date`` and ``close``).
    """
    if not price_records:
        return []

    idx = pd.to_datetime([r["date"] for r in price_records])
    close = pd.Series(
        [r["close"] for r in price_records], index=idx, dtype="float64"
    ).sort_index()

    ma_50 = close.rolling(MA_SHORT).mean()
    ma_200 = close.rolling(MA_LONG).mean()
    ma_200w = close.rolling(MA_200W_DAYS).mean()
    mayer = close / ma_200
    rsi_14 = _wilder_rsi(close, RSI_PERIOD)

    # Weekly RSI: resample to weekly closes (week ending Sun), RSI on that, then map
    # back onto daily dates with forward-fill — a mid-week day sees the prior
    # completed week's value, so no future information leaks in.
    weekly_close = close.resample("W").last()
    weekly_rsi = _wilder_rsi(weekly_close, RSI_PERIOD)
    rsi_14w = weekly_rsi.reindex(close.index, method="ffill")

    series_by_name = {
        "ma_50d": ma_50,
        "ma_200d": ma_200,
        "ma_200w": ma_200w,
        "mayer": mayer,
        "rsi_14": rsi_14,
        "rsi_14w": rsi_14w,
    }

    out: list[dict[str, Any]] = []
    for name, series in series_by_name.items():
        for ts, value in series.items():
            if value is None or pd.isna(value):
                continue  # undefined (warmup) -> don't store
            out.append({"date": ts.strftime("%Y-%m-%d"), "name": name, "value": float(value)})
    return out


def _wilder_rsi(series: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI. Causal: uses ``ewm(adjust=False)`` smoothing of gains/losses."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    # All-gain windows give avg_loss==0 -> rs=inf -> rsi already 100; make it explicit.
    rsi = rsi.where(avg_loss != 0.0, 100.0)
    # Keep RSI undefined during warmup (where avg_gain itself is NaN).
    rsi = rsi.where(avg_gain.notna())
    return rsi
