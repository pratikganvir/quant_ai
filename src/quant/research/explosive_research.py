"""Calendar-aligned labels, event dedup, and scanner metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

LABEL_ENGINE_VERSION = "3"  # bump when label/feature numerics change (invalidates caches)

LABEL_CFG_KEYS = (
    "horizons",
    "h_max",
    "primary_horizon",
    "primary_mfe_thresh",
    "ca_gap_abs_exclude",
    "triple_barrier",
    "extra_thresholds",
    "sustained_retention",
    "event_thresholds",
)


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


def _symbol_calendar_row_map(cal_pos: np.ndarray, n_cal: int) -> np.ndarray:
    """sym_row_at_cal[p] = row index in symbol frame for exchange session p, else -1."""
    sym_row_at_cal = np.full(n_cal, -1, dtype=np.int32)
    ok = cal_pos >= 0
    sym_row_at_cal[cal_pos[ok]] = np.nonzero(ok)[0].astype(np.int32)
    return sym_row_at_cal


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

    sym_row_at_cal = _symbol_calendar_row_map(cal_pos, len(calendar))
    close = g["Close"].to_numpy(dtype=np.float64)
    high = g["High"].to_numpy(dtype=np.float64)
    low = g["Low"].to_numpy(dtype=np.float64)
    n = len(g)
    n_cal = len(calendar)

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
            if end >= n_cal:
                continue
            j_idx = sym_row_at_cal[start : end + 1]
            if j_idx.shape[0] != H or (j_idx < 0).any():
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
    ca = ca_flag.reindex(g.index).fillna(False).to_numpy(dtype=bool)
    cal_pos = calendar.get_indexer(pd.to_datetime(g["Date"]))
    n_cal = len(calendar)
    ca_on_cal = np.zeros(n_cal, dtype=bool)
    ok = cal_pos >= 0
    ca_on_cal[cal_pos[ok]] |= ca[ok]
    invalid = np.zeros(len(g), dtype=bool)
    for i, pos in enumerate(cal_pos):
        if pos < 0:
            invalid[i] = True
            continue
        end = min(pos + horizon, n_cal - 1)
        if ca_on_cal[pos + 1 : end + 1].any():
            invalid[i] = True
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
    sym_row_at_cal = _symbol_calendar_row_map(cal_pos, len(calendar))
    out = np.full(len(g), np.nan)
    n_cal = len(calendar)

    for i in range(len(g)):
        c0 = close[i]
        if not np.isfinite(c0) or c0 <= 0 or cal_pos[i] < 0:
            continue
        start = cal_pos[i] + 1
        end = cal_pos[i] + horizon
        if end >= n_cal:
            continue
        j_idx = sym_row_at_cal[start : end + 1]
        if j_idx.shape[0] != horizon or (j_idx < 0).any():
            continue
        hit = np.nan
        for j in j_idx:
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
        out[i] = 0.0 if np.isnan(hit) else hit
    return pd.Series(out, index=g.index)


def label_symbol_panel(
    sym_df: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    horizons: Iterable[int],
    *,
    ca_gap_abs: float,
    tb_up: float,
    tb_down: float,
) -> pd.DataFrame:
    """One symbol: forward labels, CA flags, triple-barrier (for parallel workers)."""
    g = forward_excursion_calendar(sym_df, calendar, horizons)
    ca = mark_ca_suspect(g, ca_gap_abs)
    g["ca_suspect"] = ca.values
    for H in horizons:
        g[f"ca_fwd_invalid_{H}"] = ca_invalidates_forward_window(g, calendar, H, ca).values
        ok = g[f"horizon_complete_{H}"] & ~g[f"ca_fwd_invalid_{H}"]
        g[f"y_tb_{H}"] = first_touch_triple_barrier(g, calendar, H, tb_up, tb_down).where(ok, np.nan)
    return g


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
        cal_arr = g["cal_pos"].to_numpy(dtype=np.int64)
        for idx, cal_pos in zip(g.index, cal_arr):
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


def fingerprint_dict(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def file_fingerprint(path: Path) -> str:
    if not path.exists():
        return ""
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def read_meta_fingerprint(meta_path: Path) -> str:
    if not meta_path.exists():
        return ""
    try:
        return json.loads(meta_path.read_text()).get("fingerprint", "")
    except json.JSONDecodeError:
        return ""


def label_cfg_fingerprint(cfg: dict) -> str:
    base = {k: cfg[k] for k in LABEL_CFG_KEYS if k in cfg}
    base["engine"] = LABEL_ENGINE_VERSION
    return fingerprint_dict(base)


def force_rerun_phase(cfg: dict, phase: str) -> bool:
    if cfg.get("rerun_all"):
        return True
    fr = cfg.get("force_rerun") or []
    if "all" in fr:
        return True
    return phase in fr


def write_phase_meta(meta_path: Path, phase: str, fingerprint: str, **extra) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"phase": phase, "fingerprint": fingerprint, **extra}
    meta_path.write_text(json.dumps(payload, indent=2, default=str))


def phase_cache_hit(
    cfg: dict,
    phase: str,
    artifacts: list[Path],
    meta_path: Path,
    fingerprint: str,
) -> bool:
    if force_rerun_phase(cfg, phase):
        print(f"[{phase}] force rerun requested")
        return False
    if not all(p.exists() for p in artifacts):
        return False
    if not meta_path.exists():
        return False
    try:
        meta = json.loads(meta_path.read_text())
    except json.JSONDecodeError:
        return False
    if meta.get("fingerprint") != fingerprint:
        return False
    print(f"[{phase}] using cached artifacts ({len(artifacts)} file(s))")
    return True


def summarize_daily_metrics(daily: pd.DataFrame, k: int) -> dict[str, float]:
    pcol, lcol = f"precision_at_{k}", f"lift_at_{k}"
    return {
        f"mean_precision_at_{k}": float(daily[pcol].mean()),
        f"median_precision_at_{k}": float(daily[pcol].median()),
        f"mean_lift_at_{k}": float(daily[lcol].mean()),
        f"pct_days_with_hit": float(daily["any_hit"].mean()),
        "n_days": int(len(daily)),
    }