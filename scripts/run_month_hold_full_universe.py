#!/usr/bin/env python3
"""Backtest default month-hold portfolio on cached full MCP monthly universe."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.month_hold_portfolio import (  # noqa: E402
    MONTH_HOLD_VERSION,
    backtest_month_hold_portfolio,
    evaluate_month_hold_walkforward,
    resolve_month_hold_params,
    summarize_month_hold,
)

CACHE = _REPO / "data" / "cache" / "full_universe_monthly.parquet"
OUT = _REPO / "data" / "cache" / "month_hold_full_summary.json"
TRADES_CSV = _REPO / "data" / "cache" / "month_hold_trades.csv"


def main() -> None:
    if not CACHE.is_file():
        print("Missing cache — run scripts/run_regime_momentum_full_universe.py first", file=sys.stderr)
        sys.exit(1)
    initial_capital = 100_000.0
    profile = "growth"
    if "--capital" in sys.argv or "-c" in sys.argv:
        flag = "--capital" if "--capital" in sys.argv else "-c"
        initial_capital = float(sys.argv[sys.argv.index(flag) + 1])
    if "--profile" in sys.argv:
        profile = sys.argv[sys.argv.index("--profile") + 1]
    monthly = pd.read_parquet(CACHE)
    monthly["Date"] = pd.to_datetime(monthly["Date"])
    params = resolve_month_hold_params(profile, {"initial_capital": initial_capital})
    trades, equity = backtest_month_hold_portfolio(monthly, params, profile=profile)
    trade_export = trades.rename(
        columns={
            "entry_date": "EntryDate",
            "entry_px": "EntryPrice",
            "exit_date": "ExitDate",
            "exit_px": "ExitPrice",
            "shares": "Shares",
            "pnl": "ProfitLossINR",
        }
    )[["Symbol", "EntryDate", "EntryPrice", "ExitDate", "ExitPrice", "Shares"]]
    trade_export["EntryNotionalINR"] = trade_export["Shares"] * trade_export["EntryPrice"]
    trade_export["ExitNotionalINR"] = trade_export["Shares"] * trade_export["ExitPrice"]
    trade_export["ProfitLossINR"] = trades["pnl"].values
    trade_export["ProfitLossPercent"] = (
        trade_export["ExitPrice"] / trade_export["EntryPrice"] - 1.0
    ) * 100.0
    trade_export.insert(0, "InitialCapitalINR", initial_capital)
    trade_export["EntryDate"] = pd.to_datetime(trade_export["EntryDate"]).dt.strftime("%Y-%m-%d")
    trade_export["ExitDate"] = pd.to_datetime(trade_export["ExitDate"]).dt.strftime("%Y-%m-%d")
    trade_export.to_csv(TRADES_CSV, index=False)
    summary = asdict(summarize_month_hold(trades, equity, params))
    wf = evaluate_month_hold_walkforward(monthly, params)
    payload = {
        "version": MONTH_HOLD_VERSION,
        "n_symbols": int(monthly["Symbol"].nunique()),
        "params": params,
        "max_concurrent_positions": int(params["top_k"]),
        "summary": summary,
        "walkforward": {k: wf[k] for k in wf if k not in ("ok", "params")},
    }
    OUT.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    print(json.dumps({"summary": summary, "walkforward": payload["walkforward"]}, indent=2))
    print(f"Wrote {OUT}")
    print(f"Wrote {len(trade_export)} trades to {TRADES_CSV}")


if __name__ == "__main__":
    main()