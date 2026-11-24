"""Institutional accumulation strategy (monthly bars) — indicators and backtest."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

INST_ACC_ENGINE_VERSION = "1"

DEFAULT_PARAMS: dict[str, Any] = {
    "flow_length": 6,
    "volume_length": 12,
    "obv_length": 12,
    "relative_volume_threshold": 1.2,
    "money_flow_threshold": 0.15,
    "up_volume_threshold": 0.60,
    "required_score": 4,
    "ema_length": 12,
    "use_trend_filter": True,
    "use_breakout": False,
    "breakout_length": 6,
    "stop_loss_percent": 10.0,
    "use_trend_exit": True,
    "use_accumulation_exit": True,
    "exit_score": 1,
    "use_cooldown": True,
    "cooldown_months": 3,
    "initial_capital": 100_000.0,
    "position_pct": 10.0,
    "min_monthly_bars": 24,
    "min_price": 5.0,
    "eq_only": True,
}


def strategy_params_fingerprint(params: dict | None = None) -> dict[str, Any]:
    p = {**DEFAULT_PARAMS, **(params or {})}
    return {k: p[k] for k in sorted(DEFAULT_PARAMS)}


def resample_symbol_to_monthly(g: pd.DataFrame) -> pd.DataFrame:
    """Calendar month-end OHLCV for one symbol (daily rows, sorted by Date)."""
    g = g.sort_values("Date").copy()
    g["Date"] = pd.to_datetime(g["Date"])
    g = g.set_index("Date")
    if g.empty:
        return pd.DataFrame(columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    m = g.resample("ME").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    m = m.dropna(subset=["Close"])
    m = m.reset_index().rename(columns={"index": "Date"})
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
    out = pd.concat(parts, ignore_index=True)
    return out.sort_values(["Symbol", "Date"]).reset_index(drop=True)


def _ema(s: pd.Series, length: int) -> pd.Series:
    return s.ewm(span=length, adjust=False, min_periods=length).mean()


def _sma(s: pd.Series, length: int) -> pd.Series:
    return s.rolling(length, min_periods=length).mean()


def compute_accumulation_frame(monthly: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Monthly OHLCV for one symbol → indicators, scores, buy/sell flags (no lookahead in rolls)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    df = monthly.sort_values("Date").copy()
    if df.empty:
        return df

    h, lo, cl, op, vol = df["High"], df["Low"], df["Close"], df["Open"], df["Volume"]
    rng = h - lo
    clv = np.where(rng > 0, ((cl - lo) - (h - cl)) / rng, 0.0)
    df["clv"] = clv
    mfv = clv * vol
    df["money_flow_volume"] = mfv

    avg_vol = _sma(vol, int(p["volume_length"]))
    rel_vol = np.where(avg_vol > 0, vol / avg_vol, 0.0)
    df["relative_volume"] = rel_vol
    df["signal_volume"] = (
        (rel_vol > p["relative_volume_threshold"]) & (cl > op) & (pd.Series(clv) > 0.3)
    )

    fl = int(p["flow_length"])
    avg_mf = _sma(pd.Series(mfv), fl)
    avg_fv = _sma(vol, fl)
    money_flow = np.where(avg_fv > 0, avg_mf / avg_fv, 0.0)
    df["money_flow"] = money_flow
    df["signal_money_flow"] = money_flow > p["money_flow_threshold"]

    prev_cl = cl.shift(1)
    up_vol = np.where(cl > prev_cl, vol, 0.0)
    avg_up = _sma(pd.Series(up_vol), fl)
    avg_tot = _sma(vol, fl)
    up_ratio = np.where(avg_tot > 0, avg_up / avg_tot, 0.0)
    df["up_volume_ratio"] = up_ratio
    df["signal_up_volume"] = up_ratio > p["up_volume_threshold"]

    obv_chg = np.where(cl > prev_cl, vol, np.where(cl < prev_cl, -vol, 0.0))
    obv = pd.Series(obv_chg, index=df.index).cumsum()
    obv_ema = _ema(obv, int(p["obv_length"]))
    df["obv"] = obv
    df["signal_obv"] = (obv > obv_ema) & (obv > obv.shift(1))

    adl = pd.Series(mfv, index=df.index).cumsum()
    adl_mom = adl - adl.shift(fl)
    adl_acc = adl_mom - adl_mom.shift(1)
    df["adl_momentum"] = adl_mom
    df["adl_acceleration"] = adl_acc
    df["signal_adl"] = (adl_mom > 0) & (adl_acc > 0)

    df["accumulation_score"] = (
        df["signal_volume"].astype(int)
        + df["signal_money_flow"].astype(int)
        + df["signal_up_volume"].astype(int)
        + df["signal_obv"].astype(int)
        + df["signal_adl"].astype(int)
    )

    ema = _ema(cl, int(p["ema_length"]))
    df["trend_ema"] = ema
    trend_ok = (~p["use_trend_filter"]) | ((cl > ema) & (ema > ema.shift(1)))
    df["trend_condition"] = trend_ok

    bl = int(p["breakout_length"])
    prev_high = h.shift(1).rolling(bl, min_periods=bl).max()
    brk_ok = (~p["use_breakout"]) | (cl > prev_high)
    df["breakout_condition"] = brk_ok

    df["raw_buy"] = (
        (df["accumulation_score"] >= int(p["required_score"]))
        & df["trend_condition"]
        & df["breakout_condition"]
    )

    trend_exit = p["use_trend_exit"] & (cl < ema)
    acc_exit = p["use_accumulation_exit"] & (df["accumulation_score"] <= int(p["exit_score"]))
    df["raw_sell"] = trend_exit | acc_exit

    return df


def build_trade_signals(df: pd.DataFrame, params: dict | None = None) -> pd.DataFrame:
    """Bar-aligned buy/sell flags matching the monthly backtest (stop, exit, cooldown)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    out = df.copy()
    cd = int(p["cooldown_months"])
    n = len(out)
    buy = np.zeros(n, dtype=bool)
    sell = np.zeros(n, dtype=bool)
    in_pos = False
    pending_entry = False
    last_exit: int | None = None
    stop_px = np.nan

    for i in range(n):
        row = out.iloc[i]
        opn, lo = float(row["Open"]), float(row["Low"])
        if pending_entry:
            in_pos = True
            pending_entry = False
            stop_px = opn * (1 - float(p["stop_loss_percent"]) / 100.0)

        if in_pos:
            hit = _exit_stop_price(opn, lo, stop_px)
            if np.isfinite(hit):
                sell[i] = True
                in_pos = False
                last_exit = i
            elif bool(row["raw_sell"]):
                sell[i] = True
                in_pos = False
                last_exit = i

        if not in_pos and not pending_entry and bool(row["raw_buy"]):
            cooled = (not p["use_cooldown"]) or last_exit is None or (i - last_exit) >= cd
            if cooled and i + 1 < n:
                buy[i] = True
                pending_entry = True

    out["buy_signal"] = buy
    out["sell_signal"] = sell
    return out


