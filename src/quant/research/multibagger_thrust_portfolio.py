"""Regime + multibagger entry sleeve (monthly NSE, shared cash book).

Exits: regime off, 2× monthly close below SMA₂₄, peak trail (tighter after +100%),
entry catastrophic stop. No calendar min/max hold unless you set max_hold_months > 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant.research.institutional_accumulation import BacktestSummary, summarize_backtest
from quant.research.multibagger_exits import (
    DEFAULT_MULTIBAGGER_EXIT_PARAMS,
    position_exit_reason,
    trend_sma_key,
)
from quant.research.multibagger_signals import (
    DEFAULT_SIGNAL_PARAMS,
    indicator_panels,
    setup_mask_at,
)
from quant.research.regime_momentum_portfolio import (
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    _eligible_momentum_symbols,
    _load_benchmark_close,
    _monthly_close_panel,
    backtest_regime_momentum_portfolio,
    summarize_regime_momentum,
)

MULTIBAGGER_THRUST_VERSION = "1.3"

DEFAULT_MULTIBAGGER_THRUST_PARAMS: dict[str, Any] = {
    **DEFAULT_SIGNAL_PARAMS,
    **DEFAULT_MULTIBAGGER_EXIT_PARAMS,
    "dist_hi12_min": -0.55,
    "dist_hi12_max": -0.08,
    "ret_3_min": 0.03,
    "ret_6_min": -0.05,
    "ret_12_max": 1.0,
    "vol_ratio_min": 0.75,
    "benchmark_symbol": "NIFTYBEES",
    "top_k": 5,
    "bench_sma_months": 12,
    "bench_min_momentum": 0.03,
    "min_history_months": 36,
    "gross_cap": 1.0,
    "max_name_weight": 0.25,
    "dd_hard_stop_pct": 12.0,
    "cash_yield_annual": 0.06,
    "initial_capital": 100_000.0,
    "universe_min_close": 20.0,
    "universe_top_n_liquidity": 400,
    "universe_liquidity_lookback_months": 12,
    "universe_exclude_symbols": (),
    "rank_col": "ret_3",
}

# Monthly rebalance + 12-1 momentum with multibagger rank boost (~regime-momentum CAGR).
MULTIBAGGER_HYBRID_PARAMS: dict[str, Any] = {
    **DEFAULT_REGIME_MOMENTUM_PARAMS,
    "universe_top_n_liquidity": 400,
    "thrust_rank_boost": 0.08,
    "thrust_ret3_boost": 0.45,
    **{k: DEFAULT_SIGNAL_PARAMS[k] for k in DEFAULT_SIGNAL_PARAMS},
}

# Prior defaults (v1.0) for A/B — early SMA₁₂ churn
MULTIBAGGER_THRUST_V10_PARAMS: dict[str, Any] = {
    **DEFAULT_MULTIBAGGER_THRUST_PARAMS,
    "top_k": 8,
    "bench_min_momentum": 0.0,
    "max_hold_months": 36,
    "stock_peak_dd_pct": 30.0,
    "min_months_before_trend_exit": 0,
    "trend_exit_sma_months": 12,
    "use_breakout_path": False,
    "max_name_weight": 0.20,
}


@dataclass
class _Holding:
    shares: float
    entry_px: float
    entry_date: pd.Timestamp
    peak_close: float
    months_held: int
    months_below_sma: int = 0


def backtest_multibagger_hybrid_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    benchmark_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Regime momentum + multibagger rank boost (monthly rebalance; best CAGR)."""
    p = {**MULTIBAGGER_HYBRID_PARAMS, **(params or {})}
    return backtest_regime_momentum_portfolio(monthly, p, benchmark_frame=benchmark_frame)


