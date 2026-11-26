#!/usr/bin/env python3
"""₹1L shared book: Regime + SMA12 + 3m mom with vol-sized positions (dual_mom_portfolio)."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from itertools import product
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.dual_mom_portfolio import (  # noqa: E402
    DUAL_MOM_PORTFOLIO_BALANCED_100K,
    DUAL_MOM_PORTFOLIO_VERSION,
    DEFAULT_DUAL_MOM_PORTFOLIO_PARAMS,
    backtest_dual_mom_portfolio,
    risk_stats,
    summarize_dual_mom_portfolio,
)

PANEL = _REPO / "data" / "cache" / "nifty50_monthly_panel.parquet"
REPORTS = _REPO / "results" / "reports"
TRADES = REPORTS / "nifty50_dual_mom_book_100k_trades.csv"
EQUITY = REPORTS / "nifty50_dual_mom_book_100k_equity.csv"
SUMMARY_JSON = REPORTS / "nifty50_dual_mom_book_100k_summary.json"


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


def score(cagr: float, mdd: float, ruin_pct: float, min_eq_pct: float) -> float:
    """Higher is better: CAGR with DD / ruin penalties."""
    dd_pen = max(0.0, abs(mdd) - 14.0) * 0.6
    ruin_pen = ruin_pct * 0.08
    floor_pen = max(0.0, 55.0 - min_eq_pct) * 0.4
    return cagr - dd_pen - ruin_pen - floor_pen


def tune_panel(monthly: pd.DataFrame, initial: float, max_runs: int, *, fast: bool = True) -> dict:
    grid = {
        "top_k": [3, 4, 5],
        "target_vol_annual": [0.11, 0.13, 0.15],
        "gross_cap": [0.80, 0.85, 0.90],
        "max_name_weight": [0.18, 0.22],
        "dd_hard_stop_pct": [8.0, 9.0, 10.0],
        "dd_soft_stop_pct": [0.0, 6.0],
        "dd_soft_gross_scale": [0.35, 0.45],
    }
    keys = list(grid)
    combos = list(product(*(grid[k] for k in keys)))
    if len(combos) > max_runs:
        import random

        random.seed(7)
        combos = random.sample(combos, max_runs)
    best: dict | None = None
    best_score = -1e9
    rows: list[dict] = []
    for vals in combos:
        over = dict(zip(keys, vals))
        if over["dd_soft_stop_pct"] == 0:
            over["dd_soft_gross_scale"] = 0.5
        p = {**DEFAULT_DUAL_MOM_PORTFOLIO_PARAMS, **over, "initial_capital": initial}
        tr, eq = backtest_dual_mom_portfolio(monthly, p)
        if eq.empty:
            continue
        s = asdict(summarize_dual_mom_portfolio(tr, eq, p))
        if fast:
            min_eq_pct = 100.0 * float(eq["equity"].min()) / initial
            r_ruin = 0.0
        else:
            r = risk_stats(eq, initial, n_boot=200)
            min_eq_pct = r.min_equity_pct
            r_ruin = r.bootstrap_ruin_50pct
        sc = score(s["cagr_pct"], s["max_drawdown_pct"], r_ruin, min_eq_pct)
        row = {
            **over,
            "cagr_pct": s["cagr_pct"],
            "max_dd_pct": s["max_drawdown_pct"],
            "min_eq_pct": min_eq_pct,
            "ruin50_boot_pct": r_ruin,
            "score": sc,
        }
        rows.append(row)
        if sc > best_score:
            best_score = sc
            best = p
    if best is None:
        return DUAL_MOM_PORTFOLIO_BALANCED_100K
    tuning = pd.DataFrame(rows).sort_values("score", ascending=False)
    tuning.to_csv(REPORTS / "nifty50_dual_mom_book_100k_tuning.csv", index=False)
    print(f"Tuning: {len(rows)} configs → {REPORTS / 'nifty50_dual_mom_book_100k_tuning.csv'}")
    print(tuning.head(5).to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return best


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--capital", "-c", type=float, default=100_000.0)
    ap.add_argument("--tune", action="store_true", help="Grid-search sizing (else use balanced preset)")
    ap.add_argument("--max-tune", type=int, default=324)
    ap.add_argument("--full-risk", action="store_true", help="Bootstrap ruin estimate during tuning (slow)")
    args = ap.parse_args()

    monthly = load_panel()
    initial = float(args.capital)
    if args.tune:
        params = tune_panel(monthly, initial, args.max_tune, fast=not args.full_risk)
    else:
        params = {**DUAL_MOM_PORTFOLIO_BALANCED_100K, "initial_capital": initial}

    trades, equity = backtest_dual_mom_portfolio(monthly, params)
    summary = asdict(summarize_dual_mom_portfolio(trades, equity, params))
    risk = risk_stats(equity, initial)

    REPORTS.mkdir(parents=True, exist_ok=True)
    export = export_trades(trades, initial, params)
    export.to_csv(TRADES, index=False)
    equity.to_csv(EQUITY, index=False)

    payload = {
        "version": DUAL_MOM_PORTFOLIO_VERSION,
        "initial_capital_inr": initial,
        "params": params,
        "summary": summary,
        "risk": asdict(risk),
        "n_round_trips": len(export),
    }
    SUMMARY_JSON.write_text(json.dumps(payload, indent=2, default=float), encoding="utf-8")
    export_copy = _REPO / "strategies" / "exports" / "nifty50_dual_mom_book_100k_trades.csv"
    export_copy.parent.mkdir(parents=True, exist_ok=True)
    export_copy.write_bytes(TRADES.read_bytes())

    print(f"\n=== Dual-mom book ₹{initial:,.0f} ===")
    print(
        f"CAGR {summary['cagr_pct']:.2f}%  |  max DD {summary['max_drawdown_pct']:.2f}%  |  "
        f"final ₹{summary['final_equity']:,.0f}"
    )
    print(
        f"Min equity {risk.min_equity_pct:.1f}% of start (₹{risk.min_equity_inr:,.0f})  |  "
        f"bootstrap P(ruin below 50% start) ~ {risk.bootstrap_ruin_50pct:.1f}%"
    )
    print(f"Sizing: top_k={params['top_k']}  gross_cap={params['gross_cap']}  "
          f"target_vol={params['target_vol_annual']}  max_wt={params['max_name_weight']}")
    print(f"\nTrades:  {TRADES} ({len(export)} rows)")
    print(f"Equity:  {EQUITY}")
    print(f"Summary: {SUMMARY_JSON}")


if __name__ == "__main__":
    main()