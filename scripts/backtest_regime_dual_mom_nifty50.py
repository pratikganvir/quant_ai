#!/usr/bin/env python3
"""Regime + SMA12 + 3m momentum backtest on Nifty 50 — combined trades CSV."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "results" / "reports"
TRADES = REPORTS / "nifty50_regime_dual_mom_trades.csv"
SUMMARY = REPORTS / "nifty50_regime_dual_mom_summary.csv"
SIGNALS = REPORTS / "nifty50_regime_dual_mom_signals.csv"
EXPORT_COPY = ROOT / "strategies" / "exports" / "nifty50_regime_dual_mom_trades.csv"
SCAN = ROOT / "scripts" / "scan_regime_sma12_monthly.py"


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    cmd = [
        sys.executable,
        str(SCAN),
        "--universe",
        "nifty50",
        "--variant",
        "dual_mom",
        "--export-trades",
        str(TRADES),
        "--export-summary",
        str(SUMMARY),
        "--export-signals",
        str(SIGNALS),
        *sys.argv[1:],
    ]
    subprocess.check_call(cmd, cwd=ROOT, env=env)
    if TRADES.is_file():
        EXPORT_COPY.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_COPY.write_bytes(TRADES.read_bytes())
    print(f"\nCombined trades (Regime + SMA12 + 3m mom): {TRADES}")
    print(f"Copy:                                      {EXPORT_COPY}")
    print(f"Per-symbol summary:                        {SUMMARY}")
    print(f"Latest signals:                              {SIGNALS}")


if __name__ == "__main__":
    main()