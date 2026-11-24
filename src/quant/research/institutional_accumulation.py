"""Institutional Accumulation — Balanced V4.7 (monthly bars, NSE backtest)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

INST_ACC_ENGINE_VERSION = "5.0"
STRATEGY_LABEL = "Institutional Accumulation - Balanced V4.7"

DEFAULT_PARAMS: dict[str, Any] = {
    # Entry — accumulation
    "volume_length": 12,
    "relative_volume_min": 1.2,
    "up_volume_threshold": 0.55,
    "accumulation_needed": 3,
    "obv_ema_length": 10,
    "adl_lookback": 6,
    # Entry — regime
    "entry_ema_length": 12,
    "ema_slope_lookback": 6,
    "minimum_ema_slope_pct": 2.0,
    "adx_length": 14,
    "adx_smoothing": 14,
    "minimum_adx": 20.0,
    "efficiency_length": 10,
    "minimum_efficiency": 0.30,
    "breakout_length": 12,
    "use_breakout": True,
    # Risk — protective stop
    "atr_length": 14,
    "initial_stop_atr": 2.5,
    "maximum_initial_stop_percent": 20.0,
    "profit_activation_atr": 2.0,
    "profit_trail_atr": 3.5,
    "minimum_profit_atr": 1.5,
    # Exit — larger trend
    "medium_ema_length": 12,
    "slow_ema_length": 24,
    "trend_confirm_bars": 2,
    "minimum_holding_bars": 2,
    "cooldown_bars": 2,
    # Portfolio
    "initial_capital": 100_000.0,
    "position_pct": 100.0,
    "min_monthly_bars": 36,
    "min_price": 5.0,
    "eq_only": True,
    # Data quality (gaps)
    "max_missing_monthly_bars": 2,
    "max_monthly_gap_months": 2,
}

DEFAULT_SYMBOL_CONTINUITY: dict[str, Any] = {
    "enabled": True,
    "min_coverage_ratio": 0.95,
    # explosive filter uses max_gap > N; N=4 drops symbols with any 5+ session hole
    "max_internal_gap_sessions": 4,
}


def strategy_params_fingerprint(params: dict | None = None) -> dict[str, Any]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    return {k: p[k] for k in sorted(DEFAULT_PARAMS)}


def filter_daily_panel_continuity(
    panel: pd.DataFrame,
    continuity_cfg: dict | None,
    nifty_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Drop symbols with patchy daily session coverage (explosive research parity)."""
    from quant.research.explosive_research import apply_symbol_continuity_filter

    cfg = {"symbol_continuity": {**DEFAULT_SYMBOL_CONTINUITY, **(continuity_cfg or {})}}
    return apply_symbol_continuity_filter(panel, nifty_df, cfg)


def symbols_with_large_monthly_gaps(monthly: pd.DataFrame, max_gap_months: int) -> set[str]:
    """Symbols whose month-end series skips more than ``max_gap_months`` between bars."""
    if max_gap_months < 1:
        return set()
    bad: set[str] = set()
    for sym, g in monthly.groupby("Symbol", observed=True):
        d = pd.to_datetime(g["Date"], errors="coerce").dropna().sort_values()
        if len(d) < 2:
            continue
        ym = d.dt.year.astype(np.int64) * 12 + d.dt.month.astype(np.int64)
        skip = ym.diff().dropna() - 1
        if (skip > max_gap_months).any():
            bad.add(str(sym))
    return bad


