#!/usr/bin/env python3
"""₹1L regime TURN book — early tide entry (exports match regime_momentum book CSVs)."""

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
    backtest_regime_momentum_portfolio,
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    summarize_regime_momentum,
)
from quant.research.regime_turn_portfolio import (  # noqa: E402
    DEFAULT_REGIME_TURN_PARAMS,
    REGIME_TURN_VERSION,
    backtest_regime_turn_portfolio,
    summarize_regime_turn,
)

PANEL = _REPO / "data" / "cache" / "nifty50_monthly_panel.parquet"
REPORTS = _REPO / "results" / "reports"
EXPORTS = _REPO / "strategies" / "exports"


def load_panel() -> pd.DataFrame:
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


def write_monthly_returns(equity: pd.DataFrame, path: Path) -> None:
    eq = equity.sort_values("Date")
    out = pd.DataFrame(
        {
            "month": pd.to_datetime(eq["Date"]).dt.strftime("%b %Y"),
            "equity_inr": eq["equity"].round(2),
            "return_inr": eq["equity"].diff().round(2),
            "return_pct": (eq["equity"].pct_change() * 100).round(4),
        }
    )
    out.iloc[0, [1, 2]] = [out.iloc[0]["equity_inr"], 0.0]
    out.iloc[0, 3] = 0.0
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False)
    (EXPORTS / path.name).write_bytes(path.read_bytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capital", "-c", type=float, default=100_000.0)
    ap.add_argument("--compare", action="store_true", help="Print vs regime_momentum 1x")
    args = ap.parse_args()
    if not PANEL.is_file():
        raise SystemExit(f"Missing {PANEL}")

    monthly = load_panel()
    ic = float(args.capital)
    params = {**DEFAULT_REGIME_TURN_PARAMS, "initial_capital": ic}
    trades, equity = backtest_regime_turn_portfolio(monthly, params)
    summary = asdict(summarize_regime_turn(trades, equity, params))
    risk = risk_stats(equity, ic)

    stem = "nifty50_regime_turn_book_100k"
    trades_path = REPORTS / f"{stem}_trades.csv"
    equity_path = REPORTS / f"{stem}_equity.csv"
    monthly_path = REPORTS / f"{stem}_monthly_returns.csv"
    summary_path = REPORTS / f"{stem}_summary.json"

    export_trades(trades, ic, params).to_csv(trades_path, index=False)
    equity.to_csv(equity_path, index=False)
    write_monthly_returns(equity, monthly_path)
    summary_path.write_text(
        json.dumps(
            {"version": REGIME_TURN_VERSION, "params": params, "summary": summary, "risk": asdict(risk)},
            indent=2,
            default=float,
        ),
        encoding="utf-8",
    )
    (EXPORTS / trades_path.name).write_bytes(trades_path.read_bytes())

    print(f"=== Regime TURN ₹{ic:,.0f} ===")
    print(
        f"CAGR {summary['cagr_pct']:.2f}%  max DD {summary['max_drawdown_pct']:.2f}%  "
        f"final ₹{summary['final_equity']:,.0f}"
    )
    print(f"Trades: {trades_path}")
    print(f"Monthly: {EXPORTS / monthly_path.name}")

    if args.compare:
        p2 = {**DEFAULT_REGIME_MOMENTUM_PARAMS, "initial_capital": ic}
        _, eq2 = backtest_regime_momentum_portfolio(monthly, p2)
        s2 = asdict(summarize_regime_momentum(pd.DataFrame(), eq2, p2))
        print(
            f"\nVersus regime_momentum 1x: CAGR {s2['cagr_pct']:.2f}%  DD {s2['max_drawdown_pct']:.2f}%"
        )


if __name__ == "__main__":
    main()