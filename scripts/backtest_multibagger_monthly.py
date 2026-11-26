#!/usr/bin/env python3
"""Full multibagger backtest: ₹1L runner book + liquid-universe per-symbol round trips."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "results" / "reports"
EXPORTS = ROOT / "strategies" / "exports"
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}

RUNNER_TRADES = REPORTS / "multibagger_runner_book_100k_trades.csv"
RUNNER_EQUITY = REPORTS / "multibagger_runner_book_100k_equity.csv"
RUNNER_SUMMARY = REPORTS / "multibagger_runner_book_100k_summary.json"
UNIVERSE_TRADES = REPORTS / "multibagger_universe_trades.csv"
WINNERS = REPORTS / "multibagger_trades_100pct_plus.csv"
UNIVERSE_SUMMARY = REPORTS / "multibagger_universe_symbol_summary.csv"
BACKTEST_SUMMARY = REPORTS / "multibagger_backtest_summary.json"


def _run(cmd: list[str]) -> None:
    subprocess.check_call(cmd, cwd=ROOT, env=ENV)


def _universe_symbol_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    g = trades.groupby("symbol", as_index=False).agg(
        n_trades=("profit percent", "count"),
        n_ge_100pct=("profit percent", lambda s: int((s >= 100).sum())),
        n_ge_50pct=("profit percent", lambda s: int((s >= 50).sum())),
        median_return_pct=("profit percent", "median"),
        best_return_pct=("profit percent", "max"),
        median_hold_days=("hold days", "median"),
        win_rate_pct=("profit percent", lambda s: float((s > 0).mean()) * 100),
    )
    return g.sort_values("best_return_pct", ascending=False)


def main() -> None:
    extra = sys.argv[1:]
    full_univ = "--full-universe" in extra
    sim_extra = [a for a in extra if a != "--full-universe"]
    REPORTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)

    print("=== Multibagger backtest (runner book) ===", flush=True)
    _run(
        [
            sys.executable,
            str(ROOT / "scripts" / "simulate_multibagger_thrust_book_100k.py"),
            "--profile",
            "runner",
            *sim_extra,
        ]
    )

    label = "full MCP universe" if full_univ else "top 400 liquid"
    print(f"\n=== Multibagger backtest (universe scan, {label}) ===", flush=True)
    scan_cmd = [
        sys.executable,
        str(ROOT / "scripts" / "scan_multibagger_monthly.py"),
        "--export-trades",
        str(UNIVERSE_TRADES),
        "--export-winners",
        str(WINNERS),
        "--liquidity-top",
        "400",
    ]
    if full_univ:
        scan_cmd = [c for c in scan_cmd if c != "400" and c != "--liquidity-top"] + ["--liquidity-top", "0"]
    _run(scan_cmd)

    book = json.loads(RUNNER_SUMMARY.read_text(encoding="utf-8"))
    u = pd.read_csv(UNIVERSE_TRADES)
    sym = _universe_symbol_summary(u)
    sym.to_csv(UNIVERSE_SUMMARY, index=False)
    (EXPORTS / UNIVERSE_SUMMARY.name).write_bytes(UNIVERSE_SUMMARY.read_bytes())

    payload = {
        "runner_book": {
            "trades_csv": str(RUNNER_TRADES),
            "equity_csv": str(RUNNER_EQUITY),
            **book.get("summary", {}),
            "n_trades": book.get("n_round_trips"),
            "risk": book.get("risk"),
        },
        "universe_scan": {
            "full_universe": full_univ,
            "trades_csv": str(UNIVERSE_TRADES),
            "winners_100pct_csv": str(WINNERS),
            "symbol_summary_csv": str(UNIVERSE_SUMMARY),
            "n_round_trips": len(u),
            "n_ge_100pct": int((u["profit percent"] >= 100).sum()),
            "n_ge_50pct": int((u["profit percent"] >= 50).sum()),
            "median_hold_days": float(u["hold days"].median()),
            "exit_reason_counts": u["exit reason"].value_counts().astype(int).to_dict(),
        },
    }
    BACKTEST_SUMMARY.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")

    rb = payload["runner_book"]
    us = payload["universe_scan"]
    print("\n=== Results ===")
    print(
        f"Runner book: CAGR {rb.get('cagr_pct', 0):.2f}%  "
        f"max DD {rb.get('max_drawdown_pct', 0):.2f}%  "
        f"final ₹{rb.get('final_equity', 0):,.0f}  trades {rb.get('n_trades')}"
    )
    print(
        f"Universe:    legs {us['n_round_trips']}  ≥100% {us['n_ge_100pct']}  "
        f"≥50% {us['n_ge_50pct']}  median hold {us['median_hold_days']:.0f}d"
    )
    print(f"Summary JSON: {BACKTEST_SUMMARY}")
    print(f"Export trades: {EXPORTS / RUNNER_TRADES.name}")


if __name__ == "__main__":
    main()