#!/usr/bin/env python3
"""Sync NSE CM daily parquet from Upstox historical candles (cached per symbol).

Idempotent: symbol parquets and gzip API responses are reused unless --refresh-cache.
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

from quant.config import PARQUET_DATA_DIR  # noqa: E402
from quant.data.tejhq import latest_local_parquet_date  # noqa: E402
from quant.data.upstox import (  # noqa: E402
    UpstoxClient,
    list_cached_symbols,
    materialize_nse_cm_range,
    sync_upstox_incremental,
    sync_upstox_universe,
    upstox_access_token,
)


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=None, help="YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, default=None, help="YYYY-MM-DD (default: today)")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Extend from latest local parquet (or last ~15y) through today",
    )
    parser.add_argument(
        "--materialize-only",
        action="store_true",
        help="Rebuild nse_cm_* from Upstox symbol cache only (no API)",
    )
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated NSE symbols (default: all EQ)")
    parser.add_argument("--max-symbols", type=int, default=None, help="Limit symbols (debug)")
    parser.add_argument("--refresh-cache", action="store_true", help="Re-fetch API even if gzip cache exists")
    parser.add_argument("--overwrite", action="store_true", help="Replace valid daily parquet files")
    parser.add_argument("--no-materialize", action="store_true", help="Fetch symbol histories only")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not upstox_access_token():
        print("Set UPSTOX_ACCESS_TOKEN or UPSTOX_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    client = UpstoxClient()
    parquet_root = PARQUET_DATA_DIR
    end = args.end or date.today()
    symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else None

    if args.materialize_only:
        start = args.start or (end - timedelta(days=365 * 15))
        cached = list_cached_symbols(client.cache_dir)
        if not cached:
            print("No per-symbol Upstox cache — run a full sync first.", file=sys.stderr)
            sys.exit(1)
        df = materialize_nse_cm_range(
            parquet_root,
            client.cache_dir,
            start,
            end,
            overwrite=args.overwrite,
        )
        print(df["status"].value_counts())
        return

    if args.incremental:
        df = sync_upstox_incremental(
            parquet_root,
            client=client,
            symbols=symbols,
            max_symbols=args.max_symbols,
            refresh_cache=args.refresh_cache,
            materialize=not args.no_materialize,
            overwrite_parquet=args.overwrite,
            dry_run=args.dry_run,
        )
    else:
        start = args.start or (latest_local_parquet_date(parquet_root) or date(2005, 1, 1))
        if start > end:
            start = end - timedelta(days=30)
        df = sync_upstox_universe(
            parquet_root,
            start,
            end,
            client=client,
            symbols=symbols,
            max_symbols=args.max_symbols,
            refresh_cache=args.refresh_cache,
            materialize=not args.no_materialize,
            overwrite_parquet=args.overwrite,
            dry_run=args.dry_run,
        )

    if args.dry_run:
        print(df.head(20))
        print(f"Would sync {len(df)} symbols")
        return
    if "status" in df.columns:
        print(df["status"].value_counts().head(20))
    print("Latest parquet:", latest_local_parquet_date(parquet_root))


if __name__ == "__main__":
    main()