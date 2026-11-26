#!/usr/bin/env python3
"""Compare regime+SMA12 variants vs regime_momentum book on Nifty 50 monthly data."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.regime_momentum_portfolio import (  # noqa: E402
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS,
    REGIME_MOMENTUM_TARGET_2PCT_MONTHLY_PARAMS,
    backtest_regime_momentum_portfolio,
    summarize_regime_momentum,
)

MCP = "https://mcp.marketsenseanalytics.com"
BENCH = "NIFTYBEES"
SMA = 12
CACHE = _REPO / "data" / "cache" / "nifty50_monthly_panel.parquet"


def load_symbols() -> list[str]:
    syms = []
    for line in (_REPO / "universe/nifty50_eq_symbols.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            syms.append(line.upper())
    return sorted(set(syms))


def fetch_monthly(sym: str) -> pd.DataFrame | None:
    try:
        raw = json.loads(
            subprocess.check_output(
                ["curl", "-sS", "-m", "45", f"{MCP}/api/monthly/{sym}?limit=500"],
                text=True,
            )
        )
        bars = raw.get("bars") or []
        if len(bars) < 60:
            return None
        df = pd.DataFrame(bars)
        return pd.DataFrame(
            {
                "Date": pd.to_datetime(df["date"]),
                "Symbol": sym,
                "Open": df["open"].astype(float),
                "High": df["high"].astype(float),
                "Low": df["low"].astype(float),
                "Close": df["close"].astype(float),
                "Volume": df["volume"].astype(float),
            }
        ).sort_values("Date")
    except Exception:
        return None


def load_panel(force: bool = False) -> pd.DataFrame:
    if CACHE.is_file() and not force:
        df = pd.read_parquet(CACHE)
        df["Date"] = pd.to_datetime(df["Date"])
        return df
    syms = load_symbols()
    parts: list[pd.DataFrame] = []
    for sym in [BENCH, *syms]:
        m = fetch_monthly(sym)
        if m is not None:
            parts.append(m)
        print(f"  fetched {sym}: {0 if m is None else len(m)} bars")
    if not parts:
        raise RuntimeError("No monthly data from MCP")
    df = pd.concat(parts, ignore_index=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    return df


def regime_series(bench: pd.DataFrame, *, min_mom: float = 0.0) -> pd.Series:
    b = bench.set_index(bench["Date"].dt.to_period("M"))["Close"].sort_index()
    sma = b.rolling(SMA).mean()
    mom12 = b / b.shift(SMA) - 1
    return (b > sma) & (mom12 > min_mom)


def indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["Close"]
    df["s6"] = c.rolling(6).mean()
    df["s12"] = c.rolling(SMA).mean()
    df["s24"] = c.rolling(24).mean()
    df["mom3"] = c.pct_change(3)
    df["per"] = df["Date"].dt.to_period("M")
    return df


def backtest_symbol_rule(
    df: pd.DataFrame,
    reg: pd.Series,
    *,
    name: str,
) -> dict | None:
    if len(df) < 30:
        return None
    df = indicator_frame(df)
    open_ = df["Open"].values
    close = df["Close"].values
    s6 = df["s6"].values
    s12 = df["s12"].values
    s24 = df["s24"].values
    mom3 = df["mom3"].values
    per = df["per"].values

    def regime_ok(i: int) -> bool:
        if per[i] not in reg.index:
            return False
        return bool(reg.loc[per[i]])

    def entry_ok(i: int) -> bool:
        if not regime_ok(i):
            return False
        if name == "base":
            return close[i] > s12[i] and np.isfinite(s12[i])
        if name == "strict_regime3":
            return close[i] > s12[i] and np.isfinite(s12[i])
        if name == "stack":
            return (
                close[i] > s12[i] > s24[i]
                and np.isfinite(s12[i])
                and np.isfinite(s24[i])
            )
        if name == "dual_mom":
            return (
                close[i] > s12[i]
                and mom3[i] > 0
                and np.isfinite(s12[i])
                and np.isfinite(mom3[i])
            )
        if name in ("dual_stack", "dual_stack_fast"):
            return (
                close[i] > s12[i] > s24[i]
                and mom3[i] > 0
                and np.isfinite(s12[i])
                and np.isfinite(s24[i])
            )
        raise ValueError(name)

    def hold_ok(i: int) -> bool:
        if not regime_ok(i):
            return False
        if name == "dual_stack_fast" and close[i] < s6[i]:
            return False
        if close[i] <= s12[i]:
            return False
        if name in ("stack", "dual_stack", "dual_stack_fast"):
            if not (close[i] > s24[i]):
                return False
        if name in ("dual_mom", "dual_stack", "dual_stack_fast"):
            if mom3[i] <= 0 or not np.isfinite(mom3[i]):
                return False
        return True

    eq = 1.0
    peak = 1.0
    mdd = 0.0
    months_in = 0
    in_pos = False
    for i in range(SMA, len(df) - 1):
        if name in ("base", "strict_regime3"):
            exposed = entry_ok(i)
        else:
            exposed = in_pos
            if in_pos and not hold_ok(i):
                in_pos = False
                exposed = False
            elif not in_pos and entry_ok(i):
                in_pos = True
                exposed = True
        if exposed:
            months_in += 1
            eq *= open_[i + 1] / open_[i]
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)

    years = max((df["Date"].iloc[-1] - df["Date"].iloc[SMA]).days / 365.25, 1e-6)
    if months_in < 12:
        return None
    trades = months_in
    cagr = (eq ** (1 / years) - 1) * 100
    bh = (close[-1] / close[24]) ** (1 / years) - 1
    return {
        "cagr": cagr,
        "max_dd_pct": mdd * 100,
        "months_in": months_in,
        "trades": trades,
        "buy_hold_cagr": bh * 100,
        "calmar": cagr / max(abs(mdd * 100), 1),
    }


def per_symbol_grid(panel: pd.DataFrame, bench: pd.DataFrame) -> pd.DataFrame:
    syms = sorted(set(panel.Symbol) - {BENCH})
    variants = ["base", "strict_regime3", "stack", "dual_mom", "dual_stack", "dual_stack_fast"]
    rows = []
    for v in variants:
        min_mom = 0.03 if v == "strict_regime3" else 0.0
        reg = regime_series(bench, min_mom=min_mom)
        stats = []
        for sym in syms:
            df = panel.loc[panel.Symbol == sym].drop(columns="Symbol")
            r = backtest_symbol_rule(df, reg, name=v if v != "strict_regime3" else "base")
            if r:
                stats.append(r)
        if not stats:
            continue
        s = pd.DataFrame(stats)
        rows.append(
            {
                "variant": v,
                "median_cagr": s.cagr.median(),
                "mean_cagr": s.cagr.mean(),
                "median_dd": s.max_dd_pct.median(),
                "mean_dd": s.max_dd_pct.mean(),
                "median_calmar": s.calmar.median(),
                "beat_bh_pct": (s.cagr > s.buy_hold_cagr).mean() * 100,
                "n": len(s),
            }
        )
    return pd.DataFrame(rows)


def book_row(label: str, params: dict, panel: pd.DataFrame) -> dict:
    p = {**params}
    p["universe_exclude_symbols"] = tuple(
        s for s in load_symbols() if s not in set(panel.Symbol.astype(str))
    )
    trades, eq = backtest_regime_momentum_portfolio(panel, p)
    s = asdict(summarize_regime_momentum(trades, eq, p))
    return {
        "book": label,
        "cagr_pct": s["cagr_pct"],
        "max_dd_pct": s["max_drawdown_pct"],
        "total_return_pct": s["total_return_pct"],
        "calmar": s["cagr_pct"] / max(abs(s["max_drawdown_pct"]), 1),
        "n_trades": s["n_trades"],
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    print("Loading Nifty 50 monthly panel (MCP)...")
    panel = load_panel(force=args.refresh)
    bench = panel.loc[panel.Symbol == BENCH].drop(columns="Symbol")
    print(f"Panel: {panel.Symbol.nunique()} symbols, {panel.Date.nunique()} months\n")

    ps = per_symbol_grid(panel, bench)
    ps["score"] = ps["median_cagr"] + ps["median_dd"] / 2
    ps = ps.sort_values("score", ascending=False)
    print("=== Per-symbol rules (independent ₹1 books, median across Nifty 50) ===")
    print(
        ps[
            [
                "variant",
                "median_cagr",
                "median_dd",
                "median_calmar",
                "beat_bh_pct",
                "n",
            ]
        ].to_string(index=False, float_format=lambda x: f"{x:.1f}")
    )
    best_sym = ps.iloc[0]
    print(f"\nBest per-symbol variant by score: {best_sym['variant']}")

    books = [
        book_row("regime_momentum default", DEFAULT_REGIME_MOMENTUM_PARAMS, panel),
        book_row("regime_momentum 2pct playbook", REGIME_MOMENTUM_TARGET_2PCT_MONTHLY_PARAMS, panel),
        book_row("regime_momentum NIFTY50 balanced", REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS, panel),
    ]
    bd = pd.DataFrame(books).sort_values("calmar", ascending=False)
    print("\n=== Single portfolio book (shared equity, Nifty 50 universe) ===")
    print(bd.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    b = bd.iloc[0]
    print(
        f"\nRecommendation: use book '{b['book']}' for portfolio execution "
        f"({b['cagr_pct']:.1f}% CAGR, {b['max_dd_pct']:.1f}% max DD)."
    )
    if best_sym["variant"] != "base":
        print(
            f"For single-name / futures legs, upgrade rule → '{best_sym['variant']}' "
            f"(median {best_sym['median_cagr']:.1f}% CAGR, median DD {best_sym['median_dd']:.1f}%)."
        )


if __name__ == "__main__":
    main()