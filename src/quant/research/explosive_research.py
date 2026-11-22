"""Calendar-aligned labels, event dedup, and scanner metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

LABEL_ENGINE_VERSION = "5"  # bump when label/feature numerics change (invalidates caches)
SIM_ENGINE_VERSION = "4"  # symbol-normalized positions; no duplicate entry while held

LABEL_CFG_KEYS = (
    "horizons",
    "h_max",
    "primary_horizon",
    "primary_mfe_thresh",
    "ca_gap_abs_exclude",
    "triple_barrier",
)


def explosive_mfe_threshold(cfg: dict) -> float:
    """Minimum forward MFE (fraction) that defines a positive explosive label."""
    return float(cfg["primary_mfe_thresh"])


def trading_calendar_from_panel(panel: pd.DataFrame) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(sorted(pd.to_datetime(panel["Date"].unique())))


MISSING_BAR_CLASS_LABELS = {
    "A": "exchange_holiday",
    "B": "weekend",
    "C": "valid_session_no_stock_bar",
    "D": "suspension",
    "E": "illiquid_no_trade",
    "F": "symbol_change_or_discontinued",
    "G": "data_ingestion_failure",
    "H": "unknown",
}


def build_exchange_session_registry(
    panel: pd.DataFrame,
    nifty_df: pd.DataFrame | None = None,
    *,
    breadth_frac: float = 0.35,
) -> pd.DataFrame:
    """Date-level market registry from panel union; NIFTY is the authoritative session anchor."""
    panel = panel.copy()
    panel["Date"] = pd.to_datetime(panel["Date"])
    by_date = panel.groupby("Date", observed=True).agg(
        n_symbols=("Symbol", "nunique"),
        n_volume_positive=("Volume", lambda s: int((pd.to_numeric(s, errors="coerce") > 0).sum())),
    )
    panel_cal = pd.DatetimeIndex(sorted(panel["Date"].unique()))
    nifty_dates: set[pd.Timestamp] = set()
    if nifty_df is not None and not nifty_df.empty:
        nifty_dates = set(pd.to_datetime(nifty_df["Date"].unique()))
    med_syms = float(by_date["n_symbols"].median()) if not by_date.empty else 0.0
    breadth_floor = max(50.0, med_syms * breadth_frac)
    rows: list[dict] = []
    for d in panel_cal:
        d = pd.Timestamp(d)
        stats = by_date.loc[d] if d in by_date.index else None
        n_sym = int(stats["n_symbols"]) if stats is not None else 0
        n_vol = int(stats["n_volume_positive"]) if stats is not None else 0
        is_weekend = d.weekday() >= 5
        has_nifty = d in nifty_dates
        is_broad = n_sym >= breadth_floor
        # Valid NSE cash session: benchmark index printed a bar, or panel breadth is normal on a weekday.
        is_valid = (not is_weekend) and (has_nifty or is_broad)
        if is_weekend:
            session_type = "weekend"
        elif has_nifty and is_broad:
            session_type = "regular"
        elif has_nifty and not is_broad:
            session_type = "muhurat_or_partial"
        elif (not has_nifty) and is_broad:
            session_type = "panel_wide_no_nifty"
        elif (not has_nifty) and n_sym > 0:
            session_type = "thin_panel"
        else:
            session_type = "ghost_date"
        rows.append(
            {
                "Date": d,
                "weekday": d.weekday(),
                "n_panel_symbols": n_sym,
                "n_volume_positive": n_vol,
                "nifty_session": has_nifty,
                "is_broad_panel": is_broad,
                "is_valid_session": is_valid,
                "session_type": session_type,
            }
        )
    return pd.DataFrame(rows).sort_values("Date").reset_index(drop=True)


def refined_trading_calendar(registry: pd.DataFrame) -> pd.DatetimeIndex:
    """Authoritative session index: NIFTY-backed when available, else breadth-filtered weekdays."""
    ok = registry.loc[registry["is_valid_session"], "Date"]
    return pd.DatetimeIndex(pd.to_datetime(ok).sort_values())


def classify_calendar_gap(registry: pd.DataFrame, d: pd.Timestamp) -> str:
    """Classify a panel-calendar date that is not a validated exchange session."""
    d = pd.Timestamp(d)
    hit = registry.loc[registry["Date"] == d]
    if hit.empty:
        return "H"
    row = hit.iloc[0]
    if row["weekday"] >= 5:
        return "B"
    if not row["nifty_session"] and not row["is_broad_panel"]:
        return "A"
    if not row["nifty_session"] and row["n_panel_symbols"] < 50:
        return "G"
    return "H"


def classify_stock_absence(
    symbol: str,
    d: pd.Timestamp,
    panel: pd.DataFrame,
    registry: pd.DataFrame,
    *,
    suspension_gap_sessions: int = 5,
) -> str:
    """Classify a missing symbol bar on a validated market session."""
    d = pd.Timestamp(d)
    reg = registry.loc[registry["Date"] == d]
    if not reg.empty and not bool(reg.iloc[0]["is_valid_session"]):
        return classify_calendar_gap(registry, d)
    sym = panel.loc[panel["Symbol"].astype(str) == str(symbol)].copy()
    if sym.empty:
        return "F"
    sym["Date"] = pd.to_datetime(sym["Date"])
    sym = sym.sort_values("Date")
    if d in set(sym["Date"]):
        return "C"
    prior_rows = sym.loc[sym["Date"] < d]
    future_rows = sym.loc[sym["Date"] > d]
    if prior_rows.empty:
        return "C"
    if future_rows.empty:
        return "F"
    prior = pd.Timestamp(prior_rows["Date"].iloc[-1])
    future = pd.Timestamp(future_rows["Date"].iloc[0])
    gap_reg = registry.loc[
        (registry["Date"] > prior)
        & (registry["Date"] < future)
        & registry["is_valid_session"]
    ]
    if len(gap_reg) >= suspension_gap_sessions:
        return "D"
    vol = pd.to_numeric(prior_rows["Volume"].iloc[-1], errors="coerce")
    if not np.isfinite(vol) or vol <= 0:
        return "E"
    return "C"


def symbol_internal_session_gaps(
    panel: pd.DataFrame,
    ref_cal: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Missing reference sessions between two consecutive bars for each symbol."""
    ref = pd.DatetimeIndex(pd.to_datetime(ref_cal))
    panel = panel.copy()
    panel["Date"] = pd.to_datetime(panel["Date"])
    rows: list[dict] = []
    for sym, g in panel.groupby("Symbol", observed=True):
        dts = g["Date"].drop_duplicates().sort_values()
        pos = ref.get_indexer(dts)
        pos = pos[pos >= 0]
        if len(pos) < 2:
            continue
        pos = np.sort(pos)
        for i in range(len(pos) - 1):
            missing = int(pos[i + 1] - pos[i] - 1)
            if missing <= 0:
                continue
            gap_start = ref[pos[i]]
            gap_end = ref[pos[i + 1]]
            missing_dates = list(ref[pos[i] + 1 : pos[i + 1]])
            rows.append(
                {
                    "Symbol": str(sym),
                    "bar_before": gap_start,
                    "bar_after": gap_end,
                    "missing_sessions": missing,
                    "first_missing_session": missing_dates[0],
                    "last_missing_session": missing_dates[-1],
                }
            )
    return pd.DataFrame(rows)


