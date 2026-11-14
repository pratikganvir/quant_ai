#!/usr/bin/env python3
"""Sync NSE CM daily Parquet files from TejHQ snapshots into QUANT_DATA_DIR/parquet.

Idempotent: valid local Parquet and gzip JSON caches are reused — no repeat API calls
unless --overwrite or --refresh-cache is set.

Use --fill-gaps to download every weekday in a range that lacks a valid parquet file
(including holes before your newest on-disk date).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from quant.config import PARQUET_DATA_DIR, TEJHQ_API_KEY
from quant.data.tejhq import (
    TejHQClient,
    latest_local_parquet_date,
    missing_weekdays,
    sync_nse_cm_missing_days,
    sync_nse_cm_parquet_range,
)


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, default=None, help="YYYY-MM-DD (default: latest from API)")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Start the day after the latest nse_cm_*.parquet filename (can skip interior gaps)",
    )
    parser.add_argument(
        "--fill-gaps",
        action="store_true",
        help="Sync only weekdays missing a valid parquet (recommended)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --fill-gaps: print how many days would be synced",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing parquet files")
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Re-fetch from API and refresh gzip JSON caches",
    )
    parser.add_argument("--all-series", action="store_true", help="Keep BE/SM/etc., not EQ-only")
    args = parser.parse_args()

    client = TejHQClient(api_key=TEJHQ_API_KEY)
    end = args.end or client.latest_trading_date() or date.today()

    default_start = date(2011, 1, 4)

    if args.fill_gaps:
        start = args.start or default_start
        if end < start:
            raise SystemExit("end before start")
        missing = missing_weekdays(PARQUET_DATA_DIR, start, end)
        print(f"Missing weekdays in [{start}, {end}]: {len(missing)}")
        if missing:
            print("  first:", missing[0], " last:", missing[-1])
        if args.dry_run:
            return
        out = sync_nse_cm_missing_days(
            PARQUET_DATA_DIR,
            start,
            end,
            client=client,
            series=None if args.all_series else "EQ",
            overwrite=args.overwrite,
            refresh_cache=args.refresh_cache,
        )
    elif args.incremental:
        last = latest_local_parquet_date(PARQUET_DATA_DIR)
        start = (last + timedelta(days=1)) if last else default_start
        if end < start:
            print("Nothing to sync: newest filename is already through", last)
            print("Tip: use --fill-gaps to backfill holes before", last)
            return
        out = sync_nse_cm_parquet_range(
            PARQUET_DATA_DIR,
            start,
            end,
            client=client,
            series=None if args.all_series else "EQ",
            overwrite=args.overwrite,
            refresh_cache=args.refresh_cache,
        )
    elif args.start is not None:
        start = args.start
        if end < start:
            raise SystemExit("end before start")
        out = sync_nse_cm_parquet_range(
            PARQUET_DATA_DIR,
            start,
            end,
            client=client,
            series=None if args.all_series else "EQ",
            overwrite=args.overwrite,
            refresh_cache=args.refresh_cache,
        )
    else:
        raise SystemExit("Use --fill-gaps, --incremental, or --start")

    if out.empty:
        print("No days processed.")
        return
    print(out["status"].value_counts().to_string())
    if "source" in out.columns:
        print("sources:", out["source"].value_counts().to_string())
    errs = out[out["status"].astype(str).str.startswith("error")]
    if not errs.empty:
        print(errs.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()