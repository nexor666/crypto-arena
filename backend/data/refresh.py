"""Refresh orchestrator — the one `refresh-data` entrypoint (CLI + API share it).

Flow per source:
    fetch()  ->  write an IMMUTABLE raw snapshot to data/raw/  ->  upsert into SQLite
and, for price series, recompute that asset's indicators.

The immutable raw snapshot (plan Stage 1) is the reproducibility guarantee: a
yfinance hiccup or a Yahoo/BGeometrics revision can't silently rewrite past
Hall-of-Fame results, because the exact bytes we backtested on are kept on disk,
timestamped and never overwritten.

Run standalone:
    python -m backend.data.refresh            # full refresh
    python -m backend.data.refresh --status   # print current DB coverage, no fetch
Or via the API: POST /api/admin/refresh (see backend/main.py).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from typing import Any

from backend import config
from backend.data import fetchers, indicators
from backend.data.store import Store


def refresh(start: str = config.FETCH_START, store: Store | None = None) -> dict[str, Any]:
    """Pull every source, snapshot it, and (re)populate the SQLite store.

    Each source is isolated: a failure (e.g. an MVRV rate-limit) is recorded and
    the rest still complete, so a flaky feed never aborts the whole refresh.
    """
    store = store or Store()
    store.init_schema()
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    started = _utcnow()
    sources: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for provider in fetchers.all_providers(start):
        try:
            result = provider.fetch()
        except Exception as exc:  # noqa: BLE001 - report, don't abort the batch
            errors.append({"source": provider.name, "error": f"{type(exc).__name__}: {exc}"})
            continue

        snapshot_path = _write_raw_snapshot(result)
        store.record_snapshot(
            source=result.source,
            path=str(snapshot_path),
            row_count=len(result.records),
            date_min=result.date_min,
            date_max=result.date_max,
        )

        stored = _persist(store, result)
        sources.append({
            "source": result.source,
            "kind": result.kind,
            "rows": len(result.records),
            "stored": stored,
            "start": result.date_min,
            "end": result.date_max,
            "snapshot": snapshot_path.name,
            "notes": result.notes,
        })

    store.set_meta("last_refresh", started)
    summary = {
        "started_at": started,
        "finished_at": _utcnow(),
        "sources": sources,
        "errors": errors,
        "status": store.status(),
    }
    return summary


def _persist(store: Store, result: fetchers.FetchResult) -> dict[str, int]:
    """Write a fetch result into SQLite; for prices also recompute indicators."""
    if result.kind == "prices":
        prices_written = store.upsert_prices(result.records)
        ind_rows = indicators.compute_indicators(result.records)
        ind_written = store.replace_indicators(result.asset, ind_rows)
        return {"prices": prices_written, "indicators": ind_written}
    if result.kind == "sentiment":
        return {"sentiment": store.upsert_sentiment(result.records)}
    if result.kind == "onchain":
        return {"onchain": store.upsert_onchain(result.records)}
    return {}


def _write_raw_snapshot(result: fetchers.FetchResult) -> Any:
    """Persist the raw payload to a timestamped, never-overwritten JSON file."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = config.RAW_DIR / f"{result.source}__{stamp}.json"
    envelope = {
        "source": result.source,
        "kind": result.kind,
        "asset": result.asset,
        "metric": result.metric,
        "fetched_at": _utcnow(),
        "row_count": len(result.records),
        "date_min": result.date_min,
        "date_max": result.date_max,
        "raw": result.raw,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(envelope, fh, separators=(",", ":"), default=str)
    return path


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh the crypto-arena data layer.")
    parser.add_argument("--status", action="store_true", help="print DB coverage and exit (no fetch)")
    parser.add_argument("--start", default=config.FETCH_START, help="earliest date to fetch (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    store = Store()
    if args.status:
        store.init_schema()
        print(json.dumps(store.status(), indent=2))
        return 0

    print(f"Refreshing data layer -> {config.DB_PATH}")
    summary = refresh(start=args.start, store=store)
    for src in summary["sources"]:
        note = f"  [{'; '.join(src['notes'])}]" if src["notes"] else ""
        print(f"  {src['source']:<14} {src['rows']:>5} rows  {src['start']}..{src['end']}{note}")
    for err in summary["errors"]:
        print(f"  ! {err['source']}: {err['error']}")
    print("\nCoverage:")
    print(json.dumps(summary["status"], indent=2))
    return 1 if summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
