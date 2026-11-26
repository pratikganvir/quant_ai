#!/usr/bin/env python3
"""Universe scan: multibagger setup entries; exits via multibagger_exits (no hold calendar cap)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.multibagger_per_symbol import (  # noqa: E402
    DEFAULT_RUNNER_PARAMS,
    multibagger_round_trips,
    regime_series,
)
from quant.research.regime_momentum_portfolio import _load_benchmark_close  # noqa: E402

PANEL = _REPO / "data" / "cache" / "mcp_monthly_universe_min48.parquet"
REPORTS = _REPO / "results" / "reports"
EXPORTS = _REPO / "strategies" / "exports"
DEFAULT_TRADES = REPORTS / "multibagger_universe_trades.csv"
DEFAULT_WINNERS = REPORTS / "multibagger_trades_100pct_plus.csv"
EXPORT_TRADES = EXPORTS / "multibagger_universe_trades.csv"
EXPORT_WINNERS = EXPORTS / "multibagger_trades_100pct_plus.csv"
BENCH = "NIFTYBEES"


def load_panel(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--panel", type=Path, default=PANEL)
    ap.add_argument("--export-trades", type=Path, default=DEFAULT_TRADES)
    ap.add_argument("--export-winners", type=Path, default=DEFAULT_WINNERS)
    ap.add_argument("--min-profit-pct", type=float, default=100.0, help="also write trades at or above this %%")
    ap.add_argument("--liquidity-top", type=int, default=400, help="0 = all symbols with panel history")
    args = ap.parse_args()

    if not args.panel.is_file():
        raise SystemExit(f"Missing panel {args.panel} — run analyze_multibagger_monthly_mcp.py")

    panel = load_panel(args.panel)
    bench_df = panel.loc[panel["Symbol"] == BENCH, ["Date", "Close"]].drop_duplicates("Date").sort_values("Date")
    bench = _load_benchmark_close(BENCH, panel, bench_df)
    regime = regime_series(bench, sma=int(DEFAULT_RUNNER_PARAMS["regime_sma_months"]))

    syms = sorted(panel.loc[panel["Symbol"] != BENCH, "Symbol"].unique())
    if args.liquidity_top > 0:
        med = (
            panel.loc[panel["Symbol"] != BENCH]
            .groupby("Symbol")
            .apply(lambda g: g["Volume"].tail(12).median())
            .sort_values(ascending=False)
        )
        syms = list(med.head(args.liquidity_top).index)

    parts: list[pd.DataFrame] = []
    for i, sym in enumerate(syms, 1):
        g = panel.loc[panel["Symbol"] == sym, ["Date", "Open", "High", "Low", "Close", "Volume"]]
        rt = multibagger_round_trips(sym, g, bench, regime)
        if not rt.empty:
            parts.append(rt)
        if i % 100 == 0 or i == len(syms):
            print(f"  scanned {i}/{len(syms)} symbols, legs so far {sum(len(p) for p in parts)}")

    if not parts:
        raise SystemExit("No round trips")

    all_trades = pd.concat(parts, ignore_index=True).sort_values(["entry date", "symbol"])
    winners = all_trades.loc[all_trades["profit percent"] >= float(args.min_profit_pct)].copy()

    args.export_trades.parent.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    all_trades.to_csv(args.export_trades, index=False)
    winners.to_csv(args.export_winners, index=False)
    EXPORT_TRADES.write_bytes(args.export_trades.read_bytes())
    EXPORT_WINNERS.write_bytes(args.export_winners.read_bytes())

    n = len(all_trades)
    w = len(winners)
    print(f"\nRound trips: {n}  |  >={args.min_profit_pct:.0f}%: {w} ({100*w/n:.1f}%)")
    n50 = (all_trades["profit percent"] >= 50).sum()
    print(f">=50%: {n50} ({100*n50/n:.1f}%)  |  median hold {all_trades['hold days'].median():.0f}d")
    print(f"All trades:   {args.export_trades}")
    print(f">={args.min_profit_pct:.0f}% list: {args.export_winners}")
    print(f"Export copy:  {EXPORT_TRADES}")


if __name__ == "__main__":
    main()