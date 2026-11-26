"""Regime-filtered, vol-managed 12-1 momentum portfolio (monthly NSE).

Design (academic + practitioner mix):
- **Absolute momentum** on benchmark (default NIFTYBEES): price > SMA, 12m return > floor.
- **Relative momentum**: rank universe on classic 12-1 month return (skip last month).
- **Inverse-vol weights** among top ``top_k`` names.
- **Vol targeting**: scale gross exposure = target_vol / realized_12m cross-sectional vol (capped).
- **Drawdown fuse**: liquidate to cash when equity DD breaches ``dd_hard_stop_pct``.

This is a separate engine from institutional V4.x — aimed at book-level CAGR/DD, not TV per-symbol overlay.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant.research.institutional_accumulation import BacktestSummary, summarize_backtest

REGIME_MOMENTUM_VERSION = "1.5"

# Walk-forward validated on ~40 liquid names (train ≤2015, val 2016–19, test 2020+).
# See scripts/validate_regime_momentum.py — discrete grid only (not continuous overfit).
# Full-sample CAGR ~22% / max DD ~18%; test window CAGR often higher (~27%) — do not quote test as long-run.
VALIDATED_FULL_SAMPLE: dict[str, float] = {
    "cagr_pct": 21.9,
    "max_drawdown_pct": -17.5,
    "test_cagr_pct": 26.9,
}

DEFAULT_REGIME_MOMENTUM_PARAMS: dict[str, Any] = {
    "benchmark_symbol": "NIFTYBEES",
    "top_k": 5,
    "mom_lookback_months": 12,
    "mom_skip_months": 1,
    "bench_sma_months": 12,
    "bench_min_momentum": 0.0,
    "vol_lookback_months": 12,
    "target_vol_annual": 0.18,
    "max_leverage": 1.25,
    "min_leverage": 0.25,
    "gross_cap": 1.0,
    "max_name_weight": 0.22,
    "dd_hard_stop_pct": 12.0,
    "cash_yield_annual": 0.06,
    "initial_capital": 100_000.0,
    "min_history_months": 36,
    # Universe filter (applied before momentum rank each month; benchmark never picked)
    "universe_min_close": 20.0,
    "universe_min_median_volume": 0.0,
    "universe_top_n_liquidity": 200,
    "universe_liquidity_lookback_months": 12,
    "universe_exclude_symbols": (),
    # Cross-sectional rank: momentum (high past return) or reversal (low past return).
    "signal_rank": "momentum",
    # Dual momentum: drop names below this 12-1 return before ranking (-999 disables).
    "min_name_momentum": -999.0,
    # When book DD breaches this % (negative), scale gross by ``dd_soft_gross_scale`` (0 disables).
    "dd_soft_stop_pct": 0.0,
    "dd_soft_gross_scale": 0.5,
    # Margin: deploy may exceed cash (negative cash = borrow); monthly interest on debit balance.
    "margin_enabled": False,
    "borrow_rate_annual": 0.11,
    # Add to 12-1 momentum score when multibagger setup fires (0 = off). See ``multibagger_signals``.
    "thrust_rank_boost": 0.0,
    "thrust_ret3_boost": 0.0,
}

# **Target** ~2%/month geometric (~27% CAGR) in strong bull regimes only — not every calendar month.
# Higher vol target + gross; walk-forward validate on your full universe before sizing (`scripts/validate_regime_momentum.py`).
# For **margin** books see ``MONTH_HOLD_3X_PARAMS`` in ``month_hold_portfolio.py`` (~3× gross cap).
REGIME_MOMENTUM_TARGET_2PCT_MONTHLY_PARAMS: dict[str, Any] = {
    **DEFAULT_REGIME_MOMENTUM_PARAMS,
    "bench_min_momentum": 0.03,
    "target_vol_annual": 0.26,
    "max_leverage": 1.5,
    "min_leverage": 0.35,
    "gross_cap": 1.15,
    "top_k": 5,
    "dd_hard_stop_pct": 15.0,
    "dd_soft_stop_pct": 8.0,
    "dd_soft_gross_scale": 0.5,
}

# Nifty 50 panel (MCP monthly, 2025-11): Calmar-tilted vs default — ~19% CAGR, ~−14.5% max DD
# (`scripts/compare_improved_regime_strategies.py`, vol target ↓ + dual drawdown fuse).
REGIME_MOMENTUM_NIFTY50_BALANCED_PARAMS: dict[str, Any] = {
    **DEFAULT_REGIME_MOMENTUM_PARAMS,
    "bench_min_momentum": 0.0,
    "target_vol_annual": 0.14,
    "max_leverage": 1.15,
    "min_leverage": 0.20,
    "gross_cap": 0.95,
    "top_k": 5,
    "max_name_weight": 0.20,
    "dd_hard_stop_pct": 10.0,
    "dd_soft_stop_pct": 8.0,
    "dd_soft_gross_scale": 0.5,
    "min_name_momentum": 0.0,
}

# 3× margin book on regime_momentum (mirrors month_hold 3× gross semantics).
REGIME_MOMENTUM_3X_PARAMS: dict[str, Any] = {
    **DEFAULT_REGIME_MOMENTUM_PARAMS,
    "margin_enabled": True,
    "max_leverage": 3.0,
    "gross_cap": 3.0,
    "min_leverage": 0.5,
    "target_vol_annual": 0.30,
    "top_k": 5,
    "max_name_weight": 0.22,
    "dd_hard_stop_pct": 12.0,
    "dd_soft_stop_pct": 6.0,
    "dd_soft_gross_scale": 0.6,
    "borrow_rate_annual": 0.11,
}


def regime_momentum_params_fingerprint(params: dict | None = None) -> dict[str, Any]:
    p = {**DEFAULT_REGIME_MOMENTUM_PARAMS, **(params or {})}
    return {k: p[k] for k in sorted(DEFAULT_REGIME_MOMENTUM_PARAMS)}


def _monthly_close_panel(monthly: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    x = monthly.copy()
    x["Date"] = pd.to_datetime(x["Date"])
    need = ["Date", "Symbol", "Close", "Open"]
    for c in need:
        if c not in x.columns:
            raise ValueError(f"Monthly panel missing {c}")
    close = x.pivot(index="Date", columns="Symbol", values="Close").sort_index()
    opn = x.pivot(index="Date", columns="Symbol", values="Open").sort_index()
    if "Volume" in x.columns:
        vol = x.pivot(index="Date", columns="Symbol", values="Volume").sort_index()
    else:
        vol = close * 0.0
    return close, opn, vol


def _eligible_momentum_symbols(
    d_prev: pd.Timestamp,
    *,
    close: pd.DataFrame,
    volume: pd.DataFrame,
    p: dict,
) -> pd.Index:
    """Point-in-time liquid universe for cross-sectional momentum."""
    bench = str(p["benchmark_symbol"]).upper()
    exclude = {bench, *(str(s).upper() for s in (p.get("universe_exclude_symbols") or ()))}
    lb = int(p.get("universe_liquidity_lookback_months", 12))
    min_px = float(p.get("universe_min_close", 0) or 0)
    min_vol = float(p.get("universe_min_median_volume", 0) or 0)
    top_n = int(p.get("universe_top_n_liquidity", 0) or 0)

    px = close.loc[d_prev]
    vol_hist = volume.loc[:d_prev].tail(lb)
    med_vol = vol_hist.median()

    syms: list[str] = []
    for sym in px.dropna().index:
        sym_s = str(sym).upper()
        if sym_s in exclude:
            continue
        price = float(px[sym])
        if not np.isfinite(price) or price < min_px:
            continue
        if min_vol > 0:
            mv = float(med_vol.get(sym, np.nan))
            if not np.isfinite(mv) or mv < min_vol:
                continue
        syms.append(sym)

    if not syms:
        return pd.Index([], dtype=object)
    if top_n > 0:
        ranked = med_vol.reindex(syms).dropna().nlargest(min(top_n, len(syms))).index
        return pd.Index(ranked)
    return pd.Index(syms)


def _load_benchmark_close(
    benchmark_symbol: str,
    monthly: pd.DataFrame,
    benchmark_frame: pd.DataFrame | None = None,
) -> pd.Series:
    if benchmark_frame is not None and not benchmark_frame.empty:
        bf = benchmark_frame.copy()
        bf["Date"] = pd.to_datetime(bf["Date"])
        if "Close" in bf.columns:
            return bf.set_index("Date")["Close"].sort_index()
        if "close" in bf.columns:
            return bf.set_index("Date")["close"].sort_index()
    if benchmark_symbol in monthly["Symbol"].astype(str).unique():
        sub = monthly.loc[monthly["Symbol"].astype(str) == benchmark_symbol]
        sub = sub.sort_values("Date")
        return pd.Series(sub["Close"].values, index=pd.to_datetime(sub["Date"]))
    raise ValueError(
        f"Benchmark {benchmark_symbol!r} not in panel; pass benchmark_frame or sync NIFTYBEES."
    )


@dataclass
class _Holding:
    shares: float
    entry_px: float
    entry_date: Any


def backtest_regime_momentum_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    benchmark_frame: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared-cash book; rebalance on month *open* after prior month signal."""
    p = {**DEFAULT_REGIME_MOMENTUM_PARAMS, **(params or {})}
    close, opn, volume = _monthly_close_panel(monthly)
    bench = _load_benchmark_close(str(p["benchmark_symbol"]), monthly, benchmark_frame).reindex(close.index).ffill()
    dates = list(close.index)
    if len(dates) < int(p["min_history_months"]) + 2:
        return pd.DataFrame(), pd.DataFrame()

    mom_lb = int(p["mom_lookback_months"])
    skip = int(p["mom_skip_months"])
    sma_lb = int(p["bench_sma_months"])
    vol_lb = int(p["vol_lookback_months"])
    cash_m = 1.0 + float(p["cash_yield_annual"]) / 12.0
    borrow_m = float(p.get("borrow_rate_annual", 0.11)) / 12.0
    margin = bool(p.get("margin_enabled", False))

    cash = float(p["initial_capital"])
    holdings: dict[str, _Holding] = {}
    peak = cash
    trades: list[dict] = []
    equity_rows: list[dict] = []

    rets = close.pct_change()
    bench_sma = bench.rolling(sma_lb, min_periods=sma_lb).mean()
    bench_mom = bench / bench.shift(mom_lb) - 1.0

    thrust_setup: pd.DataFrame | None = None
    ret3_panel: pd.DataFrame | None = None
    t_boost = float(p.get("thrust_rank_boost") or 0)
    r3_boost = float(p.get("thrust_ret3_boost") or 0)
    if t_boost > 0 or r3_boost > 0:
        from quant.research.multibagger_signals import indicator_panels, precompute_setup_frame

        thrust_setup = precompute_setup_frame(monthly, str(p["benchmark_symbol"]), p)
        _, ind = indicator_panels(monthly, bench)
        ret3_panel = ind["ret_3"]

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
        row_close = close.loc[d]

        if i <= max(mom_lb + skip, vol_lb, sma_lb):
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        if margin and cash < 0:
            cash *= 1.0 + borrow_m

        eq_pre = mtm(d_prev)
        peak = max(peak, eq_pre)
        dd_pct = (eq_pre / peak - 1.0) * 100.0 if peak > 0 else 0.0
        dd_stop = float(p["dd_hard_stop_pct"])
        fused = dd_pct <= -dd_stop

        bpx = float(bench.loc[d_prev])
        risk_on = (
            not fused
            and pd.notna(bench_sma.loc[d_prev])
            and bpx >= float(bench_sma.loc[d_prev])
            and float(bench_mom.loc[d_prev]) >= float(p["bench_min_momentum"])
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
                            "reason": "risk_off",
                        }
                    )
            holdings.clear()
            cash *= cash_m
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        p1 = close.shift(skip).loc[d_prev]
        p0 = close.shift(mom_lb + skip).loc[d_prev]
        mom = (p1 / p0 - 1.0).dropna()
        if str(p.get("signal_rank", "momentum")).lower() == "reversal":
            mom = -mom
        eligible = _eligible_momentum_symbols(d_prev, close=close, volume=volume, p=p)
        if len(eligible):
            mom = mom.reindex(eligible).dropna()
        min_nm = float(p.get("min_name_momentum", -999))
        if str(p.get("signal_rank", "momentum")).lower() != "reversal":
            mom = mom.loc[mom >= min_nm]
        if mom.empty:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue

        score = mom.copy()
        if thrust_setup is not None and t_boost > 0:
            setup_row = thrust_setup.loc[d_prev].reindex(score.index)
            score = score + setup_row.fillna(False).astype(float) * t_boost
        if ret3_panel is not None and r3_boost > 0:
            r3 = ret3_panel.loc[d_prev].reindex(score.index).fillna(0).clip(lower=0, upper=0.5)
            score = score + r3 * r3_boost

        picks = list(score.nlargest(int(p["top_k"])).index)
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

        port_vol = float(rets.loc[:d_prev].tail(vol_lb).mean(axis=1).std() * np.sqrt(12))
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

        budget = deploy if margin else min(deploy, cash)
        if budget <= 0:
            equity_rows.append({"Date": d, "equity": mtm(d)})
            continue
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


