"""Exits matched to multibagger peak-giveback distribution (monthly bars)."""

from __future__ import annotations

from typing import Any

import numpy as np

from quant.research.conviction_peak_signals import DRAWDOWN_CALIBRATION


def conviction_peak_exit(
    *,
    close: float,
    low: float,
    entry_px: float,
    peak_close: float,
    months_below_sma24: int,
    in_profit: bool,
    p: dict,
    book_fused: bool = False,
) -> tuple[str | None, int]:
    cal = {**DRAWDOWN_CALIBRATION, **p}
    if book_fused:
        return "book_fuse", 0

    cat = float(cal["catastrophe_stop_pct"])
    stop_px = entry_px * (1.0 - cat / 100.0)
    if entry_px > 0 and np.isfinite(low) and low <= stop_px:
        return "catastrophe_stop", 0
    if entry_px > 0 and np.isfinite(close) and close <= stop_px:
        return "catastrophe_stop", 0

    if peak_close > 0 and entry_px > 0 and np.isfinite(close):
        gain_pct = (peak_close / entry_px - 1.0) * 100.0
        trail = (
            float(cal["peak_trail_after_double_pct"])
            if gain_pct >= float(cal["double_gain_pct"])
            else float(cal["peak_trail_while_building_pct"])
        )
        dd = (close / peak_close - 1.0) * 100.0
        if dd <= -trail:
            tag = "peak_trail_after_2x" if gain_pct >= float(cal["double_gain_pct"]) else "peak_trail_build"
            return tag, months_below_sma24

    if not in_profit and np.isfinite(close) and entry_px > 0:
        if months_below_sma24 >= int(cal.get("underwater_sma24_months", 2)):
            if close < entry_px * 0.92:
                return "trend_break_underwater", months_below_sma24

    if in_profit and months_below_sma24 >= int(cal.get("trend_confirm_months", 3)):
        return "trend_break", months_below_sma24

    return None, months_below_sma24


def update_sma24_streak(close: float, sma24: float, streak: int) -> int:
    if np.isfinite(sma24) and np.isfinite(close) and close < sma24:
        return streak + 1
    return 0