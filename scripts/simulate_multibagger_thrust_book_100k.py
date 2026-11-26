#!/usr/bin/env python3
"""₹1L multibagger book — trades CSV like regime_momentum exports.

Profiles:
  runner  (default) — multibagger entry, rule-based exits (no calendar hold cap)
  hybrid  — monthly rebalance + momentum boost (~20% CAGR; not multibagger holds)
  v10     — legacy thrust rules
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.dual_mom_portfolio import risk_stats  # noqa: E402
from quant.research.multibagger_thrust_portfolio import (  # noqa: E402
    DEFAULT_MULTIBAGGER_THRUST_PARAMS,
    MULTIBAGGER_HYBRID_PARAMS,
    MULTIBAGGER_THRUST_V10_PARAMS,
    MULTIBAGGER_THRUST_VERSION,
    backtest_multibagger_hybrid_portfolio,
    backtest_multibagger_thrust_portfolio,
    summarize_multibagger_hybrid,
    summarize_multibagger_thrust,
)

RUNNER_PROFILE = DEFAULT_MULTIBAGGER_THRUST_PARAMS
LEGACY_PROFILES = {"v10": MULTIBAGGER_THRUST_V10_PARAMS}
STEM_RUNNER = "multibagger_runner_book_100k"
HYBRID_PROFILE = MULTIBAGGER_HYBRID_PARAMS

PANEL = _REPO / "data" / "cache" / "mcp_monthly_universe_min48.parquet"
REPORTS = _REPO / "results" / "reports"
EXPORTS = _REPO / "strategies" / "exports"


def load_panel() -> pd.DataFrame:
    if not PANEL.is_file():
        raise SystemExit(
            f"Missing {PANEL}\n"
            "Run: python scripts/analyze_multibagger_monthly_mcp.py  (builds MCP monthly cache)"
        )
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
    cols = [
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
    return out[cols]


def _write_monthly_returns(equity: pd.DataFrame, out_path: Path) -> None:
    eq = equity.sort_values("Date").copy()
    eq["Date"] = pd.to_datetime(eq["Date"])
    ret_pct = eq["equity"].pct_change() * 100
    ret_inr = eq["equity"].diff()
    month = eq["Date"].dt.strftime("%b %Y")
    out = pd.DataFrame(
        {
            "month": month,
            "equity_inr": eq["equity"].round(2),
            "return_inr": ret_inr.round(2),
            "return_pct": ret_pct.round(4),
        }
    )
    out.iloc[0, out.columns.get_loc("return_inr")] = 0.0
    out.iloc[0, out.columns.get_loc("return_pct")] = 0.0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    (EXPORTS / out_path.name).write_bytes(out_path.read_bytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", "-c", type=float, default=100_000.0)
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument(
        "--profile",
        choices=("runner", "hybrid", "v10"),
        default="runner",
        help="runner = multibagger exits; hybrid = monthly momentum book",
    )
    args = ap.parse_args()

    monthly = load_panel()
    initial = float(args.capital)

    if args.profile == "hybrid":
        params = {**HYBRID_PROFILE, "initial_capital": initial}
        stem = "multibagger_hybrid_book_100k"
        if args.top_k is not None:
            params["top_k"] = int(args.top_k)
        trades, equity = backtest_multibagger_hybrid_portfolio(monthly, params)
        summary = asdict(summarize_multibagger_hybrid(trades, equity, params))
        label = "Multibagger hybrid (monthly rebalance)"
    else:
        base = RUNNER_PROFILE if args.profile == "runner" else LEGACY_PROFILES[args.profile]
        params = {**base, "initial_capital": initial}
        stem = STEM_RUNNER if args.profile == "runner" else "multibagger_thrust_book_100k"
        if args.top_k is not None:
            params["top_k"] = int(args.top_k)
        trades, equity = backtest_multibagger_thrust_portfolio(monthly, params)
        summary = asdict(summarize_multibagger_thrust(trades, equity, params))
        label = "Multibagger runner" if args.profile == "runner" else f"Multibagger legacy ({args.profile})"

    risk = risk_stats(equity, initial)

    REPORTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)

    trades_csv = REPORTS / f"{stem}_trades.csv"
    equity_csv = REPORTS / f"{stem}_equity.csv"
    summary_json = REPORTS / f"{stem}_summary.json"
    monthly_csv = REPORTS / f"{stem}_monthly_returns.csv"

    export = export_trades(trades, initial, params)
    export.to_csv(trades_csv, index=False)
    equity.to_csv(equity_csv, index=False)
    _write_monthly_returns(equity, monthly_csv)

    payload = {
        "version": MULTIBAGGER_THRUST_VERSION,
        "profile": args.profile,
        "initial_capital_inr": initial,
        "params": params,
        "summary": summary,
        "risk": asdict(risk),
        "n_round_trips": len(export),
    }
    summary_json.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")

    (EXPORTS / trades_csv.name).write_bytes(trades_csv.read_bytes())

    print(f"\n=== {label}  ₹{initial:,.0f} ===")
    print(
        f"CAGR {summary['cagr_pct']:.2f}%  |  max DD {summary['max_drawdown_pct']:.2f}%  |  "
        f"final ₹{summary['final_equity']:,.0f}"
    )
    print(f"Min equity {risk.min_equity_pct:.1f}% of start  |  trades {len(export)}")
    print(f"Trades:  {trades_csv}")
    print(f"Export:  {EXPORTS / trades_csv.name}")
    print(f"Equity:  {equity_csv}")
    print(f"Summary: {summary_json}")


if __name__ == "__main__":
    main()