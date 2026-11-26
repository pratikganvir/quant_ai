#!/usr/bin/env python3
"""₹1L Conviction Peak book — exits calibrated to multibagger peak-giveback study."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.conviction_peak_portfolio import (  # noqa: E402
    backtest_conviction_peak_portfolio,
    summarize_conviction_peak,
)
from quant.research.conviction_peak_signals import (  # noqa: E402
    CONVICTION_PEAK_VERSION,
    DRAWDOWN_CALIBRATION,
    DEFAULT_CONVICTION_PEAK_PARAMS,
)
from quant.research.dual_mom_portfolio import risk_stats  # noqa: E402

PANEL = _REPO / "data/cache/mcp_monthly_universe_min48.parquet"
REPORTS = _REPO / "results" / "reports"
EXPORTS = _REPO / "strategies" / "exports"
STEM = "conviction_peak_book_100k"


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

    params = {**DEFAULT_CONVICTION_PEAK_PARAMS, "initial_capital": args.capital}
    if args.top_k is not None:
        params["top_k"] = args.top_k

    trades, equity = backtest_conviction_peak_portfolio(load_panel(), params)
    summary = asdict(summarize_conviction_peak(trades, equity, params))
    risk = risk_stats(equity, args.capital)

    REPORTS.mkdir(parents=True, exist_ok=True)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    trades_csv = REPORTS / f"{STEM}_trades.csv"
    export = export_trades(trades, args.capital, params)
    export.to_csv(trades_csv, index=False)
    equity.to_csv(REPORTS / f"{STEM}_equity.csv", index=False)

    wins = (export["profit percent"] > 0).mean() if len(export) else 0.0
    worst = export["profit percent"].min() if len(export) else 0.0
    payload = {
        "version": CONVICTION_PEAK_VERSION,
        "drawdown_calibration": DRAWDOWN_CALIBRATION,
        "params": params,
        "summary": summary,
        "risk": asdict(risk),
        "n_trades": len(export),
        "n_ge_100pct": int((export["profit percent"] >= 100).sum()) if len(export) else 0,
        "hit_ratio": wins,
        "worst_leg_pct": worst,
    }
    (REPORTS / f"{STEM}_summary.json").write_text(json.dumps(payload, indent=2, default=float))
    (EXPORTS / trades_csv.name).write_bytes(trades_csv.read_bytes())

    print(f"\n=== Conviction Peak  ₹{args.capital:,.0f} ===")
    print(
        f"CAGR {summary['cagr_pct']:.2f}%  max DD {summary['max_drawdown_pct']:.2f}%  "
        f"final ₹{summary['final_equity']:,.0f}"
    )
    ge100 = payload["n_ge_100pct"]
    med_hold = export["hold days"].median() if len(export) else 0
    print(
        f"Trades {len(export)}  |  ≥100% legs {ge100}  |  hit {wins * 100:.1f}%  |  "
        f"worst leg {worst:.1f}%  |  median hold {med_hold:.0f}d"
    )
    print(f"Peak trails: {DRAWDOWN_CALIBRATION['peak_trail_while_building_pct']}% build / "
          f"{DRAWDOWN_CALIBRATION['peak_trail_after_double_pct']}% after 2x")
    print(f"Trades: {trades_csv}")


if __name__ == "__main__":
    main()