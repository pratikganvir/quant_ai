"""Conviction Peak shared book — hold through multibagger-grade pullbacks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from quant.research.conviction_peak_exits import conviction_peak_exit, update_sma24_streak
from quant.research.conviction_peak_signals import DEFAULT_CONVICTION_PEAK_PARAMS
from quant.research.institutional_accumulation import BacktestSummary, summarize_backtest
from quant.research.launch_base_signals import build_panels, entry_mask_at, entry_score_at, load_bench, regime_on_at
from quant.research.regime_momentum_portfolio import _eligible_momentum_symbols, _monthly_close_panel


@dataclass
class _Slot:
    shares: float
    entry_px: float
    entry_date: pd.Timestamp
    peak_close: float
    months_below_sma24: int = 0


def backtest_conviction_peak_portfolio(
    monthly: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = {**DEFAULT_CONVICTION_PEAK_PARAMS, **(params or {})}
    bench = load_bench(monthly, str(p["benchmark_symbol"]))
    panels = build_panels(monthly, bench)
    close, opn, volume = _monthly_close_panel(monthly)
    opn = opn.reindex(close.index)
    volume = volume.reindex(close.index)
    dates = list(close.index)
    if len(dates) < int(p["min_history_months"]) + 2:
        return pd.DataFrame(), pd.DataFrame()

    fuse_pct = float(p["book_fuse_pct"])
    cash_m = 1.0 + float(p["cash_yield_annual"]) / 12.0
    top_k = int(p["top_k"])
    regime_new = bool(p.get("regime_blocks_new_entries_only", True))

    cash = float(p["initial_capital"])
    holdings: dict[str, _Slot] = {}
    book_peak = cash
    trades: list[dict] = []
    equity_rows: list[dict] = []

    def equity(d: pd.Timestamp) -> float:
        row = close.loc[d]
        pos = sum(h.shares * float(row[s]) for s, h in holdings.items() if s in row.index and pd.notna(row[s]))
        return cash + pos

    def sell(sym: str, h: _Slot, d: pd.Timestamp, px: float, reason: str) -> None:
        nonlocal cash
        cat = float(p.get("catastrophe_stop_pct", 30))
        if h.entry_px > 0 and reason == "catastrophe_stop":
            px = min(px, h.entry_px * (1.0 - cat / 100.0))
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
        d_sig, d_trade = dates[i - 1], dates[i]
        if i <= int(p["min_history_months"]) + 2:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        if cash > 0:
            cash *= cash_m ** (1.0 / 12.0) if holdings else cash_m

        eq = equity(d_sig)
        book_peak = max(book_peak, eq)
        dd_pct = (eq / book_peak - 1.0) * 100.0 if book_peak > 0 else 0.0
        fused = dd_pct <= -fuse_pct

        risk_on = regime_on_at(
            d_sig, bench, sma_months=int(p["bench_sma_months"]), min_mom=float(p["bench_min_momentum"])
        )

        for sym, h in list(holdings.items()):
            c = float(close.loc[d_sig, sym]) if sym in close.columns else np.nan
            low = float(panels["low"].loc[d_sig, sym]) if sym in panels["low"].columns else np.nan
            sma24 = float(panels["sma24"].loc[d_sig, sym]) if sym in panels["sma24"].columns else np.nan
            if np.isfinite(c):
                h.peak_close = max(h.peak_close, c)
            h.months_below_sma24 = update_sma24_streak(c, sma24, h.months_below_sma24)
            px = opn.loc[d_trade, sym] if sym in opn.columns else np.nan
            if not pd.notna(px):
                continue
            in_profit = np.isfinite(c) and c >= h.entry_px
            reason, h.months_below_sma24 = conviction_peak_exit(
                close=c,
                low=low,
                entry_px=h.entry_px,
                peak_close=h.peak_close,
                months_below_sma24=h.months_below_sma24,
                in_profit=in_profit,
                p=p,
                book_fused=fused,
            )
            if reason:
                sell(sym, h, d_trade, float(px), reason)

        if fused:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        if not risk_on and not regime_new:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue

        slots = top_k - len(holdings)
        if not risk_on:
            equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})
            continue
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
        budget = eq * float(p["gross_cap"])
        held = eq - cash
        headroom = max(0.0, budget - held)
        cap = float(p["max_name_weight"]) * eq
        per = min(cap, headroom / max(len(picks), 1))

        for sym in picks:
            px = opn.loc[d_trade, sym]
            if not pd.notna(px) or float(px) <= 0:
                continue
            alloc = min(per, cash)
            if alloc < 500:
                continue
            sh = alloc / float(px)
            cash -= alloc
            c0 = float(close.loc[d_sig, sym])
            holdings[sym] = _Slot(shares=sh, entry_px=float(px), entry_date=d_trade, peak_close=c0)

        equity_rows.append({"Date": d_trade, "equity": equity(d_trade)})

    if holdings:
        d_last = dates[-1]
        for sym, h in list(holdings.items()):
            sell(sym, h, d_last, float(close.loc[d_last, sym]), "end_of_data")

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def summarize_conviction_peak(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    params: dict | None = None,
) -> BacktestSummary:
    ic = float({**DEFAULT_CONVICTION_PEAK_PARAMS, **(params or {})}["initial_capital"])
    return summarize_backtest(trades, equity, ic)