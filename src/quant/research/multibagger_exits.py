"""Multibagger position exits — rule-based only (no calendar max/min hold)."""

from __future__ import annotations

from typing import Any

import numpy as np

DEFAULT_MULTIBAGGER_EXIT_PARAMS: dict[str, Any] = {
    # Benchmark regime (book-level risk_off may also apply).
    "bench_min_momentum": 0.0,
    # Stock trend: N consecutive month closes below SMA.
    "trend_exit_sma_months": 24,
    "trend_confirm_months": 2,
    # Peak trailing stop (% drawdown from highest close since entry).
    "stock_peak_dd_pct": 35.0,
    # After this gain vs entry, tighten trail (let multibaggers run, then protect).
    "trail_after_gain_pct": 100.0,
    "trail_tight_peak_dd_pct": 25.0,
    # Hard stop from entry month close (catastrophic failure).
    "entry_stop_pct": 25.0,
    # Optional calendar cap; 0 = disabled.
    "max_hold_months": 0,
}


def trend_sma_key(months: int) -> str:
    return "sma24" if int(months) >= 24 else "sma12"


def position_exit_reason(
    *,
    close: float,
    entry_px: float,
    peak_close: float,
    months_below_sma: int,
    sma_trend: float,
    regime_on: bool,
    months_held: int,
    p: dict,
    book_fused: bool = False,
) -> tuple[str | None, int]:
    """
    Evaluate exit at month-end signal (exit next open).
    Returns (exit_reason or None, updated months_below_sma).
    """
    ep = {**DEFAULT_MULTIBAGGER_EXIT_PARAMS, **p}
    if book_fused:
        return "book_fuse", 0
    if not regime_on:
        return "regime_off", 0

    max_hold = int(ep.get("max_hold_months") or 0)
    if max_hold > 0 and months_held >= max_hold:
        return "max_hold", months_below_sma

    if entry_px > 0 and np.isfinite(close):
        stop = float(ep["entry_stop_pct"])
        if close <= entry_px * (1.0 - stop / 100.0):
            return "entry_stop", 0

    if peak_close > 0 and np.isfinite(close):
        gain_thresh = float(ep["trail_after_gain_pct"])
        wide = float(ep["stock_peak_dd_pct"])
        tight = float(ep["trail_tight_peak_dd_pct"])
        trail = tight if peak_close >= entry_px * (1.0 + gain_thresh / 100.0) else wide
        dd = (close / peak_close - 1.0) * 100.0
        if dd <= -trail:
            return "stock_trail", 0

    if np.isfinite(sma_trend) and np.isfinite(close) and close < sma_trend:
        months_below_sma += 1
    else:
        months_below_sma = 0
    if months_below_sma >= int(ep["trend_confirm_months"]):
        return "trend_break", months_below_sma

    return None, months_below_sma