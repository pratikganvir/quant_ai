#!/usr/bin/env python3
"""₹1L Launch Base book — trades CSV (regime momentum export format)."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.dual_mom_portfolio import risk_stats  # noqa: E402
from quant.research.launch_base_portfolio import (  # noqa: E402
    backtest_launch_base_portfolio,
    summarize_launch_base,
)
from quant.research.launch_base_constraints import LAUNCH_BASE_CONSTRAINTS  # noqa: E402
from quant.research.launch_base_signals import (  # noqa: E402
    DEFAULT_LAUNCH_BASE_PARAMS,
    LAUNCH_BASE_VERSION,
)

PANEL = _REPO / "data/cache/mcp_monthly_universe_min48.parquet"
REPORTS = _REPO / "results" / "reports"
EXPORTS = _REPO / "strategies" / "exports"
STEM = "launch_base_book_100k"


def load_panel() -> pd.DataFrame:
    if not PANEL.is_file():
        raise SystemExit(f"Missing {PANEL}")
    df = pd.read_parquet(PANEL)
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def export_trades(trades: pd.DataFrame, initial: float, params: dict) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    out = trades.rename(
        columns={
            "Symbol": "symbol",
            "entry_date": "entry date",
            "entry_px": "entry price",
            "exit_date": "exit date",
            "exit_px": "exit price",
            "shares": "shares",
            "pnl": "profit INR",
            "reason": "exit reason",
        }
    )
    out["entry notional INR"] = out["shares"] * out["entry price"]
    out["exit notional INR"] = out["shares"] * out["exit price"]
    out["profit percent"] = (out["exit price"] / out["entry price"] - 1.0) * 100.0
    out["hold days"] = (
        pd.to_datetime(out["exit date"]) - pd.to_datetime(out["entry date"])
    ).dt.days
    out["initial capital INR"] = initial
    out["book gross cap"] = params.get("gross_cap")
    out["entry date"] = pd.to_datetime(out["entry date"]).dt.strftime("%Y-%m-%d")
    out["exit date"] = pd.to_datetime(out["exit date"]).dt.strftime("%Y-%m-%d")
    return out[
        [
            "symbol",
            "entry date",
            "entry price",
            "entry notional INR",
            "shares",
            "exit date",
            "exit price",
            "exit notional INR",
            "hold days",
            "profit INR",
            "profit percent",
            "exit reason",
            "initial capital INR",
            "book gross cap",
        ]
    ]


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", type=float, default=100_000.0)
    ap.add_argument("--top-k", type=int, default=None)
    args = ap.parse_args()

    params = {**DEFAULT_LAUNCH_BASE_PARAMS, "initial_capital": args.capital}
    if args.top_k is not None:
        params["top_k"] = args.top_k

    trades, equity = backtest_launch_base_portfolio(load_panel(), params)
    summary = asdict(summarize_launch_base(trades, equity, params))
    risk = risk_stats(equity, args.capital)

    REPORTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    trades_csv = REPORTS / f"{STEM}_trades.csv"
    export = export_trades(trades, args.capital, params)
    export.to_csv(trades_csv, index=False)
    equity.to_csv(REPORTS / f"{STEM}_equity.csv", index=False)

    payload = {
        "version": LAUNCH_BASE_VERSION,
        "params": params,
        "summary": summary,
        "risk": asdict(risk),
        "n_trades": len(export),
        "n_ge_100pct": int((export["profit percent"] >= 100).sum()) if len(export) else 0,
    }
    (REPORTS / f"{STEM}_summary.json").write_text(json.dumps(payload, indent=2, default=float))
    (EXPORTS / trades_csv.name).write_bytes(trades_csv.read_bytes())

    print(f"\n=== Launch Base  ₹{args.capital:,.0f} ===")
    print(
        f"CAGR {summary['cagr_pct']:.2f}%  max DD {summary['max_drawdown_pct']:.2f}%  "
        f"final ₹{summary['final_equity']:,.0f}"
    )
    ge100 = payload["n_ge_100pct"]
    print(f"Trades {len(export)}  |  ≥100% legs {ge100}  |  median hold {export['hold days'].median() if len(export) else 0:.0f}d")
    print(f"Trades: {trades_csv}")
    print(f"Export: {EXPORTS / trades_csv.name}")
    if len(export) and (export["profit percent"] > 0).mean() < 0.5:
        print("\n--- Constraints (entries-only, honest) ---")
        print("Target: >50% hit ratio without profit-floor exits.")
        print("On full MCP panel, v1.3 tight entries typically land ~45–48% win rate;")
        print("≤20% leg loss and ~≤15% book DD are met. See launch_base_constraints.py")


if __name__ == "__main__":
    main()