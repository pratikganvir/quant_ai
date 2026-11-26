#!/usr/bin/env python3
"""Optimize regime_momentum portfolio for CAGR vs drawdown (Calmar-style)."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from itertools import product
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.regime_momentum_portfolio import (  # noqa: E402
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    backtest_regime_momentum_portfolio,
    summarize_regime_momentum,
)

MCP = "https://mcp.marketsenseanalytics.com"
CACHE = _REPO / "data" / "cache" / "regime_opt_monthly.parquet"

# Broad liquid NSE set (MCP monthly)
SYMBOLS = """
TRENT ASIANPAINT TCS PERSISTENT ICICIBANK MARUTI HDFCBANK BAJFINANCE TATASTEEL
HINDALCO TITAN INFY ONGC JSWSTEEL BEL POLYCAB RELIANCE COALINDIA BSE DIXON
WIPRO LT SBIN AXISBANK ITC HCLTECH SUNPHARMA BHARTIARTL INDIGO HINDUNILVR M&M
TATAMOTORS NTPC POWERGRID ULTRACEMCO NESTLEIND KOTAKBANK SHRIRAMFIN ADANIENT
ADANIPORTS TECHM ETERNAL DMART NIFTYBEES
""".split()


def load_panel() -> pd.DataFrame:
    if CACHE.is_file():
        df = pd.read_parquet(CACHE)
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    parts: list[pd.DataFrame] = []
    for sym in SYMBOLS:
        try:
            raw = json.loads(
                subprocess.check_output(
                    ["curl", "-sS", "-m", "25", f"{MCP}/api/monthly/{sym}"],
                    text=True,
                )
            )
            bars = raw.get("bars") or []
            if len(bars) < 100:
                continue
            parts.append(
                pd.DataFrame(
                    [
                        {
                            "Date": b["date"],
                            "Symbol": sym,
                            "Open": b["open"],
                            "High": b["high"],
                            "Low": b["low"],
                            "Close": b["close"],
                            "Volume": b["volume"],
                        }
                        for b in bars
                    ]
                )
            )
        except Exception:
            continue
    if not parts:
        raise RuntimeError("No MCP monthly data")
    df = pd.concat(parts, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    return df


def score(cagr: float, dd: float, target_lo: float = 20.0, target_hi: float = 30.0) -> float:
    """Higher is better: reward CAGR in band, punish drawdown."""
    dd_pen = abs(dd)
    if cagr < target_lo:
        cagr_pen = (target_lo - cagr) * 2.0
    elif cagr > target_hi:
        cagr_pen = (cagr - target_hi) * 0.3
    else:
        cagr_pen = 0.0
    calmar = cagr / max(dd_pen, 1.0)
    return calmar - cagr_pen


def main() -> None:
    monthly = load_panel()
    print(f"Panel: {monthly['Symbol'].nunique()} symbols, {monthly['Date'].nunique()} months")

    grid = {
        "top_k": [5, 8, 10],
        "mom_lookback_months": [9, 12],
        "bench_sma_months": [10, 12],
        "bench_min_momentum": [0.0, 0.03, 0.05],
        "target_vol_annual": [0.12, 0.14, 0.16, 0.18],
        "max_leverage": [1.1, 1.25, 1.35],
        "gross_cap": [0.9, 1.0],
        "max_name_weight": [0.18, 0.22],
        "dd_hard_stop_pct": [100.0, 12.0, 15.0, 18.0],
    }

    keys = list(grid.keys())
    combos = list(product(*(grid[k] for k in keys)))
    max_runs = int(sys.argv[1]) if len(sys.argv) > 1 else 800
    if len(combos) > max_runs:
        import random

        random.seed(42)
        combos = random.sample(combos, max_runs)
    rows: list[dict] = []
    for vals in combos:
        overrides = dict(zip(keys, vals))
        if overrides["dd_hard_stop_pct"] is None:
            overrides["dd_hard_stop_pct"] = 100.0  # disable fuse
        p = {**DEFAULT_REGIME_MOMENTUM_PARAMS, **overrides}
        trades, eq = backtest_regime_momentum_portfolio(monthly, p)
        s = asdict(summarize_regime_momentum(trades, eq, p))
        cagr, dd = s["cagr_pct"], s["max_drawdown_pct"]
        rows.append(
            {
                **{k: overrides[k] for k in keys if k != "dd_hard_stop_pct"},
                "dd_hard_stop_pct": overrides["dd_hard_stop_pct"],
                "cagr_pct": cagr,
                "max_drawdown_pct": dd,
                "total_return_pct": s["total_return_pct"],
                "n_trades": s["n_trades"],
                "score": score(cagr, dd),
                "calmar": cagr / max(abs(dd), 1.0),
            }
        )

    df = pd.DataFrame(rows)
    in_band = df[(df["cagr_pct"] >= 20) & (df["cagr_pct"] <= 32)]
    if not in_band.empty:
        best = in_band.sort_values(["max_drawdown_pct", "cagr_pct"], ascending=[False, False]).iloc[0]
        tag = "CAGR 20–30% band, lowest DD"
    else:
        near = df[df["cagr_pct"] >= 18].sort_values("score", ascending=False)
        best = near.iloc[0] if not near.empty else df.sort_values("score", ascending=False).iloc[0]
        tag = "Best score (CAGR≥18 or closest)"

    print(f"\n=== {tag} ===")
    print(
        f"CAGR {best['cagr_pct']:.2f}%  maxDD {best['max_drawdown_pct']:.2f}%  "
        f"total {best['total_return_pct']:.0f}%  calmar {best['calmar']:.2f}"
    )
    params = {
        k: (None if k == "dd_hard_stop_pct" and best[k] >= 99 else float(best[k]) if k != "top_k" and "months" not in k else int(best[k]))
        for k in keys
    }
    if params.get("dd_hard_stop_pct") is None or (isinstance(params["dd_hard_stop_pct"], float) and params["dd_hard_stop_pct"] >= 99):
        params.pop("dd_hard_stop_pct", None)
        params["dd_hard_stop_pct"] = 100.0
    print("Params:", {k: best[k] for k in keys})

    top = df.sort_values("score", ascending=False).head(10)
    print("\nTop 10 by score:")
    print(
        top[
            ["cagr_pct", "max_drawdown_pct", "calmar", "top_k", "target_vol_annual", "dd_hard_stop_pct", "bench_min_momentum"]
        ].to_string(index=False)
    )


if __name__ == "__main__":
    main()