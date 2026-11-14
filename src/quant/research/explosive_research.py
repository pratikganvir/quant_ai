"""Calendar-aligned labels, event dedup, and scanner metrics."""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd


def trading_calendar_from_panel(panel: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(pd.to_datetime(panel["Date"].unique())))


def calendar_shift(cal: pd.DatetimeIndex, dates: pd.Series, n: int) -> pd.Series:
    """Shift dates by n trading sessions on the exchange calendar (negative = look back)."""
    pos = cal.get_indexer(pd.to_datetime(dates))
    out = np.full(len(dates), np.nan, dtype="object")
    if n >= 0:
        tgt = pos + n
        ok = (pos >= 0) & (tgt < len(cal))
        out[ok] = cal[tgt[ok]].to_numpy()
    else:
        tgt = pos + n
        ok = (pos >= 0) & (tgt >= 0)
        out[ok] = cal[tgt[ok]].to_numpy()
    return pd.Series(pd.to_datetime(out), index=dates.index)


def mark_ca_suspect(panel: pd.DataFrame, gap_abs: float) -> pd.Series:
    g = panel.groupby("Symbol", observed=True)
    if "PrevClose" in panel.columns and panel["PrevClose"].notna().any():
        gap = panel["Close"] / panel["PrevClose"] - 1
    else:
        gap = g["Close"].pct_change()
    return (gap.abs() >= gap_abs).fillna(False)


def forward_excursion_calendar(
    sym_df: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    horizons: Iterable[int],
) -> pd.DataFrame:
    """MFE/MAE/ret using the next H exchange sessions (not row count)."""
    g = sym_df.sort_values("Date").copy()
    cal_pos = calendar.get_indexer(pd.to_datetime(g["Date"]))
    valid_pos = cal_pos >= 0
    g = g.loc[valid_pos].copy()
    cal_pos = cal_pos[valid_pos]
    if g.empty:
        return g

    idx_by_date = {d: i for i, d in enumerate(g["Date"].to_numpy())}
    close = g["Close"].to_numpy()
    high = g["High"].to_numpy()
    low = g["Low"].to_numpy()
    n = len(g)

    for H in horizons:
        mfe = np.full(n, np.nan)
        mae = np.full(n, np.nan)
        ret = np.full(n, np.nan)
        complete = np.zeros(n, dtype=bool)
        for i in range(n):
            c0 = close[i]
            if not np.isfinite(c0) or c0 <= 0:
                continue
            start = cal_pos[i] + 1
            end = cal_pos[i] + H
            if end >= len(calendar):
                continue
            fut_dates = calendar[start : end + 1]
            j_idx = [idx_by_date.get(d) for d in fut_dates.to_numpy()]
            if any(j is None for j in j_idx):
                continue
            j_idx = np.array(j_idx, dtype=int)
            if len(j_idx) != H:
                continue
            seg_h = high[j_idx]
            seg_l = low[j_idx]
            mfe[i] = seg_h.max() / c0 - 1
            mae[i] = seg_l.min() / c0 - 1
            ret[i] = close[j_idx[-1]] / c0 - 1
            complete[i] = True
        g[f"mfe_{H}"] = mfe
        g[f"mae_{H}"] = mae
        g[f"ret_{H}"] = ret
        g[f"horizon_complete_{H}"] = complete
    return g


def ca_invalidates_forward_window(
    sym_df: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    horizon: int,
    ca_flag: pd.Series,
) -> pd.Series:
    """True if any CA-suspect day occurs from T+1 through T+H on the exchange calendar."""
    g = sym_df.sort_values("Date").copy()
    ca = ca_flag.reindex(g.index).fillna(False)
    cal_pos = calendar.get_indexer(pd.to_datetime(g["Date"]))
    invalid = np.zeros(len(g), dtype=bool)
    date_index = pd.to_datetime(g["Date"]).to_numpy()
    ca_by_date = dict(zip(date_index, ca.to_numpy()))
    for i, pos in enumerate(cal_pos):
        if pos < 0:
            invalid[i] = True
            continue
        end = min(pos + horizon, len(calendar) - 1)
        for p in range(pos + 1, end + 1):
            d = calendar[p]
            if ca_by_date.get(d, False):
                invalid[i] = True
                break
    return pd.Series(invalid, index=g.index)


