"""Conviction Peak — multibagger entry + exits calibrated to peak-giveback study."""

from __future__ import annotations

from typing import Any

# Calibrated from analyze_multibagger_drawdowns.py (MCP monthly, 2x/5x MFE paths).
DRAWDOWN_CALIBRATION: dict[str, float] = {
    "peak_trail_while_building_pct": 44.0,  # ~median giveback before 2x
    "peak_trail_after_double_pct": 24.0,  # ~p90 giveback once already 2x
    "double_gain_pct": 100.0,
    "catastrophe_stop_pct": 30.0,
    "book_fuse_pct": 28.0,
}

CONVICTED_ENTRY: dict[str, Any] = {
    "dist_hi12_min": -0.45,
    "dist_hi12_max": -0.12,
    "ret_3_min": 0.05,
    "ret_6_min": 0.02,
    "ret_12_min": -0.15,
    "ret_12_max": 0.60,
    "vol_ratio_min": 0.88,
    "breakout_rel_mom_min": 0.08,
    "use_breakout_path": True,
    "require_above_sma12_thrust": True,
    "min_rel_mom_thrust": 0.04,
}

DEFAULT_CONVICTION_PEAK_PARAMS: dict[str, Any] = {
    **CONVICTED_ENTRY,
    **DRAWDOWN_CALIBRATION,
    "benchmark_symbol": "NIFTYBEES",
    "bench_sma_months": 12,
    "bench_min_momentum": 0.02,
    "top_k": 7,
    "gross_cap": 0.95,
    "max_name_weight": 0.17,
    "min_history_months": 36,
    "initial_capital": 100_000.0,
    "cash_yield_annual": 0.05,
    "universe_min_close": 20.0,
    "universe_top_n_liquidity": 300,
    "universe_liquidity_lookback_months": 12,
    "regime_blocks_new_entries_only": True,
    "min_entry_score": 0.07,
    "trend_confirm_months": 3,
    "underwater_sma24_months": 2,
}

CONVICTION_PEAK_VERSION = "1.0"