def filter_monthly_panel_gaps(monthly: pd.DataFrame, max_gap_months: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Remove symbols with large calendar-month holes in the monthly panel."""
    bad = symbols_with_large_monthly_gaps(monthly, max_gap_months)
    if not bad:
        return monthly, pd.DataFrame(columns=["Symbol", "reason"])
    kept = monthly.loc[~monthly["Symbol"].astype(str).isin(bad)].copy()
    excluded = pd.DataFrame({"Symbol": sorted(bad), "reason": "monthly_gap"})
    return kept, excluded


def resample_symbol_to_monthly(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("Date").copy()
    g["Date"] = pd.to_datetime(g["Date"])
    g = g.set_index("Date")
    if g.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    m = g.resample("ME").agg(
        {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    )
    m = m.dropna(subset=["Close"]).reset_index()
    return m


def resample_panel_to_monthly(daily: pd.DataFrame) -> pd.DataFrame:
    cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in cols if c not in daily.columns]
    if missing:
        raise ValueError(f"Daily panel missing columns: {missing}")
    x = daily.copy()
    x["Date"] = pd.to_datetime(x["Date"])
    parts: list[pd.DataFrame] = []
    for sym, g in x.groupby("Symbol", observed=True):
        m = resample_symbol_to_monthly(g)
        if m.empty:
            continue
        m["Symbol"] = sym
        parts.append(m)
    if not parts:
        return pd.DataFrame(columns=cols)
    return pd.concat(parts, ignore_index=True).sort_values(["Symbol", "Date"]).reset_index(drop=True)


def _ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def _rma(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _rolling_sum(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).sum()


def _atr(h: pd.Series, lo: pd.Series, cl: pd.Series, length: int) -> pd.Series:
    prev = cl.shift(1)
    tr = pd.concat([(h - lo).abs(), (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    return _rma(tr, length)


def _dmi_adx(
    h: pd.Series, lo: pd.Series, cl: pd.Series, length: int, smooth: int
) -> tuple[pd.Series, pd.Series, pd.Series]:
    up = h - h.shift(1)
    dn = lo.shift(1) - lo
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev = cl.shift(1)
    tr = pd.concat([(h - lo).abs(), (h - prev).abs(), (lo - prev).abs()], axis=1).max(axis=1)
    tr_r = _rma(tr, length)
    plus_di = 100 * _rma(pd.Series(plus_dm, index=h.index), length) / tr_r.replace(0, np.nan)
    minus_di = 100 * _rma(pd.Series(minus_dm, index=h.index), length) / tr_r.replace(0, np.nan)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx = _rma(dx, smooth)
    return plus_di, minus_di, adx


def _series_1d(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(dtype=float)
    loc = df.columns.get_loc(col)
    if isinstance(loc, slice):
        raw = df.iloc[:, loc.start]
    elif isinstance(loc, np.ndarray):
        raw = df.iloc[:, int(loc[0])]
    else:
        raw = df.iloc[:, loc]
    return pd.to_numeric(raw, errors="coerce")


def _normalize_ohlcv_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    vol = _series_1d(df, "Volume").fillna(0.0)
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(df["Date"], errors="coerce"),
            "Open": _series_1d(df, "Open"),
            "High": _series_1d(df, "High"),
            "Low": _series_1d(df, "Low"),
            "Close": _series_1d(df, "Close"),
            "Volume": vol,
        }
    ).dropna(subset=["Date"]).reset_index(drop=True)


def _initial_stop_price(entry_px: float, entry_atr: float, p: dict) -> float:
    atr_stop = entry_px - entry_atr * float(p["initial_stop_atr"])
    pct_stop = entry_px * (1 - float(p["maximum_initial_stop_percent"]) / 100.0)
    return float(max(atr_stop, pct_stop))


def _exit_stop_price(open_px: float, low_px: float, stop: float) -> float:
    if low_px <= stop:
        return float(open_px if open_px < stop else stop)
    return np.nan


def compute_accumulation_frame(monthly: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Indicators + entry_condition (Pine V4.7, no position state)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    df = _normalize_ohlcv_frame(monthly)
    if df.empty:
        return df

    h, lo, cl = _series_1d(df, "High"), _series_1d(df, "Low"), _series_1d(df, "Close")
    vol = _series_1d(df, "Volume").fillna(0.0)
    vl = int(p["volume_length"])

    rng = h - lo
    clv = ((cl - lo) - (h - cl)) / rng
    clv = clv.where(rng > 0, 0.0)
    mfv = clv * vol

    avg_vol = vol.rolling(vl, min_periods=vl).mean()
    rel_vol = (vol / avg_vol).where(avg_vol > 0, 0.0)
    volume_condition = rel_vol >= float(p["relative_volume_min"])

    mf_sum = _rolling_sum(mfv, vl)
    money_flow_condition = (mf_sum > 0) & (clv > 0)

    prev_cl = cl.shift(1)
    up_vol = vol.where(cl > prev_cl, 0.0)
    tot_vol = _rolling_sum(vol, vl)
    up_vol_sum = _rolling_sum(up_vol, vl)
    up_ratio = (up_vol_sum / tot_vol).where(tot_vol > 0, 0.0)
    up_volume_condition = up_ratio >= float(p["up_volume_threshold"])

    obv_chg = np.sign(cl - prev_cl) * vol
    obv = obv_chg.cumsum()
    obv_avg = _ema(obv, int(p["obv_ema_length"]))
    obv_condition = obv > obv_avg

    adl = mfv.cumsum()
    adl_lb = int(p["adl_lookback"])
    adl_condition = adl > adl.shift(adl_lb)

    score = (
        volume_condition.astype(int)
        + money_flow_condition.astype(int)
        + up_volume_condition.astype(int)
        + obv_condition.astype(int)
        + adl_condition.astype(int)
    )
    accumulation_condition = score >= int(p["accumulation_needed"])

    entry_ema = _ema(cl, int(p["entry_ema_length"]))
    lb = int(p["ema_slope_lookback"])
    denom = entry_ema.shift(lb)
    ema_slope = ((entry_ema / denom - 1) * 100).where((denom > 0) & denom.notna(), 0.0)
    ema_trend_condition = (cl > entry_ema) & (ema_slope >= float(p["minimum_ema_slope_pct"]))

    plus_di, minus_di, adx = _dmi_adx(h, lo, cl, int(p["adx_length"]), int(p["adx_smoothing"]))
    adx_condition = (adx > float(p["minimum_adx"])) & (plus_di > minus_di)

    eff = int(p["efficiency_length"])
    price_change = (cl - cl.shift(eff)).abs()
    abs_chg_sum = cl.diff().abs().rolling(eff, min_periods=eff).sum()
    efficiency_ratio = (price_change / abs_chg_sum).where(abs_chg_sum > 0, 0.0)
    efficiency_condition = efficiency_ratio >= float(p["minimum_efficiency"])

    bl = int(p["breakout_length"])
    prev_high = h.shift(1).rolling(bl, min_periods=bl).max()
    breakout_condition = cl > prev_high
    breakout_filter = (~bool(p["use_breakout"])) | breakout_condition

    entry_condition = (
        accumulation_condition
        & ema_trend_condition
        & adx_condition
        & efficiency_condition
        & breakout_filter
    )

    atr_value = _atr(h, lo, cl, int(p["atr_length"]))
    medium_ema = _ema(cl, int(p["medium_ema_length"]))
    slow_ema = _ema(cl, int(p["slow_ema_length"]))
    tcb = int(p["trend_confirm_bars"])
    below_medium = (cl < medium_ema).astype(float)
    below_medium_count = below_medium.rolling(tcb, min_periods=tcb).sum()
    trend_ready = medium_ema.shift(tcb - 1).notna() & slow_ema.notna()
    confirmed_medium_breakdown = trend_ready & (below_medium_count == tcb)
    larger_trend_bearish = medium_ema < slow_ema

    df = df.assign(
        clv=clv,
        relative_volume=rel_vol,
        up_volume_ratio=up_ratio,
        accumulation_score=score,
        entry_ema=entry_ema,
        ema_slope_pct=ema_slope,
        adx=adx,
        plus_di=plus_di,
        minus_di=minus_di,
        efficiency_ratio=efficiency_ratio,
        atr=atr_value,
        medium_ema=medium_ema,
        slow_ema=slow_ema,
        below_medium_count=below_medium_count,
        entry_condition=entry_condition,
        confirmed_medium_breakdown=confirmed_medium_breakdown,
        larger_trend_bearish=larger_trend_bearish,
    )
    return df


@dataclass
class _PositionBarState:
    entry_bar: int
    entry_px: float
    entry_atr: float
    initial_stop: float
    highest_since_entry: float
    profit_trail_stop: float | None
    active_stop: float


def _update_profit_trail(
    st: _PositionBarState,
    *,
    high: float,
    atr: float,
    p: dict,
) -> None:
    if atr <= 0 or not np.isfinite(atr):
        return
    st.highest_since_entry = max(st.highest_since_entry, high)
    max_gain_atr = (st.highest_since_entry - st.entry_px) / st.entry_atr
    if max_gain_atr < float(p["profit_activation_atr"]):
        st.active_stop = st.initial_stop
        return
    trail = st.highest_since_entry - atr * float(p["profit_trail_atr"])
    lock = st.entry_px + st.entry_atr * float(p["minimum_profit_atr"])
    candidate = max(trail, lock)
    prev = st.profit_trail_stop if st.profit_trail_stop is not None else candidate
    st.profit_trail_stop = max(prev, candidate)
    st.active_stop = max(st.initial_stop, st.profit_trail_stop)


def _larger_trend_exit(row: pd.Series, *, bars_held: int, p: dict) -> bool:
    if bars_held < int(p["minimum_holding_bars"]):
        return False
    return bool(row["confirmed_medium_breakdown"] and row["larger_trend_bearish"])


def build_trade_signals(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Buy/sell flags aligned with V4.7 simulator (next-bar open fills)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    out = compute_accumulation_frame(df, p) if "entry_condition" not in df.columns else df.copy()
    n = len(out)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    cd = int(p["cooldown_bars"])
    last_exit: int | None = None
    pos: _PositionBarState | None = None
    pending_atr: float | None = None
    trend_exit_pending = False

    for i in range(n):
        row = out.iloc[i]
        opn, hi, lo = float(row["Open"]), float(row["High"]), float(row["Low"])

        if pos is not None and trend_exit_pending:
            sell[i] = True
            pos = None
            last_exit = i
            trend_exit_pending = False

        if pos is not None:
            hit = _exit_stop_price(opn, lo, pos.active_stop)
            if np.isfinite(hit):
                sell[i] = True
                pos = None
                last_exit = i
            elif i > 0:
                bars_held = i - pos.entry_bar
                if _larger_trend_exit(out.iloc[i - 1], bars_held=bars_held, p=p):
                    trend_exit_pending = True

        if pos is None and pending_atr is not None:
            entry_px = opn
            entry_atr = pending_atr if np.isfinite(pending_atr) and pending_atr > 0 else float(row["atr"])
            initial = _initial_stop_price(entry_px, entry_atr, p)
            pos = _PositionBarState(
                entry_bar=i,
                entry_px=entry_px,
                entry_atr=entry_atr,
                initial_stop=initial,
                highest_since_entry=hi,
                profit_trail_stop=None,
                active_stop=initial,
            )
            pending_atr = None

        cooled = last_exit is None or (i - last_exit) > cd
        if pos is None and pending_atr is None and cooled and bool(row["entry_condition"]):
            if i + 1 < n:
                buy[i] = True
                pending_atr = float(row["atr"]) if np.isfinite(row["atr"]) else np.nan

        if pos is not None and not (pos.entry_bar == i):
            _update_profit_trail(pos, high=hi, atr=float(row["atr"]), p=p)

    out["buy_signal"] = buy
    out["sell_signal"] = sell
    return out


def compute_signals_monthly(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    show_progress: bool = True,
) -> pd.DataFrame:
    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    p = {**DEFAULT_PARAMS, **(params or {})}
    gap_drop = symbols_with_large_monthly_gaps(monthly, int(p["max_monthly_gap_months"]))
    parts: list[pd.DataFrame] = []
    groups = list(monthly.groupby("Symbol", observed=True))
    sym_iter: Any = groups
    if tqdm is not None and show_progress:
        sym_iter = tqdm(groups, desc="V4.7 signals", unit="symbol")
    for sym, g in sym_iter:
        if str(sym) in gap_drop:
            continue
        g = _normalize_ohlcv_frame(g)
        if len(g) < int(p["min_monthly_bars"]):
            continue
        last_close = float(g["Close"].iloc[-1])
        if not np.isfinite(last_close) or last_close < float(p["min_price"]):
            continue
        x = build_trade_signals(g, p)
        x["Symbol"] = sym
        parts.append(x)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True)


@dataclass
class BacktestSummary:
    initial_capital: float
    final_equity: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    n_trades: int
    win_rate_pct: float
    profitable: bool


def _simulate_symbol_core(
    x: pd.DataFrame,
    p: dict,
    *,
    cash: float,
    alloc_pct: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades: list[dict] = []
    equity_rows: list[dict] = []
    n = len(x)
    pos: _PositionBarState | None = None
    pending_atr: float | None = None
    trend_exit_pending = False
    last_exit: int | None = None
    cd = int(p["cooldown_bars"])
    shares = 0.0
    entry_dt = None

    for i in range(n):
        row = x.iloc[i]
        opn, hi, lo, cl = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        dt = row["Date"]

        if shares > 0 and trend_exit_pending and pos is not None:
            cash += shares * opn
            trades.append(
                {
                    "entry_date": entry_dt,
                    "exit_date": dt,
                    "entry_px": pos.entry_px,
                    "exit_px": opn,
                    "shares": shares,
                    "pnl": shares * (opn - pos.entry_px),
                    "reason": "larger_trend_reversal",
                }
            )
            shares = 0.0
            pos = None
            last_exit = i
            trend_exit_pending = False

        if shares > 0 and pos is not None:
            hit = _exit_stop_price(opn, lo, pos.active_stop)
            if np.isfinite(hit):
                cash += shares * hit
                trades.append(
                    {
                        "entry_date": entry_dt,
                        "exit_date": dt,
                        "entry_px": pos.entry_px,
                        "exit_px": hit,
                        "shares": shares,
                        "pnl": shares * (hit - pos.entry_px),
                        "reason": "protective_stop",
                    }
                )
                shares = 0.0
                pos = None
                last_exit = i
            elif i > 0:
                bars_held = i - pos.entry_bar
                if _larger_trend_exit(x.iloc[i - 1], bars_held=bars_held, p=p):
                    trend_exit_pending = True

        if shares == 0 and pending_atr is not None:
            eq = cash
            alloc = min(eq * alloc_pct / 100.0, cash)
            if alloc > 0 and opn > 0:
                shares = alloc / opn
                cash -= alloc
                entry_atr = pending_atr if np.isfinite(pending_atr) and pending_atr > 0 else float(row["atr"])
                initial = _initial_stop_price(opn, entry_atr, p)
                pos = _PositionBarState(
                    entry_bar=i,
                    entry_px=opn,
                    entry_atr=entry_atr,
                    initial_stop=initial,
                    highest_since_entry=hi,
                    profit_trail_stop=None,
                    active_stop=initial,
                )
                entry_dt = dt
            pending_atr = None

        cooled = last_exit is None or (i - last_exit) > cd
        if shares == 0 and pending_atr is None and cooled and bool(row["entry_condition"]):
            if i + 1 < n:
                pending_atr = float(row["atr"]) if np.isfinite(row["atr"]) else np.nan

        if pos is not None and shares > 0 and pos.entry_bar != i:
            _update_profit_trail(pos, high=hi, atr=float(row["atr"]), p=p)

        mtm = cash + shares * cl if shares > 0 else cash
        equity_rows.append({"Date": dt, "equity": mtm})

    if shares > 0 and pos is not None:
        cl = float(x.iloc[-1]["Close"])
        cash += shares * cl
        trades.append(
            {
                "entry_date": entry_dt,
                "exit_date": x.iloc[-1]["Date"],
                "entry_px": pos.entry_px,
                "exit_px": cl,
                "shares": shares,
                "pnl": shares * (cl - pos.entry_px),
                "reason": "end_of_data",
            }
        )

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def backtest_symbol_monthly(
    df: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    x = build_trade_signals(df, p)
    if x.empty:
        return pd.DataFrame(), pd.DataFrame()
    return _simulate_symbol_core(x, p, cash=float(p["initial_capital"]), alloc_pct=float(p["position_pct"]))


@dataclass
class _PortfolioPos:
    shares: float
    entry_px: float
    entry_date: Any
    entry_bar: int
    state: _PositionBarState
    trend_exit_pending: bool = False
    missing_months: int = 0
    last_close: float = float("nan")


def backtest_portfolio_monthly(
    signals: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared cash; V4.7 stops/exits per symbol; next-month open fills."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    if signals.empty:
        return pd.DataFrame(), pd.DataFrame()

    sig = signals.sort_values(["Date", "Symbol"]).copy()
    sig["Date"] = pd.to_datetime(sig["Date"])
    months = pd.DatetimeIndex(sorted(sig["Date"].unique()))
    if len(months) < 2:
        return pd.DataFrame(), pd.DataFrame()

    by_date = {d: g for d, g in sig.groupby("Date", observed=True)}
    month_index = {pd.Timestamp(m): i for i, m in enumerate(months)}
    cd = int(p["cooldown_bars"])
    cash = float(p["initial_capital"])
    positions: dict[str, _PortfolioPos] = {}
    last_exit_idx: dict[str, int] = {}
    pending_atr: dict[str, float] = {}
    trades: list[dict] = []
    equity_rows: list[dict] = []
    max_missing = int(p["max_missing_monthly_bars"])

    for mi in range(1, len(months)):
        m_prev, m = months[mi - 1], months[mi]
        prev_panel = by_date.get(m_prev, pd.DataFrame())
        cur_panel = by_date.get(m, pd.DataFrame())
        if cur_panel.empty:
            continue
        cur = cur_panel.set_index("Symbol")
        mii = month_index[pd.Timestamp(m)]

        for sym in list(positions):
            pp = positions[sym]
            if sym not in cur.index:
                pp.missing_months += 1
                if pp.missing_months > max_missing and np.isfinite(pp.last_close) and pp.last_close > 0:
                    px = pp.last_close
                    cash += pp.shares * px
                    trades.append(
                        {
                            "Symbol": sym,
                            "entry_date": pp.entry_date,
                            "exit_date": m,
                            "entry_px": pp.entry_px,
                            "exit_px": px,
                            "shares": pp.shares,
                            "pnl": pp.shares * (px - pp.entry_px),
                            "reason": "missing_monthly_bar",
                        }
                    )
                    del positions[sym]
                    last_exit_idx[sym] = mii
                continue

            pp.missing_months = 0
            row = cur.loc[sym]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            opn, hi, lo, cl = (
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
            )
            pp.last_close = cl

            if pp.trend_exit_pending:
                cash += pp.shares * opn
                trades.append(
                    {
                        "Symbol": sym,
                        "entry_date": pp.entry_date,
                        "exit_date": m,
                        "entry_px": pp.entry_px,
                        "exit_px": opn,
                        "shares": pp.shares,
                        "pnl": pp.shares * (opn - pp.entry_px),
                        "reason": "larger_trend_reversal",
                    }
                )
                del positions[sym]
                last_exit_idx[sym] = mii
                continue

            hit = _exit_stop_price(opn, lo, pp.state.active_stop)
            if np.isfinite(hit):
                cash += pp.shares * hit
                trades.append(
                    {
                        "Symbol": sym,
                        "entry_date": pp.entry_date,
                        "exit_date": m,
                        "entry_px": pp.entry_px,
                        "exit_px": hit,
                        "shares": pp.shares,
                        "pnl": pp.shares * (hit - pp.entry_px),
                        "reason": "protective_stop",
                    }
                )
                del positions[sym]
                last_exit_idx[sym] = mii
            elif not prev_panel.empty and sym in prev_panel["Symbol"].values:
                prev_row = prev_panel.loc[prev_panel["Symbol"] == sym].iloc[0]
                bars_held = mii - pp.state.entry_bar
                if _larger_trend_exit(prev_row, bars_held=bars_held, p=p):
                    pp.trend_exit_pending = True

        for sym, patr in list(pending_atr.items()):
            if sym in positions or sym not in cur.index:
                continue
            row = cur.loc[sym]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            opn, hi = float(row["Open"]), float(row["High"])
            mtm = cash
            for s, ps in positions.items():
                if s in cur.index:
                    cr = cur.loc[s]
                    if isinstance(cr, pd.DataFrame):
                        cr = cr.iloc[0]
                    mtm += ps.shares * float(cr["Close"])
            alloc = min(mtm * float(p["position_pct"]) / 100.0, cash)
            if alloc <= 0 or opn <= 0:
                del pending_atr[sym]
                continue
            sh = alloc / opn
            cash -= alloc
            atr = patr if np.isfinite(patr) and patr > 0 else float(row["atr"])
            initial = _initial_stop_price(opn, atr, p)
            positions[sym] = _PortfolioPos(
                shares=sh,
                entry_px=opn,
                entry_date=m,
                entry_bar=mii,
                state=_PositionBarState(
                    entry_bar=mii,
                    entry_px=opn,
                    entry_atr=atr,
                    initial_stop=initial,
                    highest_since_entry=hi,
                    profit_trail_stop=None,
                    active_stop=initial,
                ),
                last_close=float(row["Close"]),
            )
            del pending_atr[sym]

        if not prev_panel.empty:
            for _, prow in prev_panel.iterrows():
                sym = prow["Symbol"]
                if sym in positions or sym in pending_atr:
                    continue
                if not bool(prow.get("buy_signal", False)):
                    continue
                li = last_exit_idx.get(sym)
                if li is not None and (mii - li) <= cd:
                    continue
                if sym not in cur.index:
                    continue
                pending_atr[sym] = float(prow["atr"]) if np.isfinite(prow.get("atr", np.nan)) else np.nan

        for sym, pp in positions.items():
            if sym not in cur.index:
                continue
            row = cur.loc[sym]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            if pp.state.entry_bar != mii:
                _update_profit_trail(pp.state, high=float(row["High"]), atr=float(row["atr"]), p=p)

        mtm = cash
        for sym, pp in positions.items():
            if sym in cur.index:
                cr = cur.loc[sym]
                if isinstance(cr, pd.DataFrame):
                    cr = cr.iloc[0]
                mtm += pp.shares * float(cr["Close"])
            elif np.isfinite(pp.last_close) and pp.last_close > 0:
                mtm += pp.shares * pp.last_close
        equity_rows.append({"Date": m, "equity": mtm})

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def summarize_backtest(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
    initial_capital: float | None = None,
) -> BacktestSummary:
    ic = float(initial_capital or DEFAULT_PARAMS["initial_capital"])
    if equity.empty:
        return BacktestSummary(ic, ic, 0.0, 0.0, 0.0, 0, 0.0, False)
    final = float(equity["equity"].iloc[-1])
    ret_pct = (final / ic - 1) * 100
    eq = equity["equity"].astype(float)
    peak = eq.cummax()
    dd = (eq / peak - 1) * 100
    max_dd = float(dd.min()) if len(dd) else 0.0

    n_trades = len(trades)
    win_rate = 0.0
    if n_trades > 0 and "pnl" in trades.columns:
        win_rate = float((trades["pnl"] > 0).mean() * 100)

    years = max((equity["Date"].iloc[-1] - equity["Date"].iloc[0]).days / 365.25, 1 / 12)
    cagr = ((final / ic) ** (1 / years) - 1) * 100 if final > 0 and ic > 0 else 0.0

    return BacktestSummary(
        initial_capital=ic,
        final_equity=final,
        total_return_pct=ret_pct,
        cagr_pct=cagr,
        max_drawdown_pct=max_dd,
        n_trades=n_trades,
        win_rate_pct=win_rate,
        profitable=final > ic,
    )


def backtest_universe_monthly(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    show_progress: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    TradingView-style: one independent backtest per valid symbol (100% equity each).
    """
    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    p = {**DEFAULT_PARAMS, **(params or {})}
    gap_drop = symbols_with_large_monthly_gaps(monthly, int(p["max_monthly_gap_months"]))
    groups = list(monthly.groupby("Symbol", observed=True))
    sym_iter: Any = groups
    if tqdm is not None and show_progress:
        sym_iter = tqdm(groups, desc="Per-symbol backtest", unit="symbol")

    ic = float(p["initial_capital"])
    stat_rows: list[dict] = []
    trade_parts: list[pd.DataFrame] = []

    for sym, g in sym_iter:
        sym_s = str(sym)
        if sym_s in gap_drop:
            continue
        g = _normalize_ohlcv_frame(g)
        if len(g) < int(p["min_monthly_bars"]):
            continue
        last_close = float(g["Close"].iloc[-1])
        if not np.isfinite(last_close) or last_close < float(p["min_price"]):
            continue
        trades, equity = backtest_symbol_monthly(g, p)
        summary = summarize_backtest(trades, equity, ic)
        stat_rows.append(
            {
                "Symbol": sym_s,
                "n_monthly_bars": len(g),
                "had_trades": len(trades) > 0,
                **{k: getattr(summary, k) for k in summary.__dataclass_fields__},
            }
        )
        if not trades.empty:
            t = trades.copy()
            t["Symbol"] = sym_s
            trade_parts.append(t)

    stats = pd.DataFrame(stat_rows)
    all_trades = pd.concat(trade_parts, ignore_index=True) if trade_parts else pd.DataFrame()
    return all_trades, stats


def symbol_monthly_debug_frame(
    monthly: pd.DataFrame,
    params: dict | None = None,
) -> pd.DataFrame:
    """Monthly OHLC + every entry/exit input (compare to TradingView data window)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    x = build_trade_signals(monthly, p)
    flag_cols = [
        "Date",
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "accumulation_score",
        "relative_volume",
        "up_volume_ratio",
        "adx",
        "efficiency_ratio",
        "ema_slope_pct",
        "entry_condition",
        "buy_signal",
        "sell_signal",
        "confirmed_medium_breakdown",
        "larger_trend_bearish",
        "atr",
    ]
    return x[[c for c in flag_cols if c in x.columns]]


def summarize_universe(stats: pd.DataFrame) -> dict[str, Any]:
    """Aggregate 'does it work?' metrics over per-symbol backtests."""
    if stats.empty:
        return {
            "n_symbols_tested": 0,
            "n_with_trades": 0,
            "pct_profitable_with_trades": 0.0,
            "pct_profitable_all_tested": 0.0,
            "median_total_return_pct": 0.0,
            "median_cagr_pct": 0.0,
            "majority_profitable_among_traders": False,
        }
    traded = stats.loc[stats["had_trades"]]
    pct_traded = 0.0
    if len(traded) > 0:
        pct_traded = float(traded["profitable"].mean() * 100)
    return {
        "n_symbols_tested": int(len(stats)),
        "n_with_trades": int(len(traded)),
        "n_without_trades": int(len(stats) - len(traded)),
        "pct_profitable_with_trades": round(pct_traded, 2),
        "pct_profitable_all_tested": round(float(stats["profitable"].mean() * 100), 2),
        "median_total_return_pct": round(float(traded["total_return_pct"].median()), 2)
        if len(traded)
        else 0.0,
        "median_cagr_pct": round(float(traded["cagr_pct"].median()), 2) if len(traded) else 0.0,
        "mean_total_return_pct": round(float(traded["total_return_pct"].mean()), 2)
        if len(traded)
        else 0.0,
        "median_max_drawdown_pct": round(float(traded["max_drawdown_pct"].median()), 2)
        if len(traded)
        else 0.0,
        "median_n_trades": round(float(traded["n_trades"].median()), 1) if len(traded) else 0.0,
        "majority_profitable_among_traders": bool(len(traded) > 0 and traded["profitable"].mean() > 0.5),
    }


def build_daily_ohlcv_panel_from_nse_cm(
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    eq_only: bool = True,
    parquet_root: Path | None = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """Stack local ``nse_cm_*.parquet`` files into a long OHLCV panel (no API)."""
    from quant.config import PARQUET_DATA_DIR
    from quant.data.tejhq import indexed_parquet_dates, nse_cm_parquet_path, parquet_candidate_paths

    root = parquet_root or PARQUET_DATA_DIR
    have = sorted(indexed_parquet_dates(root))
    start_d = pd.Timestamp(date_start).date() if date_start else None
    end_d = pd.Timestamp(date_end).date() if date_end else None
    if start_d:
        have = [d for d in have if d >= start_d]
    if end_d:
        have = [d for d in have if d <= end_d]
    if not have:
        raise FileNotFoundError(f"No nse_cm parquets under {root} for requested range.")

    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    day_iter: Iterable[date] = have
    if show_progress and tqdm is not None:
        day_iter = tqdm(have, desc="Load nse_cm parquets", unit="day")

    parts: list[pd.DataFrame] = []
    for day in day_iter:
        path = next((p for p in parquet_candidate_paths(root, day) if p.is_file()), None)
        if path is None:
            continue
        df = pd.read_parquet(path)
        if df.empty:
            continue
        df = df.rename(
            columns={
                "date": "Date",
                "symbol": "Symbol",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
                "series": "Series",
            }
        )
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
        if "Symbol" in df.columns:
            df["Symbol"] = df["Symbol"].astype(str).str.upper().str.strip()
        if eq_only and "Series" in df.columns:
            df = df.loc[df["Series"].astype(str).str.upper().str.strip() == "EQ"]
        parts.append(df)

    if not parts:
        raise FileNotFoundError(f"No rows loaded from nse_cm parquets under {root}.")
    out = pd.concat(parts, ignore_index=True)
    out = out.loc[:, ~pd.Index(out.columns).duplicated(keep="first")]
    return out.sort_values(["Symbol", "Date"]).reset_index(drop=True)


def ensure_upstox_daily_for_monthly(
    *,
    date_start: str,
    date_end: str | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
    dry_run: bool = False,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Fetch missing Upstox **daily** history and materialize ``nse_cm_*`` (CLI / other notebooks)."""
    from datetime import date as date_cls

    from quant.config import PARQUET_DATA_DIR
    from quant.data.upstox import sync_upstox_for_monthly_backtest

    start_d = pd.Timestamp(date_start).date()
    end_d = pd.Timestamp(date_end).date() if date_end else date_cls.today()
    return sync_upstox_for_monthly_backtest(
        PARQUET_DATA_DIR,
        date_start=start_d,
        date_end=end_d,
        interval="day",
        symbols=symbols,
        max_symbols=max_symbols,
        refresh_cache=refresh_cache,
        dry_run=dry_run,
        show_progress=show_progress,
    )


def build_monthly_ohlcv_panel_from_upstox(
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    symbols: Sequence[str] | None = None,
    cache_dir: Path | None = None,
    show_progress: bool = True,
) -> pd.DataFrame:
    """Load Upstox ``month`` candle symbol parquets into a long OHLCV panel (no API)."""
    from quant.config import UPSTOX_CACHE_DIR
    from quant.data.upstox import list_cached_symbols, symbol_history_path

    root = cache_dir or UPSTOX_CACHE_DIR
    syms = [s.upper() for s in symbols] if symbols else list_cached_symbols(root, interval="month")
    if not syms:
        raise FileNotFoundError(
            f"No monthly Upstox symbol cache under {root / 'symbols' / 'nse_month'}. Run Phase 0 sync."
        )

    start_d = pd.Timestamp(date_start).date() if date_start else None
    end_d = pd.Timestamp(date_end).date() if date_end else None

    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    sym_iter: Iterable[str] = syms
    if show_progress and tqdm is not None:
        sym_iter = tqdm(syms, desc="Load Upstox monthly cache", unit="symbol")

    parts: list[pd.DataFrame] = []
    cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume"]
    for sym in sym_iter:
        path = symbol_history_path(root, sym, interval="month")
        if not path.is_file():
            continue
        df = pd.read_parquet(path)
        if df.empty:
            continue
        df = df.rename(
            columns={
                "date": "Date",
                "symbol": "Symbol",
                "open": "Open",
                "high": "High",
                "low": "Low",
                "close": "Close",
                "volume": "Volume",
            }
        )
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
        df["Symbol"] = sym
        if start_d is not None:
            df = df.loc[df["Date"].dt.date >= start_d]
        if end_d is not None:
            df = df.loc[df["Date"].dt.date <= end_d]
        if df.empty:
            continue
        parts.append(df[cols])

    if not parts:
        raise FileNotFoundError("No monthly rows in range from Upstox symbol cache.")
    return pd.concat(parts, ignore_index=True).sort_values(["Symbol", "Date"]).reset_index(drop=True)


def ensure_upstox_monthly_for_monthly(
    *,
    date_start: str,
    date_end: str | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
    dry_run: bool = False,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Fetch only missing Upstox **month** candles (max ~10y window per API call)."""
    from datetime import date as date_cls

    from quant.config import PARQUET_DATA_DIR
    from quant.data.upstox import sync_upstox_for_monthly_backtest

    start_d = pd.Timestamp(date_start).date()
    end_d = pd.Timestamp(date_end).date() if date_end else date_cls.today()
    return sync_upstox_for_monthly_backtest(
        PARQUET_DATA_DIR,
        date_start=start_d,
        date_end=end_d,
        interval="month",
        symbols=symbols,
        max_symbols=max_symbols,
        refresh_cache=refresh_cache,
        dry_run=dry_run,
        show_progress=show_progress,
    )


def load_daily_ohlcv_panel(
    panel_path: str | None = None,
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    eq_only: bool = True,
    from_nse_cm: bool = False,
    parquet_root: Path | None = None,
) -> pd.DataFrame:
    from pathlib import Path

    from quant.config import DATA_DIR

    path = Path(panel_path) if panel_path else None
    if from_nse_cm:
        return build_daily_ohlcv_panel_from_nse_cm(
            date_start=date_start,
            date_end=date_end,
            eq_only=eq_only,
            parquet_root=parquet_root,
        )
    if path is None:
        path = DATA_DIR / "results" / "explosive_moves" / "panel" / "ohlcv_panel.parquet"
    if not path.exists():
        try:
            return build_daily_ohlcv_panel_from_nse_cm(
                date_start=date_start,
                date_end=date_end,
                eq_only=eq_only,
                parquet_root=parquet_root,
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Missing panel {path}. Run Phase 0 Upstox sync or build via explosive_moves Phase 2."
            ) from None
    cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume", "Series"]
    import pyarrow.parquet as pq

    have = set(pq.ParquetFile(path).schema.names)
    load_cols = [c for c in cols if c in have]
    df = pd.read_parquet(path, columns=load_cols)
    df = df.loc[:, ~pd.Index(df.columns).duplicated(keep="first")]
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    if date_start:
        df = df.loc[df["Date"] >= pd.Timestamp(date_start)]
    if date_end:
        df = df.loc[df["Date"] <= pd.Timestamp(date_end)]
    if eq_only and "Series" in df.columns:
        df = df.loc[df["Series"].astype(str).str.upper().str.strip() == "EQ"]
    return df.sort_values(["Symbol", "Date"]).reset_index(drop=True)