def first_touch_triple_barrier(
    sym_df: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    horizon: int,
    up: float,
    down: float,
) -> pd.Series:
    """1 if upper barrier touched before lower within H sessions; 0 if lower first; NaN incomplete."""
    g = sym_df.sort_values("Date").copy()
    cal_pos = calendar.get_indexer(pd.to_datetime(g["Date"]))
    close = g["Close"].to_numpy()
    high = g["High"].to_numpy()
    low = g["Low"].to_numpy()
    idx_by_date = {d: i for i, d in enumerate(g["Date"].to_numpy())}
    out = np.full(len(g), np.nan)

    for i in range(len(g)):
        c0 = close[i]
        if not np.isfinite(c0) or c0 <= 0 or cal_pos[i] < 0:
            continue
        start = cal_pos[i] + 1
        end = cal_pos[i] + horizon
        if end >= len(calendar):
            continue
        hit = np.nan
        for p in range(start, end + 1):
            j = idx_by_date.get(calendar[p])
            if j is None:
                hit = np.nan
                break
            up_hit = high[j] / c0 - 1 >= up
            dn_hit = low[j] / c0 - 1 <= down
            if up_hit and dn_hit:
                hit = 1.0
                break
            if up_hit:
                hit = 1.0
                break
            if dn_hit:
                hit = 0.0
                break
        if np.isnan(hit):
            # no barrier touched: treat as failure for tradable barrier study
            hit = 0.0
        out[i] = hit
    return pd.Series(out, index=g.index)


def dedupe_event_episodes(
    events: pd.DataFrame,
    *,
    symbol_col: str = "Symbol",
    date_col: str = "Date",
    mfe_col: str,
    calendar: pd.DatetimeIndex,
    gap_sessions: int = 10,
) -> pd.DataFrame:
    """Keep the first event in each symbol run of high-MFE episodes separated by gap_sessions."""
    x = events.sort_values([symbol_col, date_col]).copy()
    x["cal_pos"] = calendar.get_indexer(pd.to_datetime(x[date_col]))
    keep: list = []
    for _, g in x.groupby(symbol_col, observed=True):
        g = g.sort_values(date_col)
        last_pos = -gap_sessions - 1
        for idx, row in g.iterrows():
            cal_pos = int(row["cal_pos"])
            if cal_pos < 0:
                continue
            if cal_pos - last_pos > gap_sessions:
                keep.append(idx)
                last_pos = cal_pos
    return x.loc[keep].drop(columns="cal_pos", errors="ignore")


def daily_precision_lift(
    df: pd.DataFrame,
    *,
    date_col: str,
    score_col: str,
    y_col: str,
    k: int = 20,
) -> pd.DataFrame:
    rows = []
    for d, g in df.groupby(date_col, sort=True):
        g = g.dropna(subset=[score_col, y_col])
        if g.empty:
            continue
        top = g.nlargest(min(k, len(g)), score_col)
        base = g[y_col].mean()
        prec = top[y_col].mean()
        lift = prec / base if base > 0 else np.nan
        rows.append({
            date_col: d,
            "n_candidates": len(g),
            "n_picked": len(top),
            f"precision_at_{k}": prec,
            f"lift_at_{k}": lift,
            "any_hit": int(top[y_col].sum() > 0),
        })
    return pd.DataFrame(rows)


def purge_overlaps_calendar(
    mask: pd.Series,
    frame_dates: pd.Series,
    test_start,
    horizon: int,
    calendar: pd.DatetimeIndex,
) -> pd.Series:
    """Drop rows whose forward label window could overlap test_start (exchange sessions)."""
    test_start = pd.Timestamp(test_start)
    pos = calendar.get_indexer([test_start])[0]
    if pos < 0:
        return mask
    cutoff = calendar[max(0, pos - horizon)]
    return mask & (pd.to_datetime(frame_dates) <= cutoff)


def summarize_daily_metrics(daily: pd.DataFrame, k: int) -> dict[str, float]:
    pcol, lcol = f"precision_at_{k}", f"lift_at_{k}"
    return {
        f"mean_precision_at_{k}": float(daily[pcol].mean()),
        f"median_precision_at_{k}": float(daily[pcol].median()),
        f"mean_lift_at_{k}": float(daily[lcol].mean()),
        f"pct_days_with_hit": float(daily["any_hit"].mean()),
        "n_days": int(len(daily)),
    }