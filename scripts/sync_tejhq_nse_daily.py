#!/usr/bin/env python3
"""Sync NSE CM daily Parquet files from TejHQ snapshots into QUANT_DATA_DIR/parquet.

Idempotent: valid local Parquet and gzip JSON caches are reused — no repeat API calls
unless --overwrite or --refresh-cache is set.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta

from quant.config import PARQUET_DATA_DIR, TEJHQ_API_KEY
from quant.data.tejhq import (
    TejHQClient,
    latest_local_parquet_date,
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
        help="Start the day after the latest valid local nse_cm_*.parquet (ignores --start)",
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

    if args.incremental:
        last = latest_local_parquet_date(PARQUET_DATA_DIR)
        start = (last + timedelta(days=1)) if last else date(2011, 1, 1)
    elif args.start is not None:
        start = args.start
    else:
        raise SystemExit("Provide --start or use --incremental")

    if end < start:
        print("Nothing to sync: local data is already through", end)
        return

    series = None if args.all_series else "EQ"
    out = sync_nse_cm_parquet_range(
        PARQUET_DATA_DIR,
        start,
        end,
        client=client,
        series=series,
        overwrite=args.overwrite,
        refresh_cache=args.refresh_cache,
    )
    print(out["status"].value_counts().to_string())
    if "source" in out.columns:
        print("sources:", out["source"].value_counts().to_string())
    errs = out[out["status"].astype(str).str.startswith("error")]
    if not errs.empty:
        print(errs.tail(5).to_string(index=False))


if __name__ == "__main__":
    main()