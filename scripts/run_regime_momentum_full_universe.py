#!/usr/bin/env python3
"""Fetch full MCP monthly universe, run regime_momentum backtest + walk-forward."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.regime_momentum_portfolio import (  # noqa: E402
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    REGIME_MOMENTUM_VERSION,
    backtest_regime_momentum_portfolio,
    evaluate_params_walkforward,
    summarize_regime_momentum,
)

MCP = "https://mcp.marketsenseanalytics.com"
CACHE = _REPO / "data" / "cache" / "full_universe_monthly.parquet"
BENCH = "NIFTYBEES"
MIN_BARS = 36
WORKERS = 12


def mcp_json(path: str) -> dict:
    return json.loads(subprocess.check_output(["curl", "-sS", "-m", "45", f"{MCP}{path}"], text=True))


def fetch_symbol(sym: str) -> pd.DataFrame | None:
    try:
        raw = mcp_json(f"/api/monthly/{sym}")
        bars = raw.get("bars") or []
        if len(bars) < MIN_BARS:
            return None
        return pd.DataFrame(
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
    except Exception:
        return None


def load_universe(*, refresh: bool = False) -> pd.DataFrame:
    if CACHE.is_file() and not refresh:
        df = pd.read_parquet(CACHE)
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    stocks = mcp_json("/api/stocks")["stocks"]
    symbols = sorted({str(s["symbol"]).upper() for s in stocks})
    if BENCH not in symbols:
        symbols.append(BENCH)

    parts: list[pd.DataFrame] = []
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_symbol, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            done += 1
            if done % 200 == 0:
                print(f"  fetched {done}/{len(symbols)}...", flush=True)
            df = fut.result()
            if df is not None:
                parts.append(df)

    if not parts:
        raise RuntimeError("No symbols loaded from MCP")
    panel = pd.concat(parts, ignore_index=True)
    panel["Date"] = pd.to_datetime(panel["Date"])
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(CACHE, index=False)
    return panel


def main() -> None:
    refresh = "--refresh" in sys.argv
    print("Loading full monthly universe from MCP (cached after first run)...")
    monthly = load_universe(refresh=refresh)
    n_sym = monthly["Symbol"].nunique()
    n_mo = monthly["Date"].nunique()
    print(f"Panel: {n_sym} symbols, {n_mo} months, {len(monthly):,} rows")

    if BENCH not in monthly["Symbol"].values:
        print(f"Warning: {BENCH} missing — regime filter may fail")

    p = DEFAULT_REGIME_MOMENTUM_PARAMS
    print(f"Running backtest (regime_momentum v{REGIME_MOMENTUM_VERSION})...")
    print(
        "Universe filter:",
        f"min_close={p['universe_min_close']}",
        f"top_liq={p['universe_top_n_liquidity']}",
        f"liq_lb={p['universe_liquidity_lookback_months']}mo",
    )
    trades, equity = backtest_regime_momentum_portfolio(monthly, p)
    summary = asdict(summarize_regime_momentum(trades, equity, p))
    wf = evaluate_params_walkforward(monthly, p)

    print("\n=== FULL UNIVERSE — regime_momentum ===")
    print(f"Total return:   {summary['total_return_pct']:.1f}%")
    print(f"CAGR:           {summary['cagr_pct']:.2f}%")
    print(f"Max drawdown:   {summary['max_drawdown_pct']:.2f}%")
    print(f"Round-trips:    {summary['n_trades']}")
    print(f"Win rate:       {summary['win_rate_pct']:.1f}%")

    if wf.get("ok"):
        print("\nWalk-forward (same params, no re-fit):")
        print(f"  Train ≤2015:  CAGR {wf['train_cagr']:.1f}%  DD {wf['train_dd']:.1f}%")
        print(f"  Val 2016-19:  CAGR {wf['val_cagr']:.1f}%  DD {wf['val_dd']:.1f}%")
        print(f"  Test 2020+:   CAGR {wf['test_cagr']:.1f}%  DD {wf['test_dd']:.1f}%")
        print(f"  Stable:       {wf['stable']}")

    if not trades.empty:
        t = trades.copy()
        t["exit_date"] = pd.to_datetime(t["exit_date"])
        by_m = t.groupby(t["exit_date"].dt.to_period("M")).size()
        print("\nTrades per month (exits):")
        print(f"  Mean {by_m.mean():.2f}  median {by_m.median():.0f}  months with 0: {(by_m==0).sum()}")
        print("Exit reasons:")
        print(trades["reason"].value_counts().to_string())

    out = _REPO / "data" / "cache" / "full_universe_regime_summary.json"
    payload = {"summary": summary, "walkforward": {k: wf[k] for k in wf if k not in ("params", "ok") or k == "stable"}, "n_symbols": n_sym}
    out.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()