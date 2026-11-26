#!/usr/bin/env python3
"""Regime + SMA12 backtest on full Nifty 50 — one combined trades CSV."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "results" / "reports"
TRADES = REPORTS / "nifty50_regime_sma12_trades.csv"
SUMMARY = REPORTS / "nifty50_regime_sma12_summary.csv"
SIGNALS = REPORTS / "nifty50_regime_sma12_signals.csv"
SCAN = ROOT / "scripts" / "scan_regime_sma12_monthly.py"


def main() -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    cmd = [
        sys.executable,
        str(SCAN),
        "--universe",
        "nifty50",
        "--export-trades",
        str(TRADES),
        "--export-summary",
        str(SUMMARY),
        "--export-signals",
        str(SIGNALS),
        *sys.argv[1:],
    ]
    subprocess.check_call(cmd, cwd=ROOT, env=env)
    print(f"\nCombined trades (all Nifty 50): {TRADES}")
    print(f"Per-symbol summary:             {SUMMARY}")
    print(f"Latest signals:                 {SIGNALS}")


if __name__ == "__main__":
    main()