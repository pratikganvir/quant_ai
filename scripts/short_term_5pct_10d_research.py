"""
10-session forward return labels and short-horizon features for pattern research.

Label (causal): features at close T; forward return uses close at T+10 sessions.
"""
from __future__ import annotations

from pathlib import Path
import sys

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from quant.config import DATA_DIR
from quant.research.explosive_research import calendar_shift, trading_calendar_from_panel

FORWARD_SESSIONS = 10
MIN_FORWARD_RETURN = 0.05
MIN_PRICE = 5.0
EQ_ONLY = True
TRADES_EXPORT_MAX = 5000
TRADES_EXPORT_SEED = 42

OUTPUT_DIR = DATA_DIR / "results" / "short_term_5pct_10d"

FEATURE_COLS = [
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "vol_rvol_5",
    "vol_rvol_20",
    "vol_accel",
    "turn_rvol_20",
    "range_pct",
    "atrp_14",
    "dist_sma20",
    "dist_sma50",
    "sma20_slope_5",
    "high_20_pct",
    "deliv_per",
]


def min_return_from_pct(pct: float) -> float:
    return float(pct) / 100.0


def prepare_bars(panel: pd.DataFrame, *, eq_only: bool = EQ_ONLY) -> pd.DataFrame:
    x = panel.dropna(subset=["Date", "Symbol", "Close"]).copy()
    x["Date"] = pd.to_datetime(x["Date"], errors="coerce").dt.normalize()
    x["Symbol"] = x["Symbol"].astype(str).str.upper().str.strip()
    x["Close"] = pd.to_numeric(x["Close"], errors="coerce")
    x["High"] = pd.to_numeric(x.get("High", x["Close"]), errors="coerce")
    x["Low"] = pd.to_numeric(x.get("Low", x["Close"]), errors="coerce")
    x["Volume"] = pd.to_numeric(x.get("Volume", np.nan), errors="coerce")
    x = x.dropna(subset=["Close"])
    x = x.drop_duplicates(["Symbol", "Date"], keep="last")
    if "Turnover" in x.columns:
        x["Turnover"] = pd.to_numeric(x["Turnover"], errors="coerce")
    else:
        x["Turnover"] = x["Close"] * x["Volume"]
    if eq_only and "Series" in x.columns:
        x = x[x["Series"].astype(str).str.upper().str.strip().eq("EQ")]
    return x.sort_values(["Symbol", "Date"]).reset_index(drop=True)


