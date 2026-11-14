#!/usr/bin/env python3
"""Report last TejHQ sync / local parquet / API trading date."""

from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd

from quant.config import PARQUET_DATA_DIR, TEJHQ_CACHE_DIR, TEJHQ_MANIFEST_PATH
from quant.data.tejhq import TejHQClient, latest_local_parquet_date


def _max_snapshot_cache(cache_root: Path):
    if not cache_root.is_dir():
        return None
    dates = []
    for p in cache_root.glob("snapshots/nse/*/snapshot_*.json.gz"):
        try:
            dates.append(datetime.strptime(p.stem.replace("snapshot_", ""), "%Y%m%d").date())
        except ValueError:
            continue
    return max(dates) if dates else None


def main() -> None:
    last_parquet = (
        latest_local_parquet_date(PARQUET_DATA_DIR) if PARQUET_DATA_DIR.is_dir() else None
    )
    last_cache = _max_snapshot_cache(TEJHQ_CACHE_DIR)

    manifest_last: object = None
    manifest_written: object = None
    if TEJHQ_MANIFEST_PATH.is_file():
        m = pd.read_csv(TEJHQ_MANIFEST_PATH)
        m["date"] = pd.to_datetime(m["date"])
        manifest_last = m["date"].max().date()
        ok = m[m["status"].isin(["written", "skipped_valid_parquet"])]
        if len(ok):
            manifest_written = ok["date"].max().date()
        print(f"Manifest: {TEJHQ_MANIFEST_PATH} ({len(m)} rows)")
        print(m["status"].value_counts().to_string())
        print()
    else:
        print(f"Manifest: (none) {TEJHQ_MANIFEST_PATH}\n")

    try:
        api_date = TejHQClient().latest_trading_date()
    except Exception as exc:
        api_date = f"unavailable ({exc})"

    print("=== Summary ===")
    print(f"Parquet root:     {PARQUET_DATA_DIR}")
    print(f"Last nse_cm file:   {last_parquet}")
    print(f"Last snapshot JSON: {last_cache}  ({TEJHQ_CACHE_DIR})")
    print(f"Last manifest row:  {manifest_last}")
    print(f"Last OK manifest:   {manifest_written}")
    print(f"TejHQ API latest:   {api_date}")

    if isinstance(api_date, date) and last_parquet:
        gap = (api_date - last_parquet).days
        if gap > 0:
            print(f"\nGap: {gap} calendar day(s) behind API (run sync --incremental).")
        elif gap == 0:
            print("\nLocal parquet is through the API's latest trading date.")


if __name__ == "__main__":
    main()