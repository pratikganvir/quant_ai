#!/usr/bin/env python3
"""Walk-forward-style backtest: NIFTYBEES regime + stock trend, monthly opens.

Variants: ``sma12`` (default) or ``dual_mom`` (SMA12 + 3-month momentum > 0).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.data.daily_cache import get_daily_bars
from quant.data.monthly_cache import get_monthly_bars
from quant.research.institutional_accumulation import resample_symbol_to_monthly

BENCH = "NIFTYBEES"
SMA = 12


def load_universe(path: Path) -> list[str]:
    syms = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        syms.append(line.upper())
    return syms


def monthly_bars(sym: str, *, mcp: str | None) -> pd.DataFrame | None:
    if mcp:
        try:
            out = subprocess.check_output(
                ["curl", "-sS", "-m", "45", f"{mcp.rstrip('/')}/api/monthly/{sym}?limit=500"],
                text=True,
            )
            bars = json.loads(out).get("bars") or []
            if not bars:
                return None
            df = pd.DataFrame(bars)
            return pd.DataFrame(
                {
                    "Date": pd.to_datetime(df["date"]),
                    "Open": df["open"].astype(float),
                    "High": df["high"].astype(float),
                    "Low": df["low"].astype(float),
                    "Close": df["close"].astype(float),
                    "Volume": df["volume"].astype(float),
                }
            ).sort_values("Date")
        except Exception:
            return None
    try:
        bars = get_monthly_bars(sym, limit=500)
        if bars:
            df = pd.DataFrame(bars)
            return pd.DataFrame(
                {
                    "Date": pd.to_datetime(df["date"]),
                    "Open": df["open"].astype(float),
                    "Close": df["close"].astype(float),
                }
            ).sort_values("Date")
    except FileNotFoundError:
        pass
    try:
        bars = get_daily_bars(sym, date_start="2011-01-01", limit=20_000)
        if not bars:
            return None
        d = pd.DataFrame(bars)
        d = pd.DataFrame(
            {
                "Date": pd.to_datetime(d["date"]),
                "Symbol": sym,
                "Open": d["open"].astype(float),
                "High": d["high"].astype(float),
                "Low": d["low"].astype(float),
                "Close": d["close"].astype(float),
                "Volume": d["volume"].astype(float),
            }
        )
        return resample_symbol_to_monthly(d[["Date", "Open", "High", "Low", "Close", "Volume"]])
    except FileNotFoundError:
        return None


def regime_index(bench: pd.DataFrame) -> pd.Series:
    b = bench.set_index(bench["Date"].dt.to_period("M"))["Close"].sort_index()
    return (b > b.rolling(SMA).mean()) & (b / b.shift(SMA) - 1 > 0)


def _mom3(close: pd.Series) -> np.ndarray:
    return close.pct_change(3).values


def long_ok_at(df: pd.DataFrame, i: int, reg: pd.Series, *, variant: str) -> bool:
    close = float(df["Close"].iloc[i])
    s12 = float(df["Close"].rolling(SMA).mean().iloc[i])
    per = df["Date"].iloc[i].to_period("M")
    if per not in reg.index:
        return False
    if not (bool(reg.loc[per]) and close > s12 and np.isfinite(s12)):
        return False
    if variant == "dual_mom":
        mom = float(df["Close"].pct_change(3).iloc[i])
        if not np.isfinite(mom) or mom <= 0:
            return False
    return True


def backtest(sym: str, df: pd.DataFrame, reg: pd.Series, *, variant: str) -> dict | None:
    if df is None or len(df) < SMA + 2:
        return None
    df = df.copy()
    df["per"] = df["Date"].dt.to_period("M")
    open_ = df["Open"].values
    close = df["Close"].values
    s12 = pd.Series(close).rolling(SMA).mean().values
    eq = 1.0
    months_in = 0
    in_pos = False
    for i in range(SMA, len(df) - 1):
        ok = long_ok_at(df, i, reg, variant=variant)
        if variant == "sma12":
            if not ok:
                continue
            months_in += 1
            eq *= open_[i + 1] / open_[i]
            continue
        exposed = in_pos
        if in_pos and not ok:
            in_pos = False
            exposed = False
        elif not in_pos and ok:
            in_pos = True
            exposed = True
        if exposed:
            months_in += 1
            eq *= open_[i + 1] / open_[i]
    years = max((df["Date"].iloc[-1] - df["Date"].iloc[SMA]).days / 365.25, 1e-6)
    if months_in < 12:
        return None
    cagr = (eq ** (1 / years) - 1) * 100
    bh = (close[-1] / close[SMA]) ** (1 / years) - 1
    return {
        "symbol": sym,
        "cagr": cagr,
        "buy_hold_cagr": bh * 100,
        "beats_bh": cagr > bh * 100,
        "months_in": months_in,
        "n_months": len(df),
    }


def round_trips(sym: str, df: pd.DataFrame, reg: pd.Series, *, variant: str) -> pd.DataFrame:
    """One position at a time; signal on month close, trade next month open."""
    if df is None or len(df) < SMA + 2:
        return pd.DataFrame()
    df = df.copy()
    trades: list[dict] = []
    in_pos = False
    entry_i: int | None = None
    entry_px = np.nan
    for i in range(SMA, len(df) - 1):
        ok = long_ok_at(df, i, reg, variant=variant)
        if not in_pos and ok:
            in_pos = True
            entry_i = i + 1
            entry_px = float(df["Open"].iloc[entry_i])
        elif in_pos and not ok:
            exit_i = i + 1
            exit_px = float(df["Open"].iloc[exit_i])
            per_i = df["Date"].iloc[i].to_period("M")
            reason = "regime_off"
            if per_i in reg.index and bool(reg.loc[per_i]):
                close = float(df["Close"].iloc[i])
                s12 = float(df["Close"].rolling(SMA).mean().iloc[i])
                if not (close > s12 and np.isfinite(s12)):
                    reason = "trend_off"
                elif variant == "dual_mom":
                    mom = float(df["Close"].pct_change(3).iloc[i])
                    if np.isfinite(mom) and mom <= 0:
                        reason = "mom_off"
                    else:
                        reason = "trend_off"
                else:
                    reason = "trend_off"
            gross = exit_px / entry_px - 1.0
            trades.append(
                {
                    "symbol": sym,
                    "entry_date": df["Date"].iloc[entry_i],
                    "exit_date": df["Date"].iloc[exit_i],
                    "entry_open": entry_px,
                    "exit_open": exit_px,
                    "hold_months": exit_i - entry_i,
                    "gross_return_pct": gross * 100,
                    "exit_reason": reason,
                }
            )
            in_pos = False
            entry_i = None
    if in_pos and entry_i is not None:
        exit_i = len(df) - 1
        exit_px = float(df["Open"].iloc[exit_i])
        trades.append(
            {
                "symbol": sym,
                "entry_date": df["Date"].iloc[entry_i],
                "exit_date": df["Date"].iloc[exit_i],
                "entry_open": entry_px,
                "exit_open": exit_px,
                "hold_months": exit_i - entry_i,
                "gross_return_pct": (exit_px / entry_px - 1) * 100,
                "exit_reason": "open_position",
            }
        )
    return pd.DataFrame(trades)


def latest_signal(sym: str, df: pd.DataFrame, reg: pd.Series, *, variant: str) -> dict | None:
    if df is None or len(df) < SMA + 1:
        return None
    i = len(df) - 1
    close = float(df["Close"].iloc[i])
    s12 = float(df["Close"].rolling(SMA).mean().iloc[i])
    per = df["Date"].iloc[i].to_period("M")
    regime = bool(reg.loc[per]) if per in reg.index else False
    trend = close > s12 if np.isfinite(s12) else False
    mom = float(df["Close"].pct_change(3).iloc[i])
    mom_on = np.isfinite(mom) and mom > 0
    long_sig = regime and trend and (mom_on if variant == "dual_mom" else True)
    return {
        "symbol": sym,
        "as_of": str(df["Date"].iloc[i].date()),
        "close": close,
        "sma12": round(s12, 2),
        "mom3_pct": round(mom * 100, 2) if np.isfinite(mom) else None,
        "regime_on": regime,
        "trend_on": trend,
        "mom_on": mom_on,
        "long_signal": long_sig,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--universe",
        default="nifty50",
        choices=("nifty50", "regime_sma12"),
        help="Symbol list under universe/",
    )
    p.add_argument("--mcp", default=None, help="e.g. https://mcp.marketsenseanalytics.com")
    p.add_argument("--top", type=int, default=15)
    p.add_argument(
        "--export-trades",
        type=Path,
        default=None,
        metavar="CSV",
        help="Write all historical round-trips (one row per entry/exit)",
    )
    p.add_argument(
        "--export-signals",
        type=Path,
        default=None,
        metavar="CSV",
        help="Write latest month-end signals for every symbol",
    )
    p.add_argument(
        "--export-summary",
        type=Path,
        default=None,
        metavar="CSV",
        help="Per-symbol CAGR summary (rule vs buy-and-hold)",
    )
    p.add_argument("--symbol", type=str, default=None, help="Only this symbol (trades + summary)")
    p.add_argument(
        "--variant",
        default="sma12",
        choices=("sma12", "dual_mom"),
        help="sma12: regime + Close>SMA12; dual_mom: also require 3-month return > 0",
    )
    args = p.parse_args()

    ufile = _REPO / "universe" / (
        "nifty50_eq_symbols.txt" if args.universe == "nifty50" else "regime_sma12_monthly_symbols.txt"
    )
    symbols = load_universe(ufile)
    if BENCH not in symbols:
        symbols = [BENCH] + [s for s in symbols if s != BENCH]

    bench = monthly_bars(BENCH, mcp=args.mcp)
    if bench is None:
        raise SystemExit(f"Missing benchmark {BENCH} (sync or --mcp)")

    reg = regime_index(bench)
    only = args.symbol.strip().upper() if args.symbol else None
    if only:
        symbols = [BENCH, only] if only != BENCH else [BENCH]

    rows = []
    signals = []
    trade_parts: list[pd.DataFrame] = []
    for sym in symbols:
        if sym == BENCH:
            continue
        df = monthly_bars(sym, mcp=args.mcp)
        if args.export_trades and df is not None:
            t = round_trips(sym, df, reg, variant=args.variant)
            if not t.empty:
                trade_parts.append(t)
        r = backtest(sym, df, reg, variant=args.variant)
        if r:
            rows.append(r)
        sig = latest_signal(sym, df, reg, variant=args.variant) if df is not None else None
        if sig:
            signals.append(sig)

    out = pd.DataFrame(rows).sort_values("cagr", ascending=False)
    print(f"Universe: {args.universe} ({len(symbols)-1} stocks) + {BENCH}  variant={args.variant}")
    print(f"Backtested: {len(out)} symbols (need ≥12 regime+trend months since 2011)\n")

    if out.empty:
        raise SystemExit("No results — check QUANT_DATA_DIR or --mcp")

    print("=== TOP by rule CAGR ===")
    print(out.head(args.top).to_string(index=False, float_format=lambda x: f"{x:.1f}"))
    print(f"\nMedian rule CAGR: {out.cagr.median():.1f}%  |  beat B&H: {out.beats_bh.mean()*100:.0f}%")

    longs = [s for s in signals if s["long_signal"]]
    rule = "regime + Close > SMA12 + 3m mom" if args.variant == "dual_mom" else "regime + Close > SMA12"
    print(f"\n=== LONG now ({rule}) — {len(longs)} names ===")
    for s in sorted(longs, key=lambda x: x["symbol"])[:25]:
        print(f"  {s['symbol']:14} close={s['close']:.2f} sma12={s['sma12']}  as_of={s['as_of']}")
    if len(longs) > 25:
        print(f"  ... +{len(longs)-25} more")

    off = [s for s in signals if not s["long_signal"]]
    rel = next((s for s in signals if s["symbol"] == "RELIANCE"), None)
    if rel:
        print("\n=== RELIANCE ===")
        print(
            f"  long={rel['long_signal']}  regime={rel['regime_on']}  trend={rel['trend_on']}  "
            f"close={rel['close']}  sma12={rel['sma12']}  ({rel['as_of']})"
        )
        rrow = out.loc[out.symbol == "RELIANCE"]
        if not rrow.empty:
            r = rrow.iloc[0]
            print(f"  backtest rule CAGR {r.cagr:.1f}% vs B&H {r.buy_hold_cagr:.1f}%")

    if args.export_summary:
        args.export_summary.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(args.export_summary, index=False)
        print(f"\nWrote summary: {args.export_summary} ({len(out)} symbols)")

    if args.export_signals:
        sig_df = pd.DataFrame(signals)
        args.export_signals.parent.mkdir(parents=True, exist_ok=True)
        sig_df.to_csv(args.export_signals, index=False)
        print(f"Wrote signals: {args.export_signals} ({len(sig_df)} rows)")

    if args.export_trades:
        tr_df = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
        if not tr_df.empty:
            tr_df = tr_df.sort_values(["symbol", "entry_date"]).reset_index(drop=True)
            entry_dt = pd.to_datetime(tr_df["entry_date"])
            exit_dt = pd.to_datetime(tr_df["exit_date"])
            hold_days = (exit_dt - entry_dt).dt.days
            export = pd.DataFrame(
                {
                    "symbol": tr_df["symbol"],
                    "entry price": tr_df["entry_open"],
                    "entry date": entry_dt.dt.strftime("%Y-%m-%d"),
                    "exit price": tr_df["exit_open"],
                    "exit date": exit_dt.dt.strftime("%Y-%m-%d"),
                    "hold days": hold_days.astype(int),
                    "profit percent": tr_df["gross_return_pct"].round(4),
                }
            )
        else:
            export = pd.DataFrame(
                columns=[
                    "symbol",
                    "entry price",
                    "entry date",
                    "exit price",
                    "exit date",
                    "hold days",
                    "profit percent",
                ]
            )
        args.export_trades.parent.mkdir(parents=True, exist_ok=True)
        export.to_csv(args.export_trades, index=False)
        print(f"Wrote trades: {args.export_trades} ({len(export)} round-trips)")


if __name__ == "__main__":
    main()