def compute_features(bars: pd.DataFrame) -> pd.DataFrame:
    x = bars.copy()
    g = x.groupby("Symbol", observed=True)
    prev_c = g["Close"].shift(1)
    tr = np.maximum(x["High"] - x["Low"], np.maximum((x["High"] - prev_c).abs(), (x["Low"] - prev_c).abs()))
    x["range_pct"] = tr / x["Close"]

    x["ret_1"] = g["Close"].pct_change()
    x["ret_5"] = g["Close"].pct_change(5)
    x["ret_10"] = g["Close"].pct_change(10)
    x["ret_20"] = g["Close"].pct_change(20)

    x["vol_rvol_5"] = x["Volume"] / g["Volume"].transform(lambda s: s.rolling(5, min_periods=3).mean())
    x["vol_rvol_20"] = x["Volume"] / g["Volume"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    x["vol_accel"] = g["Volume"].transform(lambda s: s.rolling(5, min_periods=3).mean()) / g["Volume"].transform(
        lambda s: s.rolling(20, min_periods=10).mean()
    )
    x["turn_rvol_20"] = x["Turnover"] / g["Turnover"].transform(lambda s: s.rolling(20, min_periods=10).mean())

    x["_tr"] = tr
    x["atrp_14"] = g["_tr"].transform(lambda s: s.rolling(14, min_periods=7).mean()) / x["Close"]
    x = x.drop(columns=["_tr"])

    sma20 = g["Close"].transform(lambda s: s.rolling(20, min_periods=10).mean())
    sma50 = g["Close"].transform(lambda s: s.rolling(50, min_periods=25).mean())
    x["sma20"] = sma20
    x["dist_sma20"] = x["Close"] / sma20 - 1.0
    x["dist_sma50"] = x["Close"] / sma50 - 1.0
    x["sma20_slope_5"] = x["sma20"] / x.groupby("Symbol", observed=True)["sma20"].shift(5) - 1.0
    x = x.drop(columns=["sma20"])

    hi20 = g["High"].transform(lambda s: s.rolling(20, min_periods=10).max())
    x["high_20_pct"] = x["Close"] / hi20 - 1.0

    if "deliv_per" in x.columns:
        x["deliv_per"] = pd.to_numeric(x["deliv_per"], errors="coerce")
    elif "DelivPer" in x.columns:
        x["deliv_per"] = pd.to_numeric(x["DelivPer"], errors="coerce")
    else:
        x["deliv_per"] = np.nan

    return x


def attach_forward_labels(
    frame: pd.DataFrame,
    *,
    forward_sessions: int = FORWARD_SESSIONS,
    min_forward_return: float = MIN_FORWARD_RETURN,
    calendar: pd.DatetimeIndex | None = None,
) -> pd.DataFrame:
    cal = calendar if calendar is not None else trading_calendar_from_panel(frame)
    x = frame.copy()
    x["exit_date"] = calendar_shift(cal, x["Date"], forward_sessions)
    exit_tbl = x[["Symbol", "Date", "Close"]].rename(columns={"Date": "exit_date", "Close": "exit_close"})
    x = x.merge(exit_tbl, on=["Symbol", "exit_date"], how="left")
    x[f"fwd_ret_{forward_sessions}"] = x["exit_close"] / x["Close"] - 1.0
    x[f"horizon_ok_{forward_sessions}"] = x["exit_close"].notna()
    x[f"y_hit_{forward_sessions}d"] = (
        x[f"horizon_ok_{forward_sessions}"]
        & x[f"fwd_ret_{forward_sessions}"].ge(min_forward_return)
    ).astype("int8")
    return x


def build_dataset(
    panel: pd.DataFrame,
    *,
    forward_sessions: int = FORWARD_SESSIONS,
    min_forward_return: float = MIN_FORWARD_RETURN,
    min_price: float | None = MIN_PRICE,
    eq_only: bool = EQ_ONLY,
    date_start: pd.Timestamp | None = None,
    date_end: pd.Timestamp | None = None,
) -> pd.DataFrame:
    bars = prepare_bars(panel, eq_only=eq_only)
    x = compute_features(bars)
    x = attach_forward_labels(
        x, forward_sessions=forward_sessions, min_forward_return=min_forward_return
    )
    ok = x[f"horizon_ok_{forward_sessions}"]
    if min_price is not None:
        ok &= x["Close"] >= min_price
    if date_start is not None:
        ok &= x["Date"] >= pd.Timestamp(date_start)
    if date_end is not None:
        ok &= x["Date"] <= pd.Timestamp(date_end)
    return x.loc[ok].copy()


def univariate_feature_rank(
    ds: pd.DataFrame,
    *,
    label_col: str,
    feature_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Rank features by |median(hit) - median(miss)| / pooled std (Cohen's d-style)."""
    feature_cols = feature_cols or [c for c in FEATURE_COLS if c in ds.columns]
    y = ds[label_col].astype(int)
    rows: list[dict] = []
    for col in feature_cols:
        s = pd.to_numeric(ds[col], errors="coerce")
        m = s.notna() & y.notna()
        if m.sum() < 100 or y.loc[m].nunique() < 2:
            continue
        hit = s.loc[m & (y == 1)]
        miss = s.loc[m & (y == 0)]
        if len(hit) < 10 or len(miss) < 10:
            continue
        mh, mm = float(hit.median()), float(miss.median())
        sh, sm = float(hit.std(ddof=1)), float(miss.std(ddof=1))
        pooled = np.sqrt((sh**2 + sm**2) / 2.0) if (sh > 0 or sm > 0) else np.nan
        d = (mh - mm) / pooled if pooled and np.isfinite(pooled) and pooled > 0 else np.nan
        rows.append(
            {
                "feature": col,
                "median_hit": mh,
                "median_miss": mm,
                "cohen_d": d,
                "hit_rate_top_quartile": float(y.loc[m & (s >= s.loc[m].quantile(0.75))].mean()),
                "hit_rate_bottom_quartile": float(y.loc[m & (s <= s.loc[m].quantile(0.25))].mean()),
                "n_valid": int(m.sum()),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["abs_cohen_d"] = out["cohen_d"].abs()
    return out.sort_values("abs_cohen_d", ascending=False).reset_index(drop=True)


def select_rf_features(
    ds: pd.DataFrame,
    feature_cols: list[str],
    *,
    min_coverage: float = 0.50,
) -> list[str]:
    """Drop features that are mostly missing (e.g. delivery % not in panel)."""
    cols = [c for c in feature_cols if c in ds.columns]
    if not cols:
        return []
    cov = ds[cols].apply(pd.to_numeric, errors="coerce").notna().mean()
    return [c for c in cols if float(cov.get(c, 0.0)) >= min_coverage]


def prepare_rf_sample(
    ds: pd.DataFrame,
    *,
    label_col: str,
    feature_cols: list[str] | None = None,
    max_rows: int = 400_000,
    random_seed: int = 42,
    min_coverage: float = 0.50,
) -> tuple[pd.DataFrame, pd.Series, dict[str, int | float]]:
    """Median-imputed feature matrix + stratified subsample with both classes."""
    feature_cols = feature_cols or FEATURE_COLS
    feat_cols = select_rf_features(ds, feature_cols, min_coverage=min_coverage)
    if not feat_cols:
        raise ValueError("No features pass min_coverage — check panel columns.")

    y = ds[label_col].astype(int)
    diag: dict[str, int | float] = {
        "rows_total": len(ds),
        "hits_total": int((y == 1).sum()),
        "miss_total": int((y == 0).sum()),
        "n_features": len(feat_cols),
    }
    if diag["hits_total"] == 0:
        raise ValueError("No positive labels in dataset. Lower MIN_RETURN_PCT or widen DATE_START.")
    if diag["miss_total"] == 0:
        raise ValueError("No negative labels in dataset.")

    X = ds[feat_cols].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median(numeric_only=True))
    frame = X.assign(_y=y)

    if len(frame) > max_rows:
        from sklearn.model_selection import train_test_split

        _, frame = train_test_split(
            frame,
            train_size=max_rows,
            stratify=frame["_y"],
            random_state=random_seed,
        )

    y_out = frame["_y"].astype(int)
    X_out = frame.drop(columns=["_y"])
    diag["rows_train"] = len(frame)
    diag["hits_train"] = int((y_out == 1).sum())
    diag["miss_train"] = int((y_out == 0).sum())
    if y_out.nunique() < 2:
        raise ValueError(
            "RF sample has a single class after filtering — widen DATE_START or lower MIN_RETURN_PCT."
        )
    return X_out, y_out, diag


def trades_export_frame(
    dataset: pd.DataFrame,
    *,
    forward_sessions: int,
    label_col: str,
    feature_cols: list[str] | None = None,
    include_features: bool = True,
) -> pd.DataFrame:
    """One row per entry session: buy at entry close, mark at forward session exit."""
    ret_col = f"fwd_ret_{forward_sessions}"
    ok_col = f"horizon_ok_{forward_sessions}"
    base_cols = [
        "Symbol",
        "Date",
        "Close",
        "exit_date",
        "exit_close",
        ret_col,
        ok_col,
        label_col,
    ]
    missing = [c for c in base_cols if c not in dataset.columns]
    if missing:
        raise KeyError(f"Missing columns for trade export: {missing}")

    out = dataset[base_cols].copy()
    out = out.rename(
        columns={
            "Date": "entry_date",
            "Close": "entry_price",
            "exit_close": "exit_price",
            ret_col: "fwd_ret",
            ok_col: "horizon_complete",
            label_col: "hit",
        }
    )
    out["fwd_ret_pct"] = (out["fwd_ret"] * 100).round(4)
    out["hit"] = out["hit"].astype(int)
    out["outcome"] = out["hit"].map({1: "hit", 0: "miss"})

    if include_features:
        feats = feature_cols or [c for c in FEATURE_COLS if c in dataset.columns]
        for c in feats:
            if c not in out.columns and c in dataset.columns:
                out[c] = dataset[c].values

    front = [
        "Symbol",
        "entry_date",
        "entry_price",
        "exit_date",
        "exit_price",
        "fwd_ret",
        "fwd_ret_pct",
        "hit",
        "outcome",
        "horizon_complete",
    ]
    rest = [c for c in out.columns if c not in front]
    return out[front + rest].sort_values(["entry_date", "Symbol"]).reset_index(drop=True)


def sample_rows_for_export(
    dataset: pd.DataFrame,
    *,
    label_col: str,
    max_rows: int | None,
    random_seed: int = TRADES_EXPORT_SEED,
) -> pd.DataFrame:
    if max_rows is None or len(dataset) <= max_rows:
        return dataset
    y = dataset[label_col].astype(int)
    if y.nunique() < 2:
        return dataset.sample(n=max_rows, random_state=random_seed).reset_index(drop=True)
    from sklearn.model_selection import train_test_split

    sample, _ = train_test_split(
        dataset,
        train_size=max_rows,
        stratify=y,
        random_state=random_seed,
    )
    return sample.reset_index(drop=True)


def export_trades_csv(
    dataset: pd.DataFrame,
    *,
    tag: str,
    forward_sessions: int,
    label_col: str,
    include_features: bool = True,
    max_trades: int | None = TRADES_EXPORT_MAX,
    random_seed: int = TRADES_EXPORT_SEED,
) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    src = sample_rows_for_export(
        dataset, label_col=label_col, max_rows=max_trades, random_seed=random_seed
    )
    trades = trades_export_frame(
        src,
        forward_sessions=forward_sessions,
        label_col=label_col,
        include_features=include_features,
    )
    suffix = f"n{len(trades)}" if max_trades is not None else "all"
    path = OUTPUT_DIR / f"trades_{suffix}_{tag}.csv"
    trades.to_csv(path, index=False)
    return path


def export_results(
    dataset: pd.DataFrame,
    rank: pd.DataFrame,
    *,
    tag: str,
    forward_sessions: int = FORWARD_SESSIONS,
    label_col: str | None = None,
    export_trades: bool = True,
    max_trades: int | None = TRADES_EXPORT_MAX,
    trades_random_seed: int = TRADES_EXPORT_SEED,
) -> tuple[Path, Path, Path | None]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ds_path = OUTPUT_DIR / f"dataset_{tag}.parquet"
    rank_path = OUTPUT_DIR / f"feature_rank_{tag}.csv"
    dataset.to_parquet(ds_path, index=False)
    rank.to_csv(rank_path, index=False)
    trades_path = None
    if export_trades:
        label_col = label_col or f"y_hit_{forward_sessions}d"
        trades_path = export_trades_csv(
            dataset,
            tag=tag,
            forward_sessions=forward_sessions,
            label_col=label_col,
            max_trades=max_trades,
            random_seed=trades_random_seed,
        )
    return ds_path, rank_path, trades_path