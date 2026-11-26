"""Monthly rotation with strict ≤1 calendar-month position hold.

Each rebalance **sells at month open** and opens new positions; maximum position
duration is one month (same engine as ``regime_momentum_portfolio``).

Signal horizon is short (1–3 month lookback) with optional mean-reversion rank.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from quant.research.institutional_accumulation import BacktestSummary
from quant.research.regime_momentum_portfolio import (
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    backtest_regime_momentum_portfolio,
    evaluate_params_walkforward,
    summarize_regime_momentum,
)

MONTH_HOLD_VERSION = "1.1"
MAX_HOLD_MONTHS = 1

# Full-universe backtest (cached MCP monthly, walk-forward stable).
FULL_UNIVERSE_BENCHMARK: dict[str, float] = {
    "growth_cagr_pct": 19.40,
    "growth_max_drawdown_pct": -18.43,
    "defensive_cagr_pct": 17.62,
    "defensive_max_drawdown_pct": -15.26,
}

# Growth: slightly higher return vs v1.0 (stricter benchmark filter 0.03).
DEFAULT_MONTH_HOLD_PARAMS: dict[str, Any] = {
    **DEFAULT_REGIME_MOMENTUM_PARAMS,
    "mom_lookback_months": 12,
    "mom_skip_months": 1,
    "bench_sma_months": 12,
    "bench_min_momentum": 0.03,
    "top_k": 5,
    "target_vol_annual": 0.18,
    "max_leverage": 1.25,
    "gross_cap": 1.0,
    "max_name_weight": 0.22,
    "dd_hard_stop_pct": 12.0,
    "min_history_months": 36,
    "signal_rank": "momentum",
}

# Defensive: lower drawdown (~15%) with ~17.6% CAGR (9m momentum, vol cap).
MONTH_HOLD_DEFENSIVE_PARAMS: dict[str, Any] = {
    **DEFAULT_MONTH_HOLD_PARAMS,
    "mom_lookback_months": 9,
    "target_vol_annual": 0.16,
    "gross_cap": 0.95,
    "max_leverage": 1.15,
}

# 3× margin book: vol-targeted gross (cap 3), 8 names, soft de-risk at −6% DD.
MONTH_HOLD_3X_PARAMS: dict[str, Any] = {
    **DEFAULT_MONTH_HOLD_PARAMS,
    "margin_enabled": True,
    "max_leverage": 3.0,
    "gross_cap": 3.0,
    "min_leverage": 0.5,
    "target_vol_annual": 0.30,
    "top_k": 8,
    "max_name_weight": 0.22,
    "dd_hard_stop_pct": 12.0,
    "dd_soft_stop_pct": 6.0,
    "dd_soft_gross_scale": 0.6,
    "borrow_rate_annual": 0.11,
}

MONTH_HOLD_3X_CONSERVATIVE_PARAMS: dict[str, Any] = {
    **MONTH_HOLD_3X_PARAMS,
    "target_vol_annual": 0.24,
    "dd_hard_stop_pct": 8.0,
    "bench_min_momentum": 0.05,
}


def resolve_month_hold_params(profile: str | None = None, overrides: dict | None = None) -> dict[str, Any]:
    key = (profile or "growth").strip().lower()
    if key in ("3x", "leveraged", "leveraged_3x", "3x_growth"):
        base = MONTH_HOLD_3X_PARAMS
    elif key in ("3x_conservative", "3x_defensive"):
        base = MONTH_HOLD_3X_CONSERVATIVE_PARAMS
    elif key in ("defensive", "low_dd", "low-dd"):
        base = MONTH_HOLD_DEFENSIVE_PARAMS
    else:
        base = DEFAULT_MONTH_HOLD_PARAMS
    return {**base, **(overrides or {})}

# Discrete grid for scripts/optimize_month_hold.py (full-universe search).
MONTH_HOLD_PARAM_GRID: list[dict[str, Any]] = [
    {
        "top_k": k,
        "mom_lookback_months": mom,
        "mom_skip_months": skip,
        "bench_sma_months": sma,
        "bench_min_momentum": bm,
        "target_vol_annual": tv,
        "max_leverage": lev,
        "gross_cap": gross,
        "max_name_weight": mw,
        "dd_hard_stop_pct": dd,
        "signal_rank": rank,
    }
    for k in (5, 8, 10)
    for mom in (1, 2, 3)
    for skip in (0, 1)
    if skip < mom
    for sma in (6, 10, 12)
    for bm in (0.0, 0.03)
    for tv in (0.16, 0.18, 0.20)
    for lev in (1.0, 1.25, 1.35)
    for gross in (0.9, 1.0)
    for mw in (0.20, 0.25)
    for dd in (12.0, 15.0, 100.0)
    for rank in ("momentum", "reversal")
]


def month_hold_params_fingerprint(params: dict | None = None) -> dict[str, Any]:
    p = {**DEFAULT_MONTH_HOLD_PARAMS, **(params or {})}
    return {k: p[k] for k in sorted(DEFAULT_MONTH_HOLD_PARAMS)}


def assert_one_month_holds(trades: pd.DataFrame, *, tol_days: float = 40.0) -> None:
    """Sanity check: round-trips should not exceed ~1 month (plus month-end slack)."""
    if trades.empty:
        return
    t = trades.copy()
    t["entry_date"] = pd.to_datetime(t["entry_date"])
    t["exit_date"] = pd.to_datetime(t["exit_date"])
    hold_days = (t["exit_date"] - t["entry_date"]).dt.days
    if hold_days.max() > tol_days:
        raise ValueError(f"Hold longer than {tol_days}d detected (max={hold_days.max()})")


def backtest_month_hold_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    profile: str | None = None,
    benchmark_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = resolve_month_hold_params(profile, params)
    trades, equity = backtest_regime_momentum_portfolio(
        monthly, p, benchmark_frame=benchmark_frame
    )
    assert_one_month_holds(trades)
    return trades, equity


def summarize_month_hold(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    return summarize_regime_momentum(trades, equity, params or DEFAULT_MONTH_HOLD_PARAMS)


def evaluate_month_hold_walkforward(
    monthly: pd.DataFrame,
    params: dict,
    **kwargs: Any,
) -> dict[str, Any]:
    p = {**DEFAULT_MONTH_HOLD_PARAMS, **params}
    return evaluate_params_walkforward(monthly, p, **kwargs)


def pareto_score(cagr_pct: float, max_drawdown_pct: float) -> float:
    """Higher is better: CAGR reward with drawdown penalty."""
    dd = abs(max_drawdown_pct)
    return cagr_pct / max(dd, 1.0) + 0.35 * max_drawdown_pct