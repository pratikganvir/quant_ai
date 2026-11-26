#!/usr/bin/env python3
"""Sync NSE CM daily parquet from Upstox historical candles (cached per symbol).

Idempotent: skips HTTP for units already in per-symbol cache or gzip API cache.
Plans missing dates first (max Upstox window per request), then fetches.

Use --plan-only to list gaps without API calls; --start/--end set the interval.
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
    plan_upstox_universe,
    print_upstox_sync_plan,
    sync_upstox_incremental,
    sync_upstox_universe,
    upstox_access_token,
)


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def resolve_symbols(*, symbol: str | None, symbols: str | None) -> list[str] | None:
    """``--symbol`` and ``--symbols`` (comma-separated); merged when both are set."""
    out: list[str] = []
    if symbol:
        out.append(symbol.strip().upper())
    if symbols:
        out.extend(s.strip().upper() for s in symbols.split(",") if s.strip())
    if not out:
        return None
    seen: set[str] = set()
    deduped: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=None, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", type=parse_date, default=None, help="YYYY-MM-DD inclusive (default: today)")
    parser.add_argument(
        "--interval",
        choices=("day", "month", "week"),
        default="day",
        help="Upstox candle interval (default: day)",
    )
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
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        metavar="TICKER",
        help="Single NSE symbol (e.g. RELIANCE); same as one entry in --symbols",
    )
    parser.add_argument(
        "--symbols",
        type=str,
        default=None,
        help="Comma-separated NSE symbols (default: all EQ)",
    )
    parser.add_argument("--max-symbols", type=int, default=None, help="Limit symbols (debug)")
    parser.add_argument("--refresh-cache", action="store_true", help="Re-fetch API even if gzip cache exists")
    parser.add_argument("--overwrite", action="store_true", help="Replace valid daily parquet files")
    parser.add_argument("--no-materialize", action="store_true", help="Fetch symbol histories only")
    parser.add_argument("--dry-run", action="store_true", help="Plan per symbol; no HTTP or parquet writes")
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Print missing dates and API chunk plan, then exit (no HTTP)",
    )
    parser.add_argument(
        "--list-dates",
        action="store_true",
        help="With --plan-only: print each missing weekday lacking nse_cm parquet (max 50)",
    )
    parser.add_argument("--quiet-plan", action="store_true", help="Skip pre-sync plan summary on stdout")
    args = parser.parse_args()

    if not upstox_access_token():
        print("Set UPSTOX_ACCESS_TOKEN or UPSTOX_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    client = UpstoxClient()
    parquet_root = PARQUET_DATA_DIR
    end = args.end or date.today()
    symbols = resolve_symbols(symbol=args.symbol, symbols=args.symbols)

    common = dict(
        interval=args.interval,
        symbols=symbols,
        max_symbols=args.max_symbols,
        refresh_cache=args.refresh_cache,
        overwrite_parquet=args.overwrite,
    )

    if args.plan_only:
        start = args.start or (latest_local_parquet_date(parquet_root) or date(2005, 1, 1))
        if args.incremental and args.start is None:
            start = max(start, end - timedelta(days=30))
        plan = plan_upstox_universe(
            parquet_root,
            start,
            end,
            client=client,
            **common,
        )
        print_upstox_sync_plan(plan, list_missing_dates=args.list_dates)
        return

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
            start=args.start,
            end=end,
            materialize=not args.no_materialize,
            dry_run=args.dry_run,
            plan_before=not args.quiet_plan,
            list_missing_dates=args.list_dates,
            **common,
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
            materialize=not args.no_materialize,
            dry_run=args.dry_run,
            plan_before=not args.quiet_plan,
            list_missing_dates=args.list_dates,
            **common,
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