def summarize_regime_momentum(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    ic = float((params or DEFAULT_REGIME_MOMENTUM_PARAMS)["initial_capital"])
    return summarize_backtest(trades, equity, ic)


def summarize_equity_window(
    equity: pd.DataFrame,
    date_start: str | pd.Timestamp,
    date_end: str | pd.Timestamp,
    initial_capital: float | None = None,
) -> BacktestSummary:
    """CAGR/DD on an equity slice (for walk-forward / hold-out tests)."""
    ic = float(initial_capital or DEFAULT_REGIME_MOMENTUM_PARAMS["initial_capital"])
    if equity.empty:
        return BacktestSummary(ic, ic, 0.0, 0.0, 0.0, 0, 0.0, False)
    eq = equity.copy()
    eq["Date"] = pd.to_datetime(eq["Date"])
    t0, t1 = pd.Timestamp(date_start), pd.Timestamp(date_end)
    w = eq.loc[(eq["Date"] >= t0) & (eq["Date"] <= t1)].sort_values("Date")
    if len(w) < 2:
        return BacktestSummary(ic, ic, 0.0, 0.0, 0.0, 0, 0.0, False)
    # Period return from first equity in window (not global initial capital).
    start_eq = float(w["equity"].iloc[0])
    scaled = w.assign(equity=w["equity"] / start_eq * ic)
    empty_trades = pd.DataFrame()
    return summarize_backtest(empty_trades, scaled, ic)


# Conservative search grid (few discrete choices — limits curve-fitting).
ROBUST_PARAM_GRID: list[dict[str, Any]] = [
    {
        "top_k": k,
        "mom_lookback_months": mom,
        "bench_sma_months": sma,
        "bench_min_momentum": bm,
        "target_vol_annual": tv,
        "max_leverage": lev,
        "gross_cap": gross,
        "dd_hard_stop_pct": dd,
    }
    for k in (5, 8)
    for mom in (9, 12)
    for sma in (10, 12)
    for bm in (0.0, 0.03, 0.05)
    for tv in (0.16, 0.18, 0.20)
    for lev in (1.25, 1.35)
    for gross in (0.9, 1.0)
    for dd in (12.0, 15.0, 100.0)
]


def evaluate_params_walkforward(
    monthly: pd.DataFrame,
    params: dict,
    *,
    train_end: str = "2015-12-31",
    val_end: str = "2019-12-31",
) -> dict[str, Any]:
    """Train / validation / hold-out metrics on one full-history backtest."""
    p = {**DEFAULT_REGIME_MOMENTUM_PARAMS, **params}
    _, equity = backtest_regime_momentum_portfolio(monthly, p)
    if equity.empty:
        return {"ok": False}
    ic = float(p["initial_capital"])
    full = summarize_regime_momentum(pd.DataFrame(), equity, p)
    dates = pd.to_datetime(equity["Date"])
    train = summarize_equity_window(equity, dates.min(), train_end, ic)
    val = summarize_equity_window(
        equity,
        pd.Timestamp(train_end) + pd.offsets.MonthBegin(1),
        val_end,
        ic,
    )
    test = summarize_equity_window(
        equity,
        pd.Timestamp(val_end) + pd.offsets.MonthBegin(1),
        dates.max(),
        ic,
    )
    stable = (
        test.cagr_pct >= 12.0
        and val.cagr_pct >= 8.0
        and test.cagr_pct >= 0.55 * max(train.cagr_pct, 1e-6)
    )
    return {
        "ok": True,
        "params": p,
        "full_cagr": full.cagr_pct,
        "full_dd": full.max_drawdown_pct,
        "train_cagr": train.cagr_pct,
        "train_dd": train.max_drawdown_pct,
        "val_cagr": val.cagr_pct,
        "val_dd": val.max_drawdown_pct,
        "test_cagr": test.cagr_pct,
        "test_dd": test.max_drawdown_pct,
        "stable": stable,
    }