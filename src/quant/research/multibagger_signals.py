"""Monthly multibagger-style entry signals (shared by thrust sleeve + hybrid rank boost)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from quant.research.regime_momentum_portfolio import _load_benchmark_close, _monthly_close_panel

DEFAULT_SIGNAL_PARAMS: dict[str, Any] = {
    "dist_hi12_min": -0.50,
    "dist_hi12_max": -0.15,
    "ret_3_min": 0.05,
    "ret_6_min": 0.0,
    "ret_12_min": -0.30,
    "ret_12_max": 0.80,
    "vol_ratio_min": 0.90,
    "use_breakout_path": True,
    "breakout_rel_mom_min": 0.08,
}


def indicator_panels(monthly: pd.DataFrame, bench: pd.Series) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    close, _opn, _vol = _monthly_close_panel(monthly)
    hi = monthly.pivot(index="Date", columns="Symbol", values="High").sort_index()
    hi = hi.reindex(close.index).reindex(close.columns, axis=1)
    ret_1 = close.pct_change()
    sma12 = close.rolling(12).mean()
    hi12 = hi.rolling(12).max()
    bench_ret12 = bench.pct_change(12).reindex(close.index)
    rel_mom = close.pct_change(12).sub(bench_ret12, axis=0)
    panels: dict[str, pd.DataFrame] = {
        "close": close,
        "ret_3": close.pct_change(3),
        "ret_6": close.pct_change(6),
        "ret_12": close.pct_change(12),
        "rel_mom_12": rel_mom,
        "vol_ratio": ret_1.rolling(6).std() / ret_1.rolling(24).std().replace(0, np.nan),
        "sma12": sma12,
        "sma24": close.rolling(24).mean(),
        "hi12": hi12,
        "dist_hi12": close / hi12 - 1.0,
        "brk12": close > hi12.shift(1),
        "above_sma12": close > sma12,
    }
    return close, panels


def setup_mask_at(
    d: pd.Timestamp,
    *,
    panels: dict[str, pd.DataFrame],
    symbols: pd.Index,
    p: dict,
) -> pd.Series:
    sp = {**DEFAULT_SIGNAL_PARAMS, **p}
    sym = symbols.intersection(panels["ret_3"].columns)
    if sym.empty:
        return pd.Series(dtype=bool)
    r3 = panels["ret_3"].loc[d, sym]
    r6 = panels["ret_6"].loc[d, sym]
    r12 = panels["ret_12"].loc[d, sym]
    dh = panels["dist_hi12"].loc[d, sym]
    vr = panels["vol_ratio"].loc[d, sym]
    thrust = (
        (r3 > float(sp["ret_3_min"]))
        & (r6 > float(sp["ret_6_min"]))
        & (r12 > float(sp["ret_12_min"]))
        & (r12 < float(sp["ret_12_max"]))
        & (dh > float(sp["dist_hi12_min"]))
        & (dh < float(sp["dist_hi12_max"]))
        & (vr > float(sp["vol_ratio_min"]))
    )
    if not bool(sp.get("use_breakout_path", True)):
        return thrust.fillna(False)
    brk = panels["brk12"].loc[d, sym]
    ab = panels["above_sma12"].loc[d, sym]
    rel = panels["rel_mom_12"].loc[d, sym]
    breakout = (
        brk.fillna(False)
        & ab.fillna(False)
        & (rel > float(sp["breakout_rel_mom_min"]))
        & (r12 < float(sp["ret_12_max"]))
        & (r12 > -0.15)
    )
    return (thrust | breakout).fillna(False)


def precompute_setup_frame(
    monthly: pd.DataFrame,
    benchmark_symbol: str = "NIFTYBEES",
    params: dict | None = None,
) -> pd.DataFrame:
    """Date × Symbol bool: multibagger-style entry setup."""
    bench = _load_benchmark_close(benchmark_symbol, monthly).sort_index()
    _close, panels = indicator_panels(monthly, bench)
    dates = panels["ret_3"].index
    syms = panels["ret_3"].columns
    out = pd.DataFrame(False, index=dates, columns=syms)
    p = params or {}
    for d in dates:
        out.loc[d] = setup_mask_at(d, panels=panels, symbols=syms, p=p).reindex(syms, fill_value=False)
    return out