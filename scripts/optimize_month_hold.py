#!/usr/bin/env python3
"""Search month-hold params for high CAGR and low drawdown on monthly NSE panel."""

from __future__ import annotations

import json
import random
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.month_hold_portfolio import (  # noqa: E402
    DEFAULT_MONTH_HOLD_PARAMS,
    MONTH_HOLD_PARAM_GRID,
    MONTH_HOLD_VERSION,
    backtest_month_hold_portfolio,
    evaluate_month_hold_walkforward,
    month_hold_params_fingerprint,
    pareto_score,
    summarize_month_hold,
)

MCP = "https://mcp.marketsenseanalytics.com"
CACHE_FULL = _REPO / "data" / "cache" / "full_universe_monthly.parquet"
CACHE_FAST = _REPO / "data" / "cache" / "regime_opt_monthly.parquet"
OUT_JSON = _REPO / "data" / "cache" / "month_hold_best.json"
PARAM_KEYS = list(MONTH_HOLD_PARAM_GRID[0].keys())

SYMBOLS_FAST = """
TRENT ASIANPAINT TCS PERSISTENT ICICIBANK MARUTI HDFCBANK BAJFINANCE TATASTEEL
HINDALCO TITAN INFY ONGC JSWSTEEL BEL POLYCAB RELIANCE COALINDIA BSE DIXON
WIPRO LT SBIN AXISBANK ITC HCLTECH SUNPHARMA BHARTIARTL INDIGO HINDUNILVR M&M
TATAMOTORS NTPC POWERGRID ULTRACEMCO NESTLEIND KOTAKBANK SHRIRAMFIN ADANIENT
ADANIPORTS TECHM ETERNAL DMART NIFTYBEES
""".split()


def _curl_json(path: str) -> dict:
    return json.loads(
        subprocess.check_output(["curl", "-sS", "-m", "30", f"{MCP}{path}"], text=True)
    )


def load_panel(*, fast: bool) -> pd.DataFrame:
    cache = CACHE_FAST if fast else CACHE_FULL
    if cache.is_file():
        df = pd.read_parquet(cache)
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    symbols = SYMBOLS_FAST if fast else [
        str(s["symbol"]).upper() for s in _curl_json("/api/stocks")["stocks"]
    ]
    parts: list[pd.DataFrame] = []
    for sym in symbols:
        try:
            raw = _curl_json(f"/api/monthly/{sym}")
            bars = raw.get("bars") or []
            if len(bars) < 36:
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
        raise RuntimeError("No monthly data — run MCP sync or place parquet under data/cache/")
    df = pd.concat(parts, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"])
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    return df


def main() -> None:
    fast = "--full" not in sys.argv
    max_runs = 120
    for arg in sys.argv[1:]:
        if arg.isdigit():
            max_runs = int(arg)
    random.seed(42)
    grid = MONTH_HOLD_PARAM_GRID
    if len(grid) > max_runs:
        grid = random.sample(grid, max_runs)

    monthly = load_panel(fast=fast)
    tag = "fast (liquid ~40)" if fast else "full universe"
    print(f"Panel [{tag}]: {monthly['Symbol'].nunique()} symbols, version {MONTH_HOLD_VERSION}")

    rows: list[dict] = []
    for overrides in grid:
        p = {**DEFAULT_MONTH_HOLD_PARAMS, **overrides}
        trades, eq = backtest_month_hold_portfolio(monthly, p)
        s = summarize_month_hold(trades, eq, p)
        wf = evaluate_month_hold_walkforward(monthly, overrides)
        rows.append(
            {
                **overrides,
                "cagr_pct": s.cagr_pct,
                "max_drawdown_pct": s.max_drawdown_pct,
                "calmar": s.cagr_pct / max(abs(s.max_drawdown_pct), 1.0),
                "score": pareto_score(s.cagr_pct, s.max_drawdown_pct),
                "stable": bool(wf.get("stable")),
                "test_cagr": wf.get("test_cagr"),
                "test_dd": wf.get("test_dd"),
            }
        )

    df = pd.DataFrame(rows)
    stable = df.loc[df["stable"]].copy()
    pick_pool = stable if not stable.empty else df
    pick = pick_pool.sort_values(
        ["score", "calmar", "max_drawdown_pct"],
        ascending=[False, False, False],
    ).iloc[0]

    if fast and CACHE_FULL.is_file():
        full = pd.read_parquet(CACHE_FULL)
        full["Date"] = pd.to_datetime(full["Date"])
        ov = {k: pick[k] for k in PARAM_KEYS}
        p_full = {**DEFAULT_MONTH_HOLD_PARAMS, **ov}
        trades, eq = backtest_month_hold_portfolio(full, p_full)
        s_full = summarize_month_hold(trades, eq, p_full)
        wf_full = evaluate_month_hold_walkforward(full, ov)
        pick = pick.copy()
        pick["full_cagr_pct"] = s_full.cagr_pct
        pick["full_max_dd_pct"] = s_full.max_drawdown_pct
        pick["full_stable"] = wf_full.get("stable")

    out = {
        "version": MONTH_HOLD_VERSION,
        "params": month_hold_params_fingerprint({k: pick[k] for k in PARAM_KEYS}),
        "in_sample": {
            "cagr_pct": float(pick["cagr_pct"]),
            "max_drawdown_pct": float(pick["max_drawdown_pct"]),
            "calmar": float(pick["calmar"]),
            "stable": bool(pick["stable"]),
        },
    }
    if "full_cagr_pct" in pick:
        out["full_universe"] = {
            "cagr_pct": float(pick["full_cagr_pct"]),
            "max_drawdown_pct": float(pick["full_max_dd_pct"]),
            "stable": bool(pick["full_stable"]),
        }
    def _jsonable(obj):  # numpy/pandas scalars → native Python
        if isinstance(obj, dict):
            return {k: _jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_jsonable(v) for v in obj]
        if hasattr(obj, "item"):
            return obj.item()
        return obj

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(_jsonable(out), indent=2), encoding="utf-8")

    out = _jsonable(out)
    out = _jsonable(out)
    print("\n=== Best month-hold config ===")
    print(json.dumps(out, indent=2))
    print(f"\nWrote {OUT_JSON}")
    top = df.sort_values("score", ascending=False).head(8)
    cols = ["cagr_pct", "max_drawdown_pct", "calmar", "stable", "top_k", "mom_lookback_months",
            "signal_rank", "dd_hard_stop_pct"]
    print("\nTop 8 by score:")
    print(top[cols].to_string(index=False))


if __name__ == "__main__":
    main()