def summarize_multibagger_hybrid(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    p = {**MULTIBAGGER_HYBRID_PARAMS, **(params or {})}
    return summarize_regime_momentum(trades, equity, p)


def backtest_multibagger_thrust_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    benchmark_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared book; entries on month open; exits on rules below."""
    p = {**DEFAULT_MULTIBAGGER_THRUST_PARAMS, **(params or {})}
    bench = _load_benchmark_close(str(p["benchmark_symbol"]), monthly, benchmark_frame)
    close, panels = indicator_panels(monthly, bench)
    _close, opn, volume = _monthly_close_panel(monthly)
    opn = opn.reindex(close.index)
    volume = volume.reindex(close.index)
    bench = bench.reindex(close.index).ffill()
    dates = list(close.index)
    if len(dates) < int(p["min_history_months"]) + 2:
        return pd.DataFrame(), pd.DataFrame()

    sma_lb = int(p["bench_sma_months"])
    cash_m = 1.0 + float(p["cash_yield_annual"]) / 12.0
    top_k = int(p["top_k"])
    rank_col = str(p.get("rank_col", "ret_3"))
    trend_key = trend_sma_key(int(p["trend_exit_sma_months"]))

    cash = float(p["initial_capital"])
    holdings: dict[str, _Holding] = {}
    peak = cash
    trades: list[dict] = []
    equity_rows: list[dict] = []

    bench_sma = bench.rolling(sma_lb, min_periods=sma_lb).mean()
    bench_mom = bench / bench.shift(sma_lb) - 1.0

    def invested_mtm(d: pd.Timestamp) -> float:
        row = close.loc[d]
        return sum(
            h.shares * float(row[sym])
            for sym, h in holdings.items()
            if sym in row.index and pd.notna(row[sym])
        )

    def mtm(d: pd.Timestamp) -> float:
        return cash + invested_mtm(d)

    def liquidate(sym: str, h: _Holding, d: pd.Timestamp, px: float, reason: str) -> None:
        nonlocal cash
        cash += h.shares * float(px)
        trades.append(
            {
                "Symbol": sym,
                "entry_date": h.entry_date,
                "exit_date": d,
                "entry_px": h.entry_px,
                "exit_px": float(px),
                "shares": h.shares,
                "pnl": h.shares * (float(px) - h.entry_px),
                "reason": reason,
            }
        )
        del holdings[sym]

    for i in range(1, len(dates)):
        d_prev, d = dates[i - 1], dates[i]
        row_open = opn.loc[d]

        if i <= max(sma_lb, 24, int(p["trend_exit_sma_months"])):
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        idle_cash = cash
        if idle_cash > 0 and not holdings:
            cash = idle_cash * cash_m
        elif idle_cash > 0:
            cash = idle_cash * (cash_m ** (1.0 / 12.0))

        eq_pre = mtm(d_prev)
        peak = max(peak, eq_pre)
        dd_pct = (eq_pre / peak - 1.0) * 100.0 if peak > 0 else 0.0
        fused = dd_pct <= -float(p["dd_hard_stop_pct"])

        bpx = float(bench.loc[d_prev])
        risk_on = (
            not fused
            and pd.notna(bench_sma.loc[d_prev])
            and bpx >= float(bench_sma.loc[d_prev])
            and float(bench_mom.loc[d_prev]) >= float(p["bench_min_momentum"])
        )

        for sym, h in list(holdings.items()):
            h.months_held += 1
            c_prev = float(close.loc[d_prev, sym]) if sym in close.columns else np.nan
            if np.isfinite(c_prev):
                h.peak_close = max(h.peak_close, c_prev)
            px = row_open.get(sym, np.nan)
            if not pd.notna(px):
                continue
            sma = panels[trend_key].loc[d_prev, sym] if sym in panels[trend_key].columns else np.nan
            sma_f = float(sma) if pd.notna(sma) else np.nan
            reason, h.months_below_sma = position_exit_reason(
                close=c_prev,
                entry_px=h.entry_px,
                peak_close=h.peak_close,
                months_below_sma=h.months_below_sma,
                sma_trend=sma_f,
                regime_on=risk_on,
                months_held=h.months_held,
                p=p,
                book_fused=fused,
            )
            if reason:
                tag = "risk_off" if reason in ("regime_off", "book_fuse") else reason
                liquidate(sym, h, d, float(px), tag)

        if not risk_on:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        slots = top_k - len(holdings)
        if slots <= 0:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        eligible = _eligible_momentum_symbols(d_prev, close=close, volume=volume, p=p)
        eligible = eligible.difference(list(holdings.keys()))
        setup = setup_mask_at(d_prev, panels=panels, symbols=eligible, p=p)
        cands = setup.loc[setup].index.tolist()
        if not cands:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        rank = panels[rank_col].loc[d_prev, cands].dropna().sort_values(ascending=False)
        picks = list(rank.head(slots).index)
        eq = mtm(d_prev)
        gross = float(p["gross_cap"])
        target_inv = eq * min(gross, 1.0)
        current_inv = invested_mtm(d_prev)
        headroom = max(0.0, target_inv - current_inv)
        max_w = float(p["max_name_weight"])
        per_slot = min(eq * max_w, headroom / max(slots, 1)) if headroom > 0 else 0.0

        for sym in picks:
            px = row_open.get(sym, np.nan)
            if not pd.notna(px) or float(px) <= 0 or per_slot <= 100:
                continue
            alloc = per_slot
            if alloc > cash:
                alloc = cash
            if alloc <= 100:
                continue
            sh = alloc / float(px)
            cash -= alloc
            c0 = float(close.loc[d_prev, sym]) if sym in close.columns else float(px)
            holdings[sym] = _Holding(
                shares=sh,
                entry_px=float(px),
                entry_date=d,
                peak_close=c0,
                months_held=0,
            )

        equity_rows.append({"Date": d, "equity": mtm(d)})

    if holdings:
        d_last = dates[-1]
        for sym, h in list(holdings.items()):
            px = float(close.loc[d_last, sym])
            liquidate(sym, h, d_last, px, "end_of_data")
        equity_rows[-1]["equity"] = cash

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def summarize_multibagger_thrust(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    p = {**DEFAULT_MULTIBAGGER_THRUST_PARAMS, **(params or {})}
    ic = float(p["initial_capital"])
    return summarize_backtest(trades, equity, ic)