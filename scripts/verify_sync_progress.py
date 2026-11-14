#!/usr/bin/env python3
"""Verify TejHQ sync is making progress (recent manifest + parquet mtimes)."""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pandas as pd

from quant.config import PARQUET_DATA_DIR, TEJHQ_MANIFEST_PATH
from quant.data.tejhq import TejHQClient, latest_local_parquet_date


def _parse_manifest_time(raw: str) -> datetime | None:
    if pd.isna(raw):
        return None
    s = str(raw).strip()
    try:
        ts = pd.Timestamp(s)
        if ts.tzinfo is None:
            return ts.to_pydatetime().replace(tzinfo=timezone.utc)
        return ts.to_pydatetime()
    except Exception:
        return None


def parquets_modified_since(root: Path, since: datetime) -> int:
    if not root.is_dir():
        return 0
    since_ts = since.timestamp()
    n = 0
    for p in root.rglob("nse_cm_*.parquet"):
        try:
            if p.stat().st_mtime >= since_ts:
                n += 1
        except OSError:
            continue
    return n


def running_sync_processes() -> list[str]:
    try:
        out = subprocess.check_output(["pgrep", "-af", "sync_tejhq"], text=True)
        return [line.strip() for line in out.splitlines() if line.strip()]
    except subprocess.CalledProcessError:
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--watch-minutes",
        type=float,
        default=0,
        help="Sleep this many minutes and compare parquet count (simple liveness test)",
    )
    parser.add_argument("--lookback-minutes", type=float, default=30)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    lookback = now - timedelta(minutes=args.lookback_minutes)

    procs = running_sync_processes()
    print("=== Sync process ===")
    if procs:
        for line in procs:
            print(" ", line)
    else:
        print("  No sync_tejhq python process found (job may have finished or not started).")
        print("  Start: nohup .venv/bin/python scripts/sync_tejhq_nse_daily.py --fill-gaps &")

    print("\n=== Paths ===")
    print("  Parquet:", PARQUET_DATA_DIR, "exists:", PARQUET_DATA_DIR.is_dir())
    print("  Manifest:", TEJHQ_MANIFEST_PATH, "exists:", TEJHQ_MANIFEST_PATH.is_file())

    try:
        api = TejHQClient().latest_trading_date()
    except Exception as exc:
        api = f"error: {exc}"
    print("  TejHQ API latest:", api)
    print("  Latest nse_cm filename date:", latest_local_parquet_date(PARQUET_DATA_DIR))

    recent_parquets = parquets_modified_since(PARQUET_DATA_DIR, lookback)
    print(f"\n=== Activity (last {args.lookback_minutes:.0f} min) ===")
    print(f"  Parquet files touched: {recent_parquets}")

    if TEJHQ_MANIFEST_PATH.is_file():
        m = pd.read_csv(TEJHQ_MANIFEST_PATH)
        m["date"] = pd.to_datetime(m["date"])
        if "fetched_at" in m.columns:
            m["_ts"] = m["fetched_at"].map(_parse_manifest_time)
            recent = m[m["_ts"].notna() & (m["_ts"] >= lookback)]
            print(f"  Manifest rows with fetched_at in window: {len(recent)}")
            if len(recent):
                by_status = recent["status"].value_counts()
                print("  Status breakdown:\n", by_status.to_string())
            print("\n  Last 8 manifest entries:")
            tail = m.sort_values("date").tail(8)
            cols = [c for c in ["date", "status", "source", "rows", "fetched_at"] if c in tail.columns]
            print(tail[cols].to_string(index=False))
        else:
            print("  (manifest has no fetched_at column)")
    else:
        print("  No manifest yet — sync may not have run.")

    alive = bool(procs) or recent_parquets > 0
    if TEJHQ_MANIFEST_PATH.is_file():
        m2 = pd.read_csv(TEJHQ_MANIFEST_PATH)
        if "fetched_at" in m2.columns:
            ts_max = m2["fetched_at"].map(_parse_manifest_time).max()
            if ts_max is not None and ts_max >= lookback:
                alive = True

    print("\n=== Verdict ===")
    if alive:
        print("  Sync appears ACTIVE or recently wrote data.")
    else:
        print("  No recent writes detected. Run sync or check logs (nohup/tejhq_fill_gaps.log).")

    if args.watch_minutes > 0:
        print(f"\n=== Watch {args.watch_minutes} min ===")
        total0 = sum(1 for _ in PARQUET_DATA_DIR.rglob("nse_cm_*.parquet")) if PARQUET_DATA_DIR.is_dir() else 0
        time.sleep(args.watch_minutes * 60)
        total1 = sum(1 for _ in PARQUET_DATA_DIR.rglob("nse_cm_*.parquet")) if PARQUET_DATA_DIR.is_dir() else 0
        delta = total1 - total0
        print(f"  nse_cm parquet count: {total0} → {total1} (Δ {delta})")
        if delta > 0:
            print("  Confirmed: new files appeared.")
        elif procs:
            print("  Process running but no new files yet (may be skipping existing days).")
        else:
            print("  No new files in watch window.")


if __name__ == "__main__":
    main()