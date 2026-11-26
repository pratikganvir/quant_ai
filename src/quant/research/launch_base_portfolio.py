"""Launch Base — one shared book: multibagger entries, stock-level exits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant.research.institutional_accumulation import BacktestSummary, summarize_backtest
from quant.research.launch_base_signals import (
    DEFAULT_LAUNCH_BASE_PARAMS,
    build_panels,
    entry_mask_at,
    entry_score_at,
    load_bench,
    regime_on_at,
)
from quant.research.launch_base_exits import launch_exit_reason
from quant.research.multibagger_exits import trend_sma_key
from quant.research.regime_momentum_portfolio import (
    _eligible_momentum_symbols,
    _monthly_close_panel,
)


@dataclass
class _Slot:
    shares: float
    entry_px: float
    entry_date: pd.Timestamp
    peak_close: float
    months_held: int = 0
    months_below_sma: int = 0
    months_below_sma12: int = 0


def backtest_launch_base_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = {**DEFAULT_LAUNCH_BASE_PARAMS, **(params or {})}
    bench = load_bench(monthly, str(p["benchmark_symbol"]))
    panels = build_panels(monthly, bench)
    close, opn, volume = _monthly_close_panel(monthly)
    opn = opn.reindex(close.index)
    volume = volume.reindex(close.index)
    dates = list(close.index)
    min_hist = int(p["min_history_months"])
    if len(dates) < min_hist + 2:
        return pd.DataFrame(), pd.DataFrame()

    top_k = int(p["top_k"])
    cash_m = 1.0 + float(p["cash_yield_annual"]) / 12.0
    trend_key = trend_sma_key(int(p["trend_exit_sma_months"]))
    regime_new_only = bool(p.get("regime_blocks_new_entries_only", True))

    cash = float(p["initial_capital"])
    holdings: dict[str, _Slot] = {}
    book_peak = cash
    trades: list[dict] = []
    equity_rows: list[dict] = []

    def invested(d: pd.Timestamp) -> float:
        row = close.loc[d]
        return sum(h.shares * float(row[s]) for s, h in holdings.items() if s in row.index and pd.notna(row[s]))

    def equity(d: pd.Timestamp) -> float:
        return cash + invested(d)

    def sell(sym: str, h: _Slot, d: pd.Timestamp, px: float, reason: str) -> None:
        nonlocal cash
        stop_pct = float(p.get("entry_stop_pct", 20))
        if h.entry_px > 0:
            floor_px = h.entry_px * (1.0 - stop_pct / 100.0)
            if reason == "profit_floor":
                px = h.entry_px * (1.0 + float(p.get("profit_floor_pct", 2)) / 100.0)
            elif float(px) < floor_px:
                px = floor_px
        cash += h.shares * px
        trades.append(
            {
                "Symbol": sym,
                "entry_date": h.entry_date,
                "exit_date": d,
                "entry_px": h.entry_px,
                "exit_px": px,
                "shares": h.shares,
                "pnl": h.shares * (px - h.entry_px),
                "reason": reason,
            }
        )
        del holdings[sym]

    for i in range(1, len(dates)):
        d_sig, d_trade = dates[i - 1], dates[i]
        row_open = opn.loc[d_trade]

        if i <= min_hist + 2:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        if cash > 0:
            cash *= cash_m ** (1.0 / 12.0) if holdings else cash_m

        eq = equity(d_sig)
        book_peak = max(book_peak, eq)
        dd_pct = (eq / book_peak - 1.0) * 100.0 if book_peak > 0 else 0.0
        fused = dd_pct <= -float(p["dd_hard_stop_pct"])

        risk_on = regime_on_at(
            d_sig,
            bench,
            sma_months=int(p["bench_sma_months"]),
            min_mom=float(p["bench_min_momentum"]),
        )

        for sym, h in list(holdings.items()):
            h.months_held += 1
            c = float(close.loc[d_sig, sym]) if sym in close.columns else np.nan
            if np.isfinite(c):
                h.peak_close = max(h.peak_close, c)
            px = row_open.get(sym, np.nan)
            if not pd.notna(px):
                continue
            low = float(panels["low"].loc[d_sig, sym]) if sym in panels["low"].columns else np.nan
            sma24 = panels["sma24"].loc[d_sig, sym] if sym in panels["sma24"].columns else np.nan
            sma12 = panels["sma12"].loc[d_sig, sym] if sym in panels["sma12"].columns else np.nan
            reason, h.months_below_sma, h.months_below_sma12 = launch_exit_reason(
                close=c,
                low=low,
                entry_px=h.entry_px,
                peak_close=h.peak_close,
                months_below_sma=h.months_below_sma,
                months_below_sma12=h.months_below_sma12,
                sma24=float(sma24) if pd.notna(sma24) else np.nan,
                sma12=float(sma12) if pd.notna(sma12) else np.nan,
                months_held=h.months_held,
                p=p,
                book_fused=fused,
            )
            if reason:
                tag = "book_fuse" if reason == "book_fuse" else reason
                sell(sym, h, d_trade, float(px), tag)

        if fused:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        if not risk_on:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        slots = top_k - len(holdings)
        if slots <= 0:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        eligible = _eligible_momentum_symbols(d_sig, close=close, volume=volume, p=p)
        eligible = eligible.difference(list(holdings.keys()))
        mask = entry_mask_at(d_sig, panels, eligible, p)
        cands = mask.loc[mask].index
        if cands.empty:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        scores = entry_score_at(d_sig, panels, cands)
        floor = float(p.get("min_entry_score") or 0)
        if floor > 0:
            scores = scores.loc[scores >= floor]
        if scores.empty:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue
        picks = list(scores.nlargest(slots).index)
        eq = equity(d_sig)
        gross = float(p["gross_cap"])
        dd_soft = float(p.get("dd_soft_stop_pct") or 0)
        if dd_soft > 0 and dd_pct <= -dd_soft:
            gross *= float(p.get("dd_soft_gross_scale", 0.55))
        budget = eq * gross
        held = invested(d_sig)
        headroom = max(0.0, budget - held)
        cap = float(p["max_name_weight"]) * eq
        per = min(cap, headroom / max(len(picks), 1))

        for sym in picks:
            px = row_open.get(sym, np.nan)
            if not pd.notna(px) or float(px) <= 0:
                continue
            alloc = min(per, cash)
            if alloc < 500:
                continue
            sh = alloc / float(px)
            cash -= alloc
            c0 = float(close.loc[d_sig, sym])
            holdings[sym] = _Slot(
                shares=sh,
                entry_px=float(px),
                entry_date=d_trade,
                peak_close=c0,
            )

        equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})

    if holdings:
        d_last = dates[-1]
        for sym, h in list(holdings.items()):
            sell(sym, h, d_last, float(close.loc[d_last, sym]), "end_of_data")

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def summarize_launch_base(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    ic = float({**DEFAULT_LAUNCH_BASE_PARAMS, **(params or {})}["initial_capital"])
    return summarize_backtest(trades, equity, ic)