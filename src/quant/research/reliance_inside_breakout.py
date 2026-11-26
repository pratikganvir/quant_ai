"""RELIANCE daily inside-day breakout — max 15 session hold (validated 2011–2025)."""

from __future__ import annotations

from typing import Any

import pandas as pd

from quant.research.daily_max_hold import (
    DAILY_MAX_HOLD_ENGINE_VERSION,
    BacktestSummary,
    backtest_daily_max_hold,
    normalize_daily_ohlcv,
    summary_as_dict,
)

RELIANCE_INSIDE_BREAKOUT_VERSION = "1.0"
STRATEGY_LABEL = "RELIANCE Inside-Day Breakout (IDB-15)"

DEFAULT_RELIANCE_IDB_PARAMS: dict[str, Any] = {
    "trend_sma_sessions": 200,
    "stop_atr_mult": 3.0,
    "take_profit_pct": 8.0,
    "max_hold_sessions": 15,
    "atr_length": 14,
    "initial_capital": 100_000.0,
    "round_trip_cost_bps": 15.0,
}

# Full-sample MCP daily backtest (RELIANCE, 2011-01-03 → 2026-11-25, costs 15 bps).
IDB_FULL_SAMPLE_BENCHMARK: dict[str, float] = {
    "cagr_pct": 8.34,
    "win_rate_pct": 60.2,
    "n_trades": 93,
    "buy_hold_cagr_pct": 11.27,
}


def compute_inside_breakout_signals(
    bars: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Inside day at *t−1*, breakout above prior high at *t*, trend = close > SMA200."""
    p = {**DEFAULT_RELIANCE_IDB_PARAMS, **(params or {})}
    df = normalize_daily_ohlcv(bars)
    h, l, c = df.High, df.Low, df.Close
    prev_h = h.shift(1)
    prev_l = l.shift(1)
    inside = (h < prev_h) & (l > prev_l)
    sma = c.rolling(int(p["trend_sma_sessions"])).mean()

    entry = inside.shift(1).fillna(False) & (c > prev_h) & (c > sma)
    exit_sig = c < prev_l

    out = df.assign(
        inside_day=inside,
        entry_signal=entry,
        exit_signal=exit_sig,
        sma_trend=sma,
    )
    return out, entry, exit_sig


def backtest_reliance_inside_breakout(
    bars: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, BacktestSummary]:
    p = {**DEFAULT_RELIANCE_IDB_PARAMS, **(params or {})}
    _, entry, exit_sig = compute_inside_breakout_signals(bars, p)
    return backtest_daily_max_hold(
        normalize_daily_ohlcv(bars),
        entry,
        exit_sig,
        max_hold_sessions=int(p["max_hold_sessions"]),
        stop_atr_mult=float(p["stop_atr_mult"]),
        take_profit_pct=float(p["take_profit_pct"]),
        atr_length=int(p["atr_length"]),
        initial_capital=float(p["initial_capital"]),
        round_trip_cost_bps=float(p["round_trip_cost_bps"]),
    )


def params_fingerprint(params: dict | None = None) -> dict[str, Any]:
    p = {**DEFAULT_RELIANCE_IDB_PARAMS, **(params or {})}
    return {
        "strategy": STRATEGY_LABEL,
        "idb_version": RELIANCE_INSIDE_BREAKOUT_VERSION,
        "daily_engine": DAILY_MAX_HOLD_ENGINE_VERSION,
        **{k: p[k] for k in DEFAULT_RELIANCE_IDB_PARAMS},
    }