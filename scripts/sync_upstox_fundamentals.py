#!/usr/bin/env python3
"""Sync Upstox fundamentals (profile, ratios, statements, etc.) into local cache.

Cache layout: ``raw/upstox/fundamentals/{SYMBOL}/{resource}.json.gz``

Requires ``UPSTOX_ACCESS_TOKEN`` or ``UPSTOX_API_KEY`` in the environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from quant.data.upstox import upstox_access_token  # noqa: E402
from quant.data.upstox_fundamentals import (  # noqa: E402
    FUNDAMENTAL_RESOURCES,
    sync_fundamentals,
)


def parse_symbols(raw: str | None, symbol: str | None) -> list[str] | None:
    out: list[str] = []
    if symbol:
        out.append(symbol.strip().upper())
    if raw:
        out.extend(s.strip().upper() for s in raw.split(",") if s.strip())
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
    parser.add_argument("--symbol", type=str, default=None, help="Single NSE symbol")
    parser.add_argument("--symbols", type=str, default=None, help="Comma-separated symbols")
    parser.add_argument(
        "--resources",
        type=str,
        default=None,
        help=f"Comma-separated resources (default: all). Options: {','.join(FUNDAMENTAL_RESOURCES)}",
    )
    parser.add_argument("--max-symbols", type=int, default=None, help="Limit universe size")
    parser.add_argument("--refresh-cache", action="store_true", help="Re-fetch even if JSON exists")
    parser.add_argument("--dry-run", action="store_true", help="Count work only; no HTTP")
    parser.add_argument(
        "--all-eq",
        action="store_true",
        help="All NSE EQ symbols from Upstox instruments (default: symbols with OHLCV cache)",
    )
    parser.add_argument("--no-progress", action="store_true", help="Disable tqdm progress bar")
    args = parser.parse_args()

    if not args.dry_run and not upstox_access_token():
        print("Set UPSTOX_ACCESS_TOKEN or UPSTOX_API_KEY in .env", file=sys.stderr)
        sys.exit(1)

    resources = None
    if args.resources:
        resources = tuple(s.strip().lower() for s in args.resources.split(",") if s.strip())

    if args.all_eq and (args.symbol or args.symbols):
        print("Ignore --symbol/--symbols when using --all-eq", file=sys.stderr)

    summary = sync_fundamentals(
        None if args.all_eq else parse_symbols(args.symbols, args.symbol),
        resources=resources,
        refresh_cache=args.refresh_cache,
        dry_run=args.dry_run,
        max_symbols=args.max_symbols,
        all_eq=args.all_eq,
        show_progress=not args.no_progress,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()