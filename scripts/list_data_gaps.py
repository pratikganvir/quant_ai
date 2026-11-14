#!/usr/bin/env python3
"""List weekdays missing parquet, excluding likely NSE holidays (TejHQ no_rows / empty cache)."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from quant.config import PARQUET_DATA_DIR, TEJHQ_MANIFEST_PATH
from quant.data.tejhq import TejHQClient, classify_data_gaps, gap_ranges


def parse_date(raw: str) -> date:
    return datetime.strptime(raw, "%Y-%m-%d").date()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=parse_date, default=date(2011, 1, 4))
    parser.add_argument("--end", type=parse_date, default=None)
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Write gap dates to CSV (one column: date)",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="Treat never-fetched days as gaps (default: list them separately)",
    )
    args = parser.parse_args()

    end = args.end or TejHQClient().latest_trading_date() or date.today()
    if end < args.start:
        raise SystemExit("end before start")

    result = classify_data_gaps(
        PARQUET_DATA_DIR,
        args.start,
        end,
        manifest_path=TEJHQ_MANIFEST_PATH,
    )

    gaps = list(result["gaps"])
    if args.include_unknown:
        gaps = sorted(set(gaps) | set(result["unknown_never_fetched"]))

    print(f"Range: {args.start} → {end}")
    print(f"Weekdays without valid parquet: {len(result['missing_weekdays_total'])}")
    print(f"Likely holidays (excluded):      {len(result['non_trading_skipped'])}")
    print(f"Never fetched (no cache row):    {len(result['unknown_never_fetched'])}")
    print(f"Sync gaps (need data):           {len(gaps)}")

    ranges = gap_ranges(gaps)
    print("\n=== Gap ranges (contiguous) ===")
    for a, b, n in ranges[:80]:
        if a == b:
            print(f"  {a}  ({n} day)")
        else:
            print(f"  {a} → {b}  ({n} weekdays)")
    if len(ranges) > 80:
        print(f"  ... {len(ranges) - 80} more ranges")

    if not args.include_unknown and result["unknown_never_fetched"]:
        print("\n=== Never fetched (run --fill-gaps; not counted as holiday) ===")
        u_ranges = gap_ranges(result["unknown_never_fetched"])
        for a, b, n in u_ranges[:20]:
            print(f"  {a} → {b}  ({n})")
        if len(u_ranges) > 20:
            print(f"  ... {len(u_ranges) - 20} more")

    if args.csv and gaps:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        args.csv.write_text("date\n" + "\n".join(d.isoformat() for d in sorted(gaps)) + "\n")
        print(f"\nWrote {len(gaps)} dates to {args.csv}")


if __name__ == "__main__":
    main()