def compute_signals_monthly(
    monthly: pd.DataFrame,
    params: dict | None = None,
    *,
    show_progress: bool = True,
) -> pd.DataFrame:
    """Full panel monthly bars with signals."""
    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    p = {**DEFAULT_PARAMS, **(params or {})}
    parts: list[pd.DataFrame] = []
    groups = list(monthly.groupby("Symbol", observed=True))
    sym_iter = groups
    if tqdm is not None and show_progress:
        sym_iter = tqdm(groups, desc="Accumulation signals", unit="symbol")
    for sym, g in sym_iter:
        if len(g) < int(p["min_monthly_bars"]):
            continue
        if g["Close"].iloc[-1] < float(p["min_price"]):
            continue
        x = build_trade_signals(compute_accumulation_frame(g, p), p)
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


def _exit_stop_price(open_px: float, low_px: float, stop: float) -> float:
    if low_px <= stop:
        return float(open_px if open_px < stop else stop)
    return np.nan


def backtest_symbol_monthly(
    df: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Single-symbol monthly backtest (next-bar open entries/exits; intrabar stop)."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    x = build_trade_signals(compute_accumulation_frame(df, p), p)
    if x.empty:
        return pd.DataFrame(), pd.DataFrame()

    cash = float(p["initial_capital"])
    equity = cash
    pos_shares = 0.0
    entry_px = stop_px = np.nan
    trades: list[dict] = []
    equity_rows: list[dict] = []

    n = len(x)
    for i in range(n):
        row = x.iloc[i]
        opn, lo, cl = float(row["Open"]), float(row["Low"]), float(row["Close"])
        dt = row["Date"]

        if pos_shares > 0:
            hit = _exit_stop_price(opn, lo, stop_px)
            if np.isfinite(hit):
                cash += pos_shares * hit
                trades.append(
                    {
                        "entry_date": entry_dt,
                        "exit_date": dt,
                        "entry_px": entry_px,
                        "exit_px": hit,
                        "shares": pos_shares,
                        "pnl": pos_shares * (hit - entry_px),
                        "reason": "stop_loss",
                    }
                )
                pos_shares = 0.0
                entry_px = stop_px = np.nan
            elif i > 0 and bool(x.iloc[i - 1]["sell_signal"]):
                cash += pos_shares * opn
                trades.append(
                    {
                        "entry_date": entry_dt,
                        "exit_date": dt,
                        "entry_px": entry_px,
                        "exit_px": opn,
                        "shares": pos_shares,
                        "pnl": pos_shares * (opn - entry_px),
                        "reason": "signal_exit",
                    }
                )
                pos_shares = 0.0
                entry_px = stop_px = np.nan

        if pos_shares == 0 and i > 0 and bool(x.iloc[i - 1]["buy_signal"]):
            equity = cash
            alloc = equity * float(p["position_pct"]) / 100.0
            if alloc > 0 and opn > 0:
                sh = alloc / opn
                cash -= alloc
                pos_shares = sh
                entry_px = opn
                entry_dt = dt
                stop_px = entry_px * (1 - float(p["stop_loss_percent"]) / 100.0)

        mtm = cash + pos_shares * cl if pos_shares > 0 else cash
        equity_rows.append({"Date": dt, "equity": mtm})

    if pos_shares > 0:
        cl = float(x.iloc[-1]["Close"])
        cash += pos_shares * cl
        trades.append(
            {
                "entry_date": entry_dt,
                "exit_date": x.iloc[-1]["Date"],
                "entry_px": entry_px,
                "exit_px": cl,
                "shares": pos_shares,
                "pnl": pos_shares * (cl - entry_px),
                "reason": "end_of_data",
            }
        )

    return pd.DataFrame(trades), pd.DataFrame(equity_rows)


def backtest_portfolio_monthly(
    signals: pd.DataFrame,
    params: dict | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shared cash; 10% of equity per new long; one position per symbol; next-month open fills."""
    p = {**DEFAULT_PARAMS, **(params or {})}
    if signals.empty:
        return pd.DataFrame(), pd.DataFrame()

    sig = signals.sort_values(["Date", "Symbol"]).copy()
    sig["Date"] = pd.to_datetime(sig["Date"])
    months = pd.DatetimeIndex(sorted(sig["Date"].unique()))
    if len(months) < 2:
        return pd.DataFrame(), pd.DataFrame()

    by_date = {d: g for d, g in sig.groupby("Date", observed=True)}

    cash = float(p["initial_capital"])
    positions: dict[str, dict] = {}
    last_exit_idx: dict[str, int] = {}
    month_index = {pd.Timestamp(m): i for i, m in enumerate(months)}
    cd = int(p["cooldown_months"]) if p["use_cooldown"] else 0
    trades: list[dict] = []
    equity_rows: list[dict] = []

    for mi in range(1, len(months)):
        m_prev, m = months[mi - 1], months[mi]
        prev_panel = by_date.get(m_prev, pd.DataFrame())
        cur_panel = by_date.get(m, pd.DataFrame())
        if cur_panel.empty:
            continue
        cur = cur_panel.set_index("Symbol")

        for sym in list(positions):
            if sym not in cur.index:
                continue
            pos = positions[sym]
            row = cur.loc[sym]
            opn, lo, cl = float(row["Open"]), float(row["Low"]), float(row["Close"])
            stop = pos["stop"]
            hit = _exit_stop_price(opn, lo, stop)
            sell_prev = False
            if not prev_panel.empty and sym in prev_panel["Symbol"].values:
                sell_prev = bool(
                    prev_panel.loc[prev_panel["Symbol"] == sym, "sell_signal"].iloc[0]
                )
            if np.isfinite(hit):
                notional = pos["shares"] * hit
                cash += notional
                trades.append(
                    {
                        "Symbol": sym,
                        "entry_date": pos["entry_date"],
                        "exit_date": m,
                        "entry_px": pos["entry_px"],
                        "exit_px": hit,
                        "shares": pos["shares"],
                        "pnl": pos["shares"] * (hit - pos["entry_px"]),
                        "reason": "stop_loss",
                    }
                )
                del positions[sym]
                last_exit_idx[sym] = month_index[pd.Timestamp(m)]
            elif sell_prev:
                notional = pos["shares"] * opn
                cash += notional
                trades.append(
                    {
                        "Symbol": sym,
                        "entry_date": pos["entry_date"],
                        "exit_date": m,
                        "entry_px": pos["entry_px"],
                        "exit_px": opn,
                        "shares": pos["shares"],
                        "pnl": pos["shares"] * (opn - pos["entry_px"]),
                        "reason": "signal_exit",
                    }
                )
                del positions[sym]
                last_exit_idx[sym] = month_index[pd.Timestamp(m)]

        eq = cash + sum(pos["shares"] * float(cur.loc[s, "Close"]) for s, pos in positions.items() if s in cur.index)
        for _, row in prev_panel.iterrows():
            sym = row["Symbol"]
            if sym in positions:
                continue
            if not bool(row["buy_signal"]):
                continue
            if sym not in cur.index:
                continue
            li = last_exit_idx.get(sym)
            mii = month_index[pd.Timestamp(m)]
            if p["use_cooldown"] and li is not None and (mii - li) < cd:
                continue
            opn = float(cur.loc[sym, "Open"])
            if opn <= 0 or not np.isfinite(opn):
                continue
            alloc = eq * float(p["position_pct"]) / 100.0
            alloc = min(alloc, cash)
            if alloc < opn:
                continue
            sh = alloc / opn
            cash -= alloc
            positions[sym] = {
                "shares": sh,
                "entry_px": opn,
                "entry_date": m,
                "stop": opn * (1 - float(p["stop_loss_percent"]) / 100.0),
            }

        mtm = cash + sum(
            pos["shares"] * float(cur.loc[sym, "Close"])
            for sym, pos in positions.items()
            if sym in cur.index
        )
        equity_rows.append({"Date": m, "equity": mtm})

    trade_df = pd.DataFrame(trades)
    eq_df = pd.DataFrame(equity_rows)
    return trade_df, eq_df


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


def load_daily_ohlcv_panel(
    panel_path: str | None = None,
    *,
    date_start: str | None = None,
    date_end: str | None = None,
    eq_only: bool = True,
) -> pd.DataFrame:
    from pathlib import Path

    from quant.config import DATA_DIR

    path = Path(panel_path) if panel_path else DATA_DIR / "results" / "explosive_moves" / "panel" / "ohlcv_panel.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing panel {path}. Build it via strategies/explosive_moves_research.ipynb Phase 2."
        )
    cols = ["Date", "Symbol", "Open", "High", "Low", "Close", "Volume", "Series"]
    import pyarrow.parquet as pq

    have = set(pq.ParquetFile(path).schema.names)
    load_cols = [c for c in cols if c in have]
    df = pd.read_parquet(path, columns=load_cols)
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.normalize()
    if date_start:
        df = df.loc[df["Date"] >= pd.Timestamp(date_start)]
    if date_end:
        df = df.loc[df["Date"] <= pd.Timestamp(date_end)]
    if eq_only and "Series" in df.columns:
        df = df.loc[df["Series"].astype(str).str.upper().str.strip() == "EQ"]
    return df.sort_values(["Symbol", "Date"]).reset_index(drop=True)