"""Per-symbol multibagger round trips (regime + setup entry; rule-based exits)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from quant.research.multibagger_exits import (
    DEFAULT_MULTIBAGGER_EXIT_PARAMS,
    position_exit_reason,
    trend_sma_key,
)
from quant.research.multibagger_signals import DEFAULT_SIGNAL_PARAMS, setup_mask_at

DEFAULT_RUNNER_PARAMS: dict[str, Any] = {
    **DEFAULT_SIGNAL_PARAMS,
    **DEFAULT_MULTIBAGGER_EXIT_PARAMS,
    "dist_hi12_min": -0.55,
    "dist_hi12_max": -0.08,
    "ret_3_min": 0.03,
    "ret_6_min": -0.05,
    "ret_12_max": 1.0,
    "vol_ratio_min": 0.75,
    "min_months_history": 36,
    "min_price": 20.0,
    "regime_sma_months": 12,
}


def _symbol_panels(df: pd.DataFrame, bench: pd.Series) -> dict[str, pd.Series]:
    c = df.set_index("Date")["Close"].astype(float)
    h = df.set_index("Date")["High"].astype(float)
    idx = c.index
    hi12 = h.rolling(12, min_periods=12).max()
    ret1 = c.pct_change()
    br = bench.reindex(idx).pct_change(12)
    rel = c.pct_change(12) - br
    return {
        "close": c,
        "ret_3": c.pct_change(3),
        "ret_6": c.pct_change(6),
        "ret_12": c.pct_change(12),
        "rel_mom_12": rel,
        "vol_ratio": ret1.rolling(6).std() / ret1.rolling(24).std().replace(0, np.nan),
        "sma12": c.rolling(12).mean(),
        "sma24": c.rolling(24).mean(),
        "hi12": hi12,
        "dist_hi12": c / hi12 - 1.0,
        "brk12": c > hi12.shift(1),
        "above_sma12": c > c.rolling(12).mean(),
    }


def regime_series(bench: pd.Series, *, sma: int = 12) -> pd.Series:
    """Benchmark regime by calendar month (Period 'M' index)."""
    b = bench.sort_index()
    if not isinstance(b.index, pd.PeriodIndex):
        b = pd.Series(b.values, index=pd.to_datetime(b.index).to_period("M"))
    else:
        b = pd.Series(b.values, index=b.index)
    return (b > b.rolling(sma).mean()) & (b / b.shift(sma) - 1.0 > 0)


def multibagger_round_trips(
    symbol: str,
    df: pd.DataFrame,
    bench: pd.Series,
    regime: pd.Series,
    params: dict | None = None,
) -> pd.DataFrame:
    """One position at a time; enter on multibagger setup + regime; exit on rule stack."""
    p = {**DEFAULT_RUNNER_PARAMS, **(params or {})}
    need = {"Date", "Open", "High", "Close"}
    if df is None or not need.issubset(df.columns) or len(df) < int(p["min_months_history"]) + 5:
        return pd.DataFrame()
    df = df.sort_values("Date").copy()
    df["Date"] = pd.to_datetime(df["Date"])

    panels = _symbol_panels(df, bench)
    open_ = df.set_index("Date")["Open"].astype(float)
    dates = list(df["Date"])
    min_hist = int(p["min_months_history"])
    min_px = float(p["min_price"])
    sma_key = trend_sma_key(int(p["trend_exit_sma_months"]))

    wide = {
        k: pd.DataFrame({symbol: panels[k]})
        for k in (
            "ret_3",
            "ret_6",
            "ret_12",
            "dist_hi12",
            "vol_ratio",
            "brk12",
            "above_sma12",
            "rel_mom_12",
        )
    }

    trades: list[dict] = []
    in_pos = False
    entry_date = None
    entry_px = np.nan
    entry_i = 0
    peak = np.nan
    months_held = 0
    months_below_sma = 0

    for i in range(min_hist, len(dates) - 1):
        d = pd.Timestamp(dates[i])
        d_next = pd.Timestamp(dates[i + 1])
        per = d.to_period("M")
        reg_on = per in regime.index and bool(regime.loc[per])
        c = float(panels["close"].get(d, np.nan))
        if not in_pos and (not np.isfinite(c) or c < min_px):
            continue

        if in_pos:
            months_held += 1
            peak = max(peak, c)
            sma = float(panels[sma_key].get(d, np.nan))
            reason, months_below_sma = position_exit_reason(
                close=c,
                entry_px=entry_px,
                peak_close=peak,
                months_below_sma=months_below_sma,
                sma_trend=sma,
                regime_on=reg_on,
                months_held=months_held,
                p=p,
            )
            if reason:
                xp = float(open_.get(d_next, np.nan))
                if np.isfinite(xp):
                    trades.append(_trade_row(symbol, entry_date, d_next, entry_px, xp, entry_i, i + 1, reason))
                in_pos = False
                months_held = 0
                months_below_sma = 0
                peak = np.nan
            continue

        if not reg_on:
            continue
        setup = setup_mask_at(d, panels=wide, symbols=pd.Index([symbol]), p=p)
        if not bool(setup.get(symbol, False)):
            continue
        xp = float(open_.get(d_next, np.nan))
        if not np.isfinite(xp) or xp < min_px:
            continue
        in_pos = True
        entry_date = d_next
        entry_px = xp
        entry_i = i + 1
        peak = c
        months_held = 0
        months_below_sma = 0

    if in_pos and entry_date is not None:
        d_last = pd.Timestamp(dates[-1])
        xp = float(open_.get(d_last, np.nan))
        if np.isfinite(xp):
            trades.append(
                _trade_row(symbol, entry_date, d_last, entry_px, xp, entry_i, len(dates) - 1, "end_of_data")
            )

    return pd.DataFrame(trades)


def _trade_row(
    symbol: str,
    entry_date: Any,
    exit_date: Any,
    entry_px: float,
    exit_px: float,
    entry_i: int,
    exit_i: int,
    reason: str,
) -> dict:
    hold_days = (pd.Timestamp(exit_date) - pd.Timestamp(entry_date)).days
    gross = (exit_px / entry_px - 1.0) * 100.0 if entry_px > 0 else np.nan
    return {
        "symbol": symbol,
        "entry price": round(entry_px, 4),
        "entry date": pd.Timestamp(entry_date).strftime("%Y-%m-%d"),
        "exit price": round(exit_px, 4),
        "exit date": pd.Timestamp(exit_date).strftime("%Y-%m-%d"),
        "hold days": hold_days,
        "profit percent": round(gross, 4),
        "exit reason": reason,
        "hold months": exit_i - entry_i,
    }