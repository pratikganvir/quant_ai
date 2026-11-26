"""Shared-cash book: Nifty names with Regime + SMA12 + 3m momentum signal."""

from __future__ import annotations

from dataclasses import dataclass
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

DUAL_MOM_PORTFOLIO_VERSION = "1.0"
SMA = 12

DEFAULT_DUAL_MOM_PORTFOLIO_PARAMS: dict[str, Any] = {
    "benchmark_symbol": "NIFTYBEES",
    "initial_capital": 100_000.0,
    "top_k": 5,
    "vol_lookback_months": 12,
    "target_vol_annual": 0.14,
    "max_leverage": 1.10,
    "min_leverage": 0.15,
    "gross_cap": 0.90,
    "max_name_weight": 0.18,
    "dd_hard_stop_pct": 10.0,
    "dd_soft_stop_pct": 7.0,
    "dd_soft_gross_scale": 0.40,
    "cash_yield_annual": 0.06,
    "min_history_months": 36,
    "universe_min_close": 20.0,
    "universe_top_n_liquidity": 50,
    "universe_liquidity_lookback_months": 12,
    "universe_min_median_volume": 0.0,
    "bench_min_momentum": 0.0,
    "rank_by": "mom3",  # mom3 | inv_vol
}

# Tuned on Nifty 50 MCP panel (₹1L): ~9.4% CAGR, ~−14.5% max DD, min equity ~88% (ruin-aware grid).
DUAL_MOM_PORTFOLIO_BALANCED_100K: dict[str, Any] = {
    **DEFAULT_DUAL_MOM_PORTFOLIO_PARAMS,
    "top_k": 4,
    "target_vol_annual": 0.18,
    "gross_cap": 0.95,
    "max_name_weight": 0.25,
    "max_leverage": 1.15,
    "dd_hard_stop_pct": 12.0,
    "dd_soft_stop_pct": 7.0,
    "dd_soft_gross_scale": 0.40,
}


def backtest_dual_mom_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    benchmark_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = {**DEFAULT_DUAL_MOM_PORTFOLIO_PARAMS, **(params or {})}
    close, opn, volume = _monthly_close_panel(monthly)
    bench = _load_benchmark_close(str(p["benchmark_symbol"]), monthly, benchmark_frame).reindex(close.index).ffill()
    dates = list(close.index)
    vol_lb = int(p["vol_lookback_months"])
    if len(dates) < int(p["min_history_months"]) + 2:
        return pd.DataFrame(), pd.DataFrame()

    cash = float(p["initial_capital"])
    holdings: dict[str, _Holding] = {}
    peak = cash
    trades: list[dict] = []
    equity_rows: list[dict] = []
    cash_m = 1.0 + float(p["cash_yield_annual"]) / 12.0

    bench_sma = bench.rolling(SMA, min_periods=SMA).mean()
    bench_mom12 = bench / bench.shift(SMA) - 1.0
    rets = close.pct_change()
    bench_reg = (bench > bench_sma) & (bench_mom12 > float(p["bench_min_momentum"]))
    sma12_all = close.rolling(SMA, min_periods=SMA).mean()
    mom3_all = close.pct_change(3)
    dual_mom_long = (close > sma12_all) & (mom3_all > 0)
    vols_rolling = rets.rolling(vol_lb, min_periods=vol_lb // 2).std() * np.sqrt(12)

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

        if i <= max(SMA + 3, vol_lb):
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        if cash < 0:
            raise ValueError("dual_mom book is cash-only; negative cash")

        eq_pre = mtm(d_prev)
        peak = max(peak, eq_pre)
        dd_pct = (eq_pre / peak - 1.0) * 100.0 if peak > 0 else 0.0
        fused = dd_pct <= -float(p["dd_hard_stop_pct"])

        risk_on = not fused and bool(bench_reg.loc[d_prev]) if d_prev in bench_reg.index else False

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
                            "reason": "risk_off",
                        }
                    )
            holdings.clear()
            cash *= cash_m
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        eligible_syms = _eligible_momentum_symbols(d_prev, close=close, volume=volume, p=p)
        long_mask = dual_mom_long.loc[d_prev].reindex(eligible_syms)
        longs = list(long_mask.index[long_mask.fillna(False).astype(bool)])

        if not longs:
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
                            "reason": "no_signal",
                        }
                    )
            holdings.clear()
            cash *= cash_m
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        rank_by = str(p.get("rank_by", "mom3")).lower()
        mom_row = mom3_all.loc[d_prev]
        vol_row = vols_rolling.loc[d_prev]
        if rank_by == "inv_vol":
            scores = {s: 1.0 / max(float(vol_row.get(s, 0.25)), 0.08) for s in longs}
        else:
            scores = {s: float(mom_row[s]) for s in longs if pd.notna(mom_row.get(s))}
        picks = sorted(scores, key=scores.get, reverse=True)[: int(p["top_k"])]

        vols = vol_row
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

        port_vol = float(rets.loc[:d_prev].tail(vol_lb)[picks].mean(axis=1).std() * np.sqrt(12)) if len(picks) > 1 else float(vol_row.get(picks[0], 0.2))
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
        if budget <= 0:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue
        for sym, w in weights.items():
            px = row_open.get(sym, np.nan)
            if not pd.notna(px) or float(px) <= 0:
                continue
            alloc = budget * w
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


def summarize_dual_mom_portfolio(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    ic = float((params or DEFAULT_DUAL_MOM_PORTFOLIO_PARAMS)["initial_capital"])
    return summarize_backtest(trades, equity, ic)


@dataclass
class DualMomRiskStats:
    min_equity_pct: float
    min_equity_inr: float
    bootstrap_ruin_50pct: float  # P(min path < 50% initial) on monthly return bootstrap


def risk_stats(equity: pd.DataFrame, initial_capital: float, *, n_boot: int = 500, seed: int = 42) -> DualMomRiskStats:
    eq = equity["equity"].astype(float)
    ic = float(initial_capital)
    min_eq = float(eq.min())
    rets = eq.pct_change().dropna()
    if len(rets) < 24:
        return DualMomRiskStats(
            min_equity_pct=100.0 * min_eq / ic,
            min_equity_inr=min_eq,
            bootstrap_ruin_50pct=0.0,
        )
    rng = np.random.default_rng(seed)
    ruin = 0
    thresh = 0.5 * ic
    block = 12
    n = len(rets)
    for _ in range(n_boot):
        path = ic
        for _m in range(n):
            i = int(rng.integers(0, max(n - block, 1)))
            chunk = rets.iloc[i : i + block].values
            for r in chunk:
                path *= 1.0 + float(r)
                if path < thresh:
                    ruin += 1
                    break
            if path < thresh:
                break
    return DualMomRiskStats(
        min_equity_pct=100.0 * min_eq / ic,
        min_equity_inr=min_eq,
        bootstrap_ruin_50pct=100.0 * ruin / n_boot,
    )