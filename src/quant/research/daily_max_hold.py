"""Single-symbol daily backtest with a hard cap on holding sessions."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

import numpy as np
import pandas as pd

from quant.research.institutional_accumulation import BacktestSummary

DAILY_MAX_HOLD_ENGINE_VERSION = "1.0"


def normalize_daily_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Expect ``Date``, ``Open``, ``High``, ``Low``, ``Close`` (case-insensitive input ok)."""
    x = df.copy()
    rename = {c: c.capitalize() for c in x.columns if c.lower() in {"date", "open", "high", "low", "close", "volume"}}
    if "Date" not in rename and "date" in x.columns:
        rename["date"] = "Date"
    x = x.rename(columns=rename)
    required = ["Date", "Open", "High", "Low", "Close"]
    missing = [c for c in required if c not in x.columns]
    if missing:
        raise ValueError(f"Missing columns: {missing}")
    x["Date"] = pd.to_datetime(x["Date"])
    for c in ["Open", "High", "Low", "Close"]:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    if "Volume" in x.columns:
        x["Volume"] = pd.to_numeric(x["Volume"], errors="coerce")
    return x.sort_values("Date").drop_duplicates("Date", keep="last").reset_index(drop=True)


def atr_series(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    tr = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(length).mean()


def backtest_daily_max_hold(
    bars: pd.DataFrame,
    entry_signal: pd.Series,
    exit_signal: pd.Series,
    *,
    max_hold_sessions: int = 15,
    stop_atr_mult: float = 3.0,
    take_profit_pct: float | None = None,
    atr_length: int = 14,
    initial_capital: float = 100_000.0,
    round_trip_cost_bps: float = 10.0,
) -> tuple[pd.DataFrame, pd.DataFrame, BacktestSummary]:
    """
  Long-only, one position at a time. Signal on bar *t*, enter at bar *t+1* open.
    Exit priority per session: take-profit (intraday high), ATR stop (intraday low),
    discretionary exit signal (open), else flat at max hold (open).
    """
    df = normalize_daily_ohlcv(bars)
    if len(entry_signal) != len(df) or len(exit_signal) != len(df):
        raise ValueError("Signals must align with bars index length")

    o, h, l, c = df.Open, df.High, df.Low, df.Close
    atr = atr_series(h, l, c, atr_length)
    mh = int(max_hold_sessions)
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, float]] = []
    cash = float(initial_capital)
    n = len(df)
    i = 0
    while i < n - 1:
        equity_rows.append({"Date": df.Date.iloc[i], "equity": cash})
        if not bool(entry_signal.iloc[i]):
            i += 1
            continue

        sig_i = i
        entry_i = i + 1
        if entry_i >= n:
            break
        entry_px = float(o.iloc[entry_i])
        atr_i = float(atr.iloc[sig_i])
        if entry_px <= 0 or not np.isfinite(atr_i) or atr_i <= 0:
            i += 1
            continue

        stop_px = entry_px - stop_atr_mult * atr_i
        exit_i: int | None = None
        exit_px = np.nan
        reason = "max_hold"

        for j in range(entry_i, min(entry_i + mh, n)):
            opn, hi, lo = float(o.iloc[j]), float(h.iloc[j]), float(l.iloc[j])
            if take_profit_pct is not None and hi >= entry_px * (1 + take_profit_pct / 100):
                exit_px = entry_px * (1 + take_profit_pct / 100)
                exit_i, reason = j, "take_profit"
                break
            if lo <= stop_px:
                exit_px = min(opn, stop_px)
                exit_i, reason = j, "stop"
                break
            if bool(exit_signal.iloc[j]):
                exit_px = opn
                exit_i, reason = j, "exit_signal"
                break

        if exit_i is None:
            exit_i = min(entry_i + mh - 1, n - 1)
            exit_px = float(o.iloc[exit_i])
            reason = "max_hold"

        gross_ret = exit_px / entry_px - 1.0
        net_ret = gross_ret - 2 * round_trip_cost_bps / 10_000
        cash *= 1 + net_ret
        sym = "?"
        if "symbol" in bars.columns and len(bars):
            sym = str(bars["symbol"].iloc[0]).upper()
        elif "Symbol" in bars.columns and len(bars):
            sym = str(bars["Symbol"].iloc[0]).upper()
        trades.append(
            {
                "symbol": sym,
                "entry_date": df.Date.iloc[entry_i],
                "exit_date": df.Date.iloc[exit_i],
                "entry_px": entry_px,
                "exit_px": float(exit_px),
                "hold_sessions": int(exit_i - entry_i + 1),
                "exit_reason": reason,
                "gross_return_pct": gross_ret * 100,
                "net_return_pct": net_ret * 100,
            }
        )
        equity_rows.append({"Date": df.Date.iloc[exit_i], "equity": cash})
        i = exit_i + 1

    if not equity_rows and n:
        equity_rows.append({"Date": df.Date.iloc[-1], "equity": cash})
    elif n and equity_rows[-1]["Date"] != df.Date.iloc[-1]:
        equity_rows.append({"Date": df.Date.iloc[-1], "equity": cash})

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_rows)
    summary = _summarize_trades(trades_df, equity_df, initial_capital)
    return trades_df, equity_df, summary


def _summarize_trades(trades: pd.DataFrame, equity: pd.DataFrame, initial: float) -> BacktestSummary:
    if trades.empty or equity.empty:
        ic = float(initial)
        return BacktestSummary(ic, ic, 0.0, 0.0, 0.0, 0, 0.0, False)

    final = float(equity["equity"].iloc[-1])
    total_ret = (final / initial - 1) * 100
    days = max((equity["Date"].iloc[-1] - equity["Date"].iloc[0]).days, 1)
    years = days / 365.25
    cagr = ((final / initial) ** (1 / years) - 1) * 100 if years > 0 else 0.0

    peak = equity["equity"].cummax()
    dd = (equity["equity"] / peak - 1) * 100
    max_dd = float(dd.min()) if len(dd) else 0.0

    wins = trades["net_return_pct"] > 0
    return BacktestSummary(
        initial_capital=float(initial),
        final_equity=final,
        total_return_pct=float(total_ret),
        cagr_pct=float(cagr),
        max_drawdown_pct=float(max_dd),
        n_trades=int(len(trades)),
        win_rate_pct=float(wins.mean() * 100),
        profitable=bool(final > initial),
    )


def summary_as_dict(s: BacktestSummary) -> dict[str, float | int | bool]:
    return {k: getattr(s, k) for k in s.__dataclass_fields__}