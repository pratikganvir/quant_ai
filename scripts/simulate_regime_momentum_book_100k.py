#!/usr/bin/env python3
"""₹1L shared book: regime_momentum portfolio — trades CSV matches dual_mom book export."""

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
from quant.research.regime_momentum_portfolio import (  # noqa: E402
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    REGIME_MOMENTUM_3X_PARAMS,
    REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS,
    REGIME_MOMENTUM_VERSION,
    backtest_regime_momentum_portfolio,
    summarize_regime_momentum,
)

PANEL = _REPO / "data" / "cache" / "nifty50_monthly_panel.parquet"
REPORTS = _REPO / "results" / "reports"
EXPORTS = _REPO / "strategies" / "exports"

PROFILE_PARAMS = {
    "default": DEFAULT_REGIME_MOMENTUM_PARAMS,
    "balanced": REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS,
    "3x": REGIME_MOMENTUM_3X_PARAMS,
}


def load_panel() -> pd.DataFrame:
    if not PANEL.is_file():
        raise SystemExit(f"Missing {PANEL} — run compare_improved_regime_strategies.py first")
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
    out["margin enabled"] = bool(params.get("margin_enabled"))
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
        "margin enabled",
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
    copy = EXPORTS / out_path.name
    copy.write_bytes(out_path.read_bytes())


def paths_for(profile: str) -> tuple[Path, Path, Path]:
    tag = "default" if profile == "default" else profile
    stem = f"nifty50_regime_momentum_book_100k" if tag == "default" else f"nifty50_regime_momentum_{tag}_100k"
    return (
        REPORTS / f"{stem}_trades.csv",
        REPORTS / f"{stem}_equity.csv",
        REPORTS / f"{stem}_summary.json",
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", "-c", type=float, default=100_000.0)
    ap.add_argument(
        "--profile",
        choices=tuple(PROFILE_PARAMS),
        default="default",
        help="default ~22%% CAGR; balanced ~19.5%%; 3x margin book (gross cap 3)",
    )
    ap.add_argument(
        "--all-profiles",
        action="store_true",
        help="Export default + balanced in one run",
    )
    args = ap.parse_args()

    monthly = load_panel()
    initial = float(args.capital)
    profiles = list(PROFILE_PARAMS) if args.all_profiles else [args.profile]

    REPORTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)

    for profile in profiles:
        params = {**PROFILE_PARAMS[profile], "initial_capital": initial}
        trades, equity = backtest_regime_momentum_portfolio(monthly, params)
        summary = asdict(summarize_regime_momentum(trades, equity, params))
        risk = risk_stats(equity, initial)

        trades_csv, equity_csv, summary_json = paths_for(profile)
        export = export_trades(trades, initial, params)
        export.to_csv(trades_csv, index=False)
        equity.to_csv(equity_csv, index=False)
        monthly_csv = trades_csv.with_name(trades_csv.stem.replace("_trades", "_monthly_returns") + ".csv")
        _write_monthly_returns(equity, monthly_csv)

        payload = {
            "version": REGIME_MOMENTUM_VERSION,
            "profile": profile,
            "initial_capital_inr": initial,
            "params": params,
            "summary": summary,
            "risk": asdict(risk),
            "n_round_trips": len(export),
        }
        summary_json.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")

        export_name = trades_csv.name
        (EXPORTS / export_name).write_bytes(trades_csv.read_bytes())
        (EXPORTS / monthly_csv.name).write_bytes(monthly_csv.read_bytes())

        print(f"\n=== Regime momentum ({profile})  ₹{initial:,.0f} ===")
        print(
            f"CAGR {summary['cagr_pct']:.2f}%  |  max DD {summary['max_drawdown_pct']:.2f}%  |  "
            f"final ₹{summary['final_equity']:,.0f}"
        )
        print(f"Min equity {risk.min_equity_pct:.1f}% of start  |  trades {len(export)}")
        print(f"Trades:  {trades_csv}")
        print(f"Export:  {EXPORTS / export_name}")
        print(f"Equity:  {equity_csv}")
        print(f"Summary: {summary_json}")


if __name__ == "__main__":
    main()