"""Launch Base — multibagger entry fingerprint (monthly bars)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from quant.research.launch_base_constraints import LAUNCH_BASE_TIGHT_ENTRY
from quant.research.regime_momentum_portfolio import _load_benchmark_close, _monthly_close_panel

LAUNCH_BASE_VERSION = "1.3"

# Distilled from MCP 5× pattern scan (liquid names).
LAUNCH_BASE_ENTRY: dict[str, Any] = {
    "dist_hi12_min": -0.45,
    "dist_hi12_max": -0.12,
    "ret_3_min": 0.06,
    "ret_6_min": 0.03,
    "ret_12_min": -0.10,
    "ret_12_max": 0.55,
    "vol_ratio_min": 0.90,
    "breakout_rel_mom_min": 0.10,
    "use_breakout_path": True,
    "require_above_sma12_thrust": True,
    "min_rel_mom_thrust": 0.02,
}

DEFAULT_LAUNCH_BASE_PARAMS: dict[str, Any] = {
    **LAUNCH_BASE_ENTRY,
    **LAUNCH_BASE_TIGHT_ENTRY,
    "benchmark_symbol": "NIFTYBEES",
    "bench_sma_months": 12,
    "max_name_weight": 0.16,
    "min_history_months": 36,
    "initial_capital": 100_000.0,
    "cash_yield_annual": 0.06,
    "dd_hard_stop_pct": 10.0,
    "dd_soft_stop_pct": 6.0,
    "dd_soft_gross_scale": 0.40,
    "universe_min_close": 20.0,
    "universe_top_n_liquidity": 300,
    "universe_liquidity_lookback_months": 12,
    # Stock exits (no calendar hold).
    "trend_exit_sma_months": 24,
    "trend_confirm_months": 2,
    "stock_peak_dd_pct": 28.0,
    "trail_after_gain_pct": 60.0,
    "trail_tight_peak_dd_pct": 18.0,
    "entry_stop_pct": 20.0,
    "use_profit_floor": False,
    "profit_arm_pct": 0.0,
    "profit_floor_pct": 0.0,
    "max_hold_months": 0,
    # Regime filters new entries only (open positions use stock exits).
    "regime_blocks_new_entries_only": True,
    "min_entry_score": 0.08,
}


def build_panels(monthly: pd.DataFrame, bench: pd.Series) -> dict[str, pd.DataFrame]:
    close, _opn, _vol = _monthly_close_panel(monthly)
    lo = monthly.pivot(index="Date", columns="Symbol", values="Low").sort_index()
    hi = monthly.pivot(index="Date", columns="Symbol", values="High").sort_index()
    lo = lo.reindex(close.index).reindex(close.columns, axis=1)
    hi = hi.reindex(close.index).reindex(close.columns, axis=1)
    ret1 = close.pct_change()
    hi12 = hi.rolling(12).max()
    bench_ret12 = bench.reindex(close.index).pct_change(12)
    rel = close.pct_change(12).sub(bench_ret12, axis=0)
    return {
        "close": close,
        "low": lo,
        "sma12": close.rolling(12).mean(),
        "ret_3": close.pct_change(3),
        "ret_6": close.pct_change(6),
        "ret_12": close.pct_change(12),
        "rel_mom_12": rel,
        "vol_ratio": ret1.rolling(6).std() / ret1.rolling(24).std().replace(0, np.nan),
        "sma24": close.rolling(24).mean(),
        "dist_hi12": close / hi12 - 1.0,
        "brk12": close > hi12.shift(1),
        "above_sma12": close > close.rolling(12).mean(),
    }


def entry_mask_at(
    d: pd.Timestamp,
    panels: dict[str, pd.DataFrame],
    symbols: pd.Index,
    p: dict,
) -> pd.Series:
    ep = {**LAUNCH_BASE_ENTRY, **p}
    sym = symbols.intersection(panels["ret_3"].columns)
    if sym.empty:
        return pd.Series(dtype=bool)
    r3 = panels["ret_3"].loc[d, sym]
    r6 = panels["ret_6"].loc[d, sym]
    r12 = panels["ret_12"].loc[d, sym]
    dh = panels["dist_hi12"].loc[d, sym]
    vr = panels["vol_ratio"].loc[d, sym]
    ab = panels["above_sma12"].loc[d, sym]
    rel = panels["rel_mom_12"].loc[d, sym]
    thrust = (
        (r3 >= float(ep["ret_3_min"]))
        & (r6 >= float(ep["ret_6_min"]))
        & (r12 > float(ep["ret_12_min"]))
        & (r12 < float(ep["ret_12_max"]))
        & (dh > float(ep["dist_hi12_min"]))
        & (dh < float(ep["dist_hi12_max"]))
        & (vr >= float(ep["vol_ratio_min"]))
    )
    if bool(ep.get("require_above_sma12_thrust", False)):
        thrust = thrust & ab.fillna(False)
    min_rel = float(ep.get("min_rel_mom_thrust", -999))
    if min_rel > -900:
        thrust = thrust & (rel >= min_rel).fillna(False)
    if not bool(ep.get("use_breakout_path", True)):
        return thrust.fillna(False)
    brk = panels["brk12"].loc[d, sym]
    breakout = (
        brk.fillna(False)
        & ab.fillna(False)
        & (rel >= float(ep["breakout_rel_mom_min"]))
        & (r12 < float(ep["ret_12_max"]))
        & (r12 > float(ep["ret_12_min"]))
    )
    return (thrust | breakout).fillna(False)


def entry_score_at(d: pd.Timestamp, panels: dict[str, pd.DataFrame], symbols: pd.Index) -> pd.Series:
    """Higher = stronger launch (3m thrust + not too extended on 12m)."""
    sym = symbols.intersection(panels["ret_3"].columns)
    r3 = panels["ret_3"].loc[d, sym].fillna(0)
    rel = panels["rel_mom_12"].loc[d, sym].fillna(0)
    dh = panels["dist_hi12"].loc[d, sym].fillna(0)
    base_depth = (-dh - 0.12).clip(lower=0, upper=0.35)
    return r3 + 0.12 * rel + 0.08 * base_depth


def regime_on_at(
    d: pd.Timestamp,
    bench: pd.Series,
    *,
    sma_months: int = 12,
    min_mom: float = 0.0,
) -> bool:
    b = bench.sort_index()
    if d not in b.index:
        return False
    sma = b.rolling(sma_months).mean().loc[d]
    mom = b / b.shift(sma_months) - 1.0
    m = mom.loc[d] if d in mom.index else np.nan
    if not np.isfinite(sma) or not np.isfinite(m):
        return False
    return float(b.loc[d]) >= float(sma) and float(m) >= min_mom


def load_bench(monthly: pd.DataFrame, symbol: str = "NIFTYBEES") -> pd.Series:
    return _load_benchmark_close(symbol, monthly).sort_index()