"""Launch Base exits: ≤20% loss per trade; asymmetric trend (tight when red, loose when green)."""

from __future__ import annotations

from typing import Any

import numpy as np

from quant.research.multibagger_exits import DEFAULT_MULTIBAGGER_EXIT_PARAMS


def launch_exit_reason(
    *,
    close: float,
    low: float,
    entry_px: float,
    peak_close: float,
    months_below_sma: int,
    months_below_sma12: int,
    sma24: float,
    sma12: float,
    months_held: int,
    p: dict,
    book_fused: bool = False,
) -> tuple[str | None, int, int]:
    ep = {**DEFAULT_MULTIBAGGER_EXIT_PARAMS, **p}
    stop = float(ep["entry_stop_pct"])
    if book_fused:
        return "book_fuse", 0, 0

    stop_px = entry_px * (1.0 - stop / 100.0) if entry_px > 0 else np.nan
    if entry_px > 0 and np.isfinite(low) and low <= stop_px:
        return "entry_stop", 0, 0
    if entry_px > 0 and np.isfinite(close) and close <= stop_px:
        return "entry_stop", 0, 0

    if bool(ep.get("use_profit_floor", False)):
        arm = float(ep.get("profit_arm_pct", 8.0))
        floor = float(ep.get("profit_floor_pct", 2.0))
        if (
            entry_px > 0
            and peak_close >= entry_px * (1.0 + arm / 100.0)
            and np.isfinite(low)
            and low <= entry_px * (1.0 + floor / 100.0)
        ):
            return "profit_floor", 0, 0

    in_profit = np.isfinite(close) and close >= entry_px

    if in_profit and peak_close > 0:
        gain_thresh = float(ep.get("trail_after_gain_pct", 60))
        wide = float(ep["stock_peak_dd_pct"])
        tight = float(ep.get("trail_tight_peak_dd_pct", 18))
        trail = tight if peak_close >= entry_px * (1.0 + gain_thresh / 100.0) else wide
        dd = (close / peak_close - 1.0) * 100.0
        if dd <= -trail:
            return "stock_trail", months_below_sma, months_below_sma12

    if not in_profit:
        if np.isfinite(sma12) and np.isfinite(close) and close < sma12:
            months_below_sma12 += 1
        else:
            months_below_sma12 = 0
        if months_below_sma12 >= 1:
            return "trend_break_loser", months_below_sma, months_below_sma12

    if np.isfinite(sma24) and np.isfinite(close) and close < sma24:
        months_below_sma += 1
    else:
        months_below_sma = 0
    if in_profit and months_below_sma >= int(ep.get("trend_confirm_months", 2)):
        return "trend_break", months_below_sma, months_below_sma12

    return None, months_below_sma, months_below_sma12