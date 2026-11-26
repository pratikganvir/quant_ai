"""Early-cycle book: enter when benchmark tide *turns*, before full regime confirmation."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from quant.research.institutional_accumulation import BacktestSummary, summarize_backtest
from quant.research.regime_momentum_portfolio import (
    _Holding,
    _eligible_momentum_symbols,
    _load_benchmark_close,
    _monthly_close_panel,
)

REGIME_TURN_VERSION = "1.0"

# Anticipatory sleeve — validated on Nifty 50 MCP panel vs regime_momentum (see simulate script).
DEFAULT_REGIME_TURN_PARAMS: dict[str, Any] = {
    "benchmark_symbol": "NIFTYBEES",
    "initial_capital": 100_000.0,
    "top_k": 5,
    # Faster leadership: 6–1 cross-sectional momentum
    "mom_lookback_months": 6,
    "mom_skip_months": 1,
    "vol_lookback_months": 12,
    "target_vol_annual": 0.26,
    "max_leverage": 1.25,
    "min_leverage": 0.35,
    "gross_cap": 1.0,
    "max_name_weight": 0.24,
    "dd_hard_stop_pct": 15.0,
    "dd_soft_stop_pct": 9.0,
    "dd_soft_gross_scale": 0.55,
    "cash_yield_annual": 0.06,
    "min_history_months": 36,
    "universe_min_close": 20.0,
    "universe_top_n_liquidity": 200,
    "universe_liquidity_lookback_months": 12,
    "universe_min_median_volume": 0.0,
    "universe_exclude_symbols": (),
    "signal_rank": "momentum",
    "min_name_momentum": 0.0,
    "margin_enabled": False,
    # Turn detection (benchmark, month-end)
    "bench_sma_fast": 6,
    "bench_sma_slow": 12,
    "turn_min_3m_return": 0.02,
    "turn_latch_months": 12,
    "turn_exit_below_sma_fast": True,
}


def _turn_risk_on(
    bench: pd.Series,
    d_prev: pd.Timestamp,
    *,
    p: dict,
    latch_remaining: int,
) -> tuple[bool, int, str]:
    """Return (risk_on, new_latch_remaining, reason_tag)."""
    fast = int(p["bench_sma_fast"])
    slow = int(p["bench_sma_slow"])
    sma_f = bench.rolling(fast, min_periods=fast).mean()
    sma_s = bench.rolling(slow, min_periods=slow).mean()
    if d_prev not in bench.index:
        return False, 0, "no_data"
    b = float(bench.loc[d_prev])
    sf = float(sma_f.loc[d_prev]) if pd.notna(sma_f.loc[d_prev]) else np.nan
    ss = float(sma_s.loc[d_prev]) if pd.notna(sma_s.loc[d_prev]) else np.nan
    mom3 = float(bench.loc[d_prev] / bench.shift(3).loc[d_prev] - 1) if d_prev in bench.shift(3).index else np.nan
    mom12 = float(bench.loc[d_prev] / bench.shift(12).loc[d_prev] - 1) if d_prev in bench.shift(12).index else np.nan

    # Hard exit while latched
    if p.get("turn_exit_below_sma_fast") and latch_remaining > 0 and np.isfinite(sf) and b < sf:
        return False, 0, "sma_fast_break"

    cross_up = False
    prev = bench.index.get_loc(d_prev) - 1
    if prev >= slow:
        d_pp = bench.index[prev]
        ss_prev = float(sma_s.loc[d_pp]) if pd.notna(sma_s.loc[d_pp]) else np.nan
        b_prev = float(bench.loc[d_pp])
        if np.isfinite(ss) and np.isfinite(ss_prev):
            cross_up = b > ss and b_prev <= ss_prev

    pre_regime = (
        np.isfinite(sf)
        and np.isfinite(ss)
        and b > sf
        and sf > float(sma_f.shift(1).loc[d_prev])
        and np.isfinite(mom3)
        and mom3 >= float(p["turn_min_3m_return"])
        and (b < ss or (np.isfinite(mom12) and mom12 < 0))
    )

    full_regime = np.isfinite(ss) and b >= ss and np.isfinite(mom12) and mom12 >= 0.0

    if cross_up or pre_regime or full_regime:
        latch = int(p["turn_latch_months"])
        return True, latch, ("cross" if cross_up else "pre_regime" if pre_regime else "full_regime")

    if latch_remaining > 0:
        return True, latch_remaining - 1, "latched"

    return False, 0, "off"


def backtest_regime_turn_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    benchmark_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = {**DEFAULT_REGIME_TURN_PARAMS, **(params or {})}
    close, opn, volume = _monthly_close_panel(monthly)
    bench = _load_benchmark_close(str(p["benchmark_symbol"]), monthly, benchmark_frame).reindex(close.index).ffill()
    dates = list(close.index)
    mom_lb = int(p["mom_lookback_months"])
    skip = int(p["mom_skip_months"])
    vol_lb = int(p["vol_lookback_months"])
    cash_m = 1.0 + float(p["cash_yield_annual"]) / 12.0

    if len(dates) < int(p["min_history_months"]) + 2:
        return pd.DataFrame(), pd.DataFrame()

    cash = float(p["initial_capital"])
    holdings: dict[str, _Holding] = {}
    peak = cash
    trades: list[dict] = []
    equity_rows: list[dict] = []
    rets = close.pct_change()
    latch_remaining = 0

    def mtm(d: pd.Timestamp) -> float:
        row = close.loc[d]
        total = cash
        for sym, h in holdings.items():
            px = row.get(sym, np.nan)
            if pd.notna(px):
                total += h.shares * float(px)
        return total

    for i in range(1, len(dates)):
        d_prev, d = dates[i - 1], dates[i]
        row_open = opn.loc[d]

        if i <= max(mom_lb + skip, vol_lb, int(p["bench_sma_slow"]) + 2):
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        eq_pre = mtm(d_prev)
        peak = max(peak, eq_pre)
        dd_pct = (eq_pre / peak - 1.0) * 100.0 if peak > 0 else 0.0
        fused = dd_pct <= -float(p["dd_hard_stop_pct"])

        risk_on, latch_remaining, turn_tag = (
            (False, 0, "fused") if fused else _turn_risk_on(bench, d_prev, p=p, latch_remaining=latch_remaining)
        )

        if not risk_on:
            for sym, h in list(holdings.items()):
                px = row_open.get(sym, np.nan)
                if pd.notna(px):
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
                            "reason": f"risk_off:{turn_tag}",
                        }
                    )
            holdings.clear()
            cash *= cash_m
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        p1 = close.shift(skip).loc[d_prev]
        p0 = close.shift(mom_lb + skip).loc[d_prev]
        mom = (p1 / p0 - 1.0).dropna()
        eligible = _eligible_momentum_symbols(d_prev, close=close, volume=volume, p=p)
        if len(eligible):
            mom = mom.reindex(eligible).dropna()
        min_nm = float(p.get("min_name_momentum", -999))
        mom = mom.loc[mom >= min_nm]
        if mom.empty:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        picks = list(mom.nlargest(int(p["top_k"])).index)
        vols = rets.loc[:d_prev].tail(vol_lb).std() * np.sqrt(12)
        inv: dict[str, float] = {}
        for sym in picks:
            v = float(vols.get(sym, np.nan))
            if not np.isfinite(v) or v <= 0:
                v = 0.25
            inv[sym] = 1.0 / max(v, 0.08)
        inv_sum = sum(inv.values())
        weights = {s: min(v / inv_sum, float(p["max_name_weight"])) for s, v in inv.items()}
        wsum = sum(weights.values())
        if wsum <= 0:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue
        weights = {s: w / wsum for s, w in weights.items()}

        port_vol = float(rets.loc[:d_prev].tail(vol_lb)[picks].mean(axis=1).std() * np.sqrt(12))
        if not np.isfinite(port_vol) or port_vol <= 0:
            port_vol = 0.2
        target = float(p["target_vol_annual"])
        lev = float(np.clip(target / port_vol, float(p["min_leverage"]), float(p["max_leverage"])))
        gross = min(float(p["gross_cap"]), lev)
        dd_soft = float(p.get("dd_soft_stop_pct") or 0)
        if dd_soft > 0 and dd_pct <= -dd_soft:
            gross *= float(p.get("dd_soft_gross_scale", 0.5))

        deploy = mtm(d_prev) * gross
        for sym, h in list(holdings.items()):
            px = row_open.get(sym, np.nan)
            if pd.notna(px):
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
                        "reason": "rebalance",
                    }
                )
        holdings.clear()
        budget = min(deploy, cash)
        for sym, w in weights.items():
            px = row_open.get(sym, np.nan)
            if not pd.notna(px) or float(px) <= 0:
                continue
            alloc = budget * w
            if alloc <= 0:
                continue
            sh = alloc / float(px)
            cash -= alloc
            holdings[sym] = _Holding(shares=sh, entry_px=float(px), entry_date=d)
        equity_rows.append({"Date": d, "equity": mtm(d)})

    if holdings:
        d_last = dates[-1]
        for sym, h in holdings.items():
            px = float(close.loc[d_last, sym])
            cash += h.shares * px
            trades.append(
                {
                    "Symbol": sym,
                    "entry_date": h.entry_date,
                    "exit_date": d_last,
                    "entry_px": h.entry_px,
                    "exit_px": px,
                    "shares": h.shares,
                    "pnl": h.shares * (px - h.entry_px),
                    "reason": "end_of_data",
                }
            )
        holdings.clear()
        equity_rows[-1]["equity"] = cash

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def summarize_regime_turn(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    ic = float((params or DEFAULT_REGIME_TURN_PARAMS)["initial_capital"])
    return summarize_backtest(trades, equity, ic)