def symbol_continuity_table(
    panel: pd.DataFrame,
    ref_cal: pd.DatetimeIndex,
    session_gaps: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per-symbol coverage on the reference calendar over each symbol's first→last bar."""
    ref = pd.DatetimeIndex(pd.to_datetime(ref_cal))
    panel = panel.copy()
    panel["Date"] = pd.to_datetime(panel["Date"])
    if session_gaps is None:
        session_gaps = symbol_internal_session_gaps(panel, ref)
    gap_agg = pd.DataFrame()
    if not session_gaps.empty:
        gap_agg = session_gaps.groupby("Symbol", observed=True)["missing_sessions"].agg(
            max_gap="max",
            total_missing="sum",
            n_gaps="size",
        )
        gap_agg.index = gap_agg.index.astype(str)
    rows: list[dict] = []
    for sym, g in panel.groupby("Symbol", observed=True):
        dts = g["Date"].drop_duplicates().sort_values()
        d0, d1 = pd.Timestamp(dts.min()), pd.Timestamp(dts.max())
        i0, i1 = ref.get_indexer([d0, d1])
        expected = int(i1 - i0 + 1) if i0 >= 0 and i1 >= 0 else 0
        n_bars = int(len(dts))
        coverage = (n_bars / expected) if expected > 0 else np.nan
        sym_s = str(sym)
        row = {
            "Symbol": sym_s,
            "n_bars": n_bars,
            "first_date": d0,
            "last_date": d1,
            "expected_sessions": expected,
            "coverage_ratio": coverage,
            "max_gap": 0,
            "total_missing_sessions": 0,
            "n_gap_episodes": 0,
        }
        if not gap_agg.empty and sym_s in gap_agg.index:
            row["max_gap"] = int(gap_agg.loc[sym_s, "max_gap"])
            row["total_missing_sessions"] = int(gap_agg.loc[sym_s, "total_missing"])
            row["n_gap_episodes"] = int(gap_agg.loc[sym_s, "n_gaps"])
        rows.append(row)
    return pd.DataFrame(rows)


def apply_symbol_continuity_filter(
    panel: pd.DataFrame,
    nifty_df: pd.DataFrame | None,
    cfg: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Drop symbols that miss too many reference sessions while ostensibly listed.

    Perfect session coverage (1.0) is unrealistic on NSE vendor data; defaults aim for
    names tradable on (almost) every exchange session in their active window.
    """
    sc = cfg.get("symbol_continuity") or {}
    if not sc.get("enabled", True):
        return panel, pd.DataFrame()
    min_cov = float(sc.get("min_coverage_ratio", 0.95))
    max_gap = int(sc.get("max_internal_gap_sessions", 20))
    registry = build_exchange_session_registry(panel, nifty_df)
    ref = refined_trading_calendar(registry)
    if len(ref) == 0:
        ref = pd.DatetimeIndex(sorted(pd.to_datetime(panel["Date"].unique())))
    gaps = symbol_internal_session_gaps(panel, ref)
    cov = symbol_continuity_table(panel, ref, gaps)
    if cov.empty:
        return panel, cov
    bad = (cov["coverage_ratio"] < min_cov) | (cov["max_gap"] > max_gap)
    bad_syms = set(cov.loc[bad, "Symbol"].astype(str))
    kept = panel.loc[~panel["Symbol"].astype(str).isin(bad_syms)].copy()
    excluded = cov.loc[cov["Symbol"].astype(str).isin(bad_syms)].copy()
    excluded["excluded"] = True
    return kept, excluded


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


FEATURE_COL_PREFIXES = ("vol_", "vola_", "px_", "deliv_", "liq_", "rs_", "mkt_")


def feature_column_names(columns: Iterable[str]) -> list[str]:
    return [c for c in columns if c.startswith(FEATURE_COL_PREFIXES)]


def load_feature_dataset_artifacts(
    features_path: Path,
    panel_features_path: Path | None = None,
) -> dict[str, pd.DataFrame | list[str]]:
    """Load Phase 5 parquet outputs (resume / out-of-order notebook execution)."""
    if not features_path.exists():
        raise FileNotFoundError(f"Missing features parquet: {features_path}")
    features = pd.read_parquet(features_path)
    features["Date"] = pd.to_datetime(features["Date"])
    panel_features = None
    if panel_features_path is not None and panel_features_path.exists():
        panel_features = pd.read_parquet(panel_features_path)
        panel_features["Date"] = pd.to_datetime(panel_features["Date"])
    src_cols = panel_features.columns if panel_features is not None else features.columns
    feature_cols = feature_column_names(src_cols)
    label_cols = [c for c in features.columns if c.startswith("y_")]
    out: dict[str, pd.DataFrame | list[str]] = {
        "features": features,
        "feature_cols": feature_cols,
        "label_cols": label_cols,
    }
    if panel_features is not None:
        out["panel_features"] = panel_features
    return out


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


def transaction_cost(notional: float, side: str, cfg: dict) -> float:
    c = cfg["costs"]
    brokerage = notional * c["brokerage_bps"] / 10000
    stt = notional * c["stt_bps"] / 10000 if side == "SELL" else 0
    exchange = notional * c["exchange_bps"] / 10000
    stamp = notional * c["stamp_bps"] / 10000 if side == "BUY" else 0
    sebi = notional * c["sebi_bps"] / 10000
    gst = (brokerage + exchange + sebi) * c["gst_rate"]
    return brokerage + stt + exchange + stamp + sebi + gst


def execution_price(px: float, side: str, cfg: dict) -> float:
    slip = cfg["costs"]["slippage_bps"] / 10000
    return px * (1 + slip) if side == "BUY" else px * (1 - slip)


def sessions_held(entry_date, current_date, calendar: pd.DatetimeIndex) -> int:
    i0 = calendar.get_indexer([pd.Timestamp(entry_date)])[0]
    i1 = calendar.get_indexer([pd.Timestamp(current_date)])[0]
    if i0 < 0 or i1 < 0:
        return 0
    return max(0, i1 - i0)


def _portfolio_mtm(cash: float, positions: dict) -> float:
    mtm = cash
    for pos in positions.values():
        mtm += pos["shares"] * pos["last_close"]
    return mtm


def equity_curve_metrics(eq: pd.DataFrame, initial_capital: float) -> dict[str, float]:
    if eq.empty:
        return {}
    peak = eq["equity"].cummax()
    dd = (eq["equity"] / peak - 1).min()
    ret = eq["equity"].iloc[-1] / initial_capital - 1
    rets = eq["equity"].pct_change().dropna()
    sharpe = (
        float(rets.mean() / rets.std() * np.sqrt(252))
        if len(rets) > 1 and rets.std() > 0
        else np.nan
    )
    return {
        "total_return": float(ret),
        "max_drawdown": float(dd),
        "sharpe_daily_ann": sharpe,
        "n_sessions": int(len(eq)),
    }


def enrich_equity_drawdown(eq: pd.DataFrame) -> pd.DataFrame:
    out = eq.sort_values("Date").copy()
    out["peak"] = out["equity"].cummax()
    out["drawdown"] = out["equity"] / out["peak"] - 1
    return out


def report_equity_diagnostics(
    eq: pd.DataFrame,
    label: str,
    initial_capital: float | None = None,
) -> None:
    """Print drawdown / worst-session diagnostics (matches notebook forensic snippet)."""
    if eq.empty:
        print(f"\n{label}: empty equity curve")
        return
    eq = enrich_equity_drawdown(eq)
    ic = float(initial_capital) if initial_capital is not None else float(eq["equity"].iloc[0])
    print(f"\n{label}")
    print("Initial capital (denominator):", ic)
    print("First session equity:", eq["equity"].iloc[0])
    print("Last equity:", eq["equity"].iloc[-1])
    print("Minimum equity:", eq["equity"].min())
    print("Minimum drawdown:", eq["drawdown"].min())
    print("Sessions below 50% of peak:", int((eq["drawdown"] < -0.50).sum()))
    cols = ["Date", "equity", "peak", "drawdown", "n_pos"]
    if "n_pos_no_bar" in eq.columns:
        cols.append("n_pos_no_bar")
    worst = eq.nsmallest(10, "equity")[cols]
    print("\nWorst 10 sessions:")
    print(worst.to_string(index=False))


def normalize_symbol(sym) -> str:
    return str(sym).strip().upper()


def trades_to_round_trips(trades: pd.DataFrame) -> pd.DataFrame:
    """Pair BUY/SELL legs (FIFO per symbol) into closed round trips for export."""
    cols = [
        "symbol",
        "trade_entry_date",
        "trade_entry_price",
        "trade_exit_date",
        "trade_exit_price",
        "profit_loss",
    ]
    if trades is None or trades.empty:
        return pd.DataFrame(columns=cols)
    need = {"Symbol", "Date", "side", "px", "shares"}
    if not need.issubset(trades.columns):
        return pd.DataFrame(columns=cols)
    t = trades.sort_values(["Symbol", "Date"]).copy()
    t["Date"] = pd.to_datetime(t["Date"])
    rows: list[dict] = []
    for sym, g in t.groupby("Symbol", observed=True):
        open_buy: pd.Series | None = None
        for _, r in g.iterrows():
            side = str(r["side"]).upper()
            if side == "BUY":
                open_buy = r
            elif side == "SELL" and open_buy is not None:
                shares = float(open_buy["shares"])
                entry_px = float(open_buy["px"])
                exit_px = float(r["px"])
                rows.append(
                    {
                        "symbol": str(sym),
                        "trade_entry_date": open_buy["Date"],
                        "trade_entry_price": entry_px,
                        "trade_exit_date": r["Date"],
                        "trade_exit_price": exit_px,
                        "profit_loss": (exit_px - entry_px) * shares,
                    }
                )
                open_buy = None
    return pd.DataFrame(rows, columns=cols)


def simulate_topk_calendar(
    features_df: pd.DataFrame,
    panel_df: pd.DataFrame,
    scores: Iterable[float] | np.ndarray | pd.Series,
    top_k: int,
    cfg: dict,
    calendar: pd.DatetimeIndex,
    *,
    missing_bar_sessions: pd.DatetimeIndex | None = None,
) -> tuple[pd.DataFrame, dict, pd.DataFrame]:
    """Top-K long-only simulator on a full trading calendar with carry-forward MTM."""
    st = cfg["strategy"]
    max_missing = int(st.get("max_missing_sessions", 10))
    prob_floor = float(st.get("prob_threshold", 0))
    panel_df = panel_df.copy()
    panel_df["Symbol"] = panel_df["Symbol"].map(normalize_symbol)
    f = features_df.copy()
    f["score"] = np.asarray(scores)
    f["Date"] = pd.to_datetime(f["Date"])
    f["Symbol"] = f["Symbol"].map(normalize_symbol)
    f = f.sort_values(["Date", "score"], ascending=[True, False]).drop_duplicates(
        ["Date", "Symbol"], keep="first"
    )
    if prob_floor > 0:
        f = f.loc[f["score"] >= prob_floor]
    dmin, dmax = f["Date"].min(), f["Date"].max()
    dates = [d for d in calendar if dmin <= d <= dmax]
    if len(dates) < 2:
        return pd.DataFrame(), {}, pd.DataFrame()

    countable_missing = (
        set(pd.to_datetime(missing_bar_sessions))
        if missing_bar_sessions is not None
        else None
    )

    cash = float(st["initial_capital"])
    positions: dict = {}
    equity_rows: list[dict] = []
    trade_rows: list[dict] = []
    n_trades = 0

    for i in range(len(dates) - 1):
        d, nxt = dates[i], dates[i + 1]
        panel_nxt = panel_df.loc[panel_df["Date"] == nxt].set_index("Symbol")
        n_no_bar = 0

        for sym in list(positions):
            pos = positions[sym]
            if sym not in panel_nxt.index:
                if countable_missing is not None and nxt not in countable_missing:
                    continue
                pos["missing_bars"] = pos.get("missing_bars", 0) + 1
                n_no_bar += 1
                if pos["missing_bars"] > max_missing:
                    raw = pos["last_close"]
                    if np.isfinite(raw) and raw > 0:
                        px = execution_price(raw, "SELL", cfg)
                        notional = pos["shares"] * px
                        cash += notional - transaction_cost(notional, "SELL", cfg)
                        trade_rows.append(
                            {
                                "Date": nxt,
                                "Symbol": sym,
                                "side": "SELL",
                                "reason": "missing_bar",
                                "px": px,
                                "shares": pos["shares"],
                                "notional": notional,
                            }
                        )
                        del positions[sym]
                        n_trades += 1
                continue

            row = panel_nxt.loc[sym]
            opn, lo, cl = float(row["Open"]), float(row["Low"]), float(row["Close"])
            pos["missing_bars"] = 0
            if np.isfinite(cl) and cl > 0:
                pos["last_close"] = cl
                pos["last_mark_date"] = nxt

            held = sessions_held(pos["entry_date"], nxt, calendar)
            stop = pos["entry_px"] * (1 - st["stop_loss"])
            sell = False
            reason = ""
            raw = np.nan
            if np.isfinite(lo) and lo <= stop:
                sell = True
                reason = "stop"
                raw = min(opn, stop) if np.isfinite(opn) else stop
            elif held >= st["max_hold"]:
                sell = True
                reason = "max_hold"
                raw = opn
            if sell and np.isfinite(raw) and raw > 0:
                px = execution_price(raw, "SELL", cfg)
                notional = pos["shares"] * px
                cash += notional - transaction_cost(notional, "SELL", cfg)
                trade_rows.append(
                    {
                        "Date": nxt,
                        "Symbol": sym,
                        "side": "SELL",
                        "reason": reason,
                        "px": px,
                        "shares": pos["shares"],
                        "notional": notional,
                    }
                )
                del positions[sym]
                n_trades += 1

        day = (
            f.loc[f["Date"] == d]
            .sort_values("score", ascending=False)
            .drop_duplicates("Symbol", keep="first")
        )
        for _, r in day.iterrows():
            if len(positions) >= top_k:
                break
            sym = normalize_symbol(r["Symbol"])
            if sym in positions:
                continue
            if sym not in panel_nxt.index:
                continue
            nxt_row = panel_nxt.loc[sym]
            opn = float(nxt_row["Open"])
            prev_c = float(r["Close"])
            if not np.isfinite(opn) or opn <= 0:
                continue
            if prev_c > 0 and opn >= prev_c * 1.195:
                continue
            vol = float(nxt_row["Volume"]) if np.isfinite(nxt_row["Volume"]) else 0
            max_notional = vol * opn * st["max_participation_pct"]
            slots = top_k - len(positions)
            alloc = cash / max(slots, 1)
            notional = min(alloc, max_notional) if max_notional > 0 else alloc
            px = execution_price(opn, "BUY", cfg)
            shares = notional / px
            cost = transaction_cost(notional, "BUY", cfg)
            if cash < notional + cost:
                continue
            cash -= notional + cost
            mark = float(nxt_row["Close"]) if np.isfinite(nxt_row["Close"]) and float(nxt_row["Close"]) > 0 else px
            positions[sym] = {
                "entry_px": px,
                "shares": shares,
                "entry_date": nxt,
                "prev_close": prev_c,
                "last_close": mark,
                "last_mark_date": nxt,
                "missing_bars": 0,
            }
            trade_rows.append(
                {
                    "Date": nxt,
                    "Symbol": sym,
                    "side": "BUY",
                    "reason": "signal",
                    "px": px,
                    "shares": shares,
                    "notional": notional,
                }
            )
            n_trades += 1

        mtm = _portfolio_mtm(cash, positions)
        equity_rows.append(
            {"Date": nxt, "equity": mtm, "n_pos": len(positions), "n_pos_no_bar": n_no_bar}
        )

    eq = pd.DataFrame(equity_rows)
    trades = pd.DataFrame(trade_rows)
    if eq.empty:
        return eq, {}, trades
    stats_out = equity_curve_metrics(eq, float(st["initial_capital"]))
    stats_out["n_trades"] = n_trades
    return eq, stats_out, trades