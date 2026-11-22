"""
Screen NSE symbols with >100% return over one calendar year.

Return: close(end_date) / close(start_date) - 1, where start_date is the last
observed bar on or before end_date - 1 calendar year (not shift(252) rows).

Also reports avg Volume/Turnover over 5…90 sessions ending on start_date.
"""
from __future__ import annotations

from pathlib import Path
import sys

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from quant.config import DATA_DIR, PARQUET_DATA_DIR

MIN_RETURN = 1.0
MIN_PRICE = 5.0
AS_OF: pd.Timestamp | None = None
EQ_ONLY = True

# Calendar window quality: reject gappy histories (e.g. 252 rows over 5+ years).
MIN_WINDOW_DAYS = 300
MAX_WINDOW_DAYS = 400

LIQ_WINDOWS = (5, 10, 15, 20, 25, 30, 45, 60, 75, 90)

OUTPUT_DIR = DATA_DIR / "results" / "screens"
CSV_LATEST = OUTPUT_DIR / "stocks_100pct_1y_latest.csv"
CSV_EVER_BEST = OUTPUT_DIR / "stocks_100pct_1y_ever_best.csv"


def load_panel() -> pd.DataFrame:
    panel_path = DATA_DIR / "results" / "explosive_moves" / "panel" / "ohlcv_panel.parquet"
    if panel_path.exists():
        import pyarrow.parquet as pq

        have = set(pq.ParquetFile(panel_path).schema.names)
        cols = [c for c in ("Date", "Symbol", "Close", "Volume", "Turnover", "Series") if c in have]
        df = pd.read_parquet(panel_path, columns=cols)
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    import duckdb

    files = sorted(
        p
        for p in PARQUET_DATA_DIR.rglob("nse_cm_*.parquet")
        if "results" not in p.parts and "indices" not in p.parts
    )
    if not files:
        raise FileNotFoundError(f"No nse_cm_*.parquet under {PARQUET_DATA_DIR}")

    paths_sql = ", ".join(f"'{str(p).replace(chr(39), chr(39) * 2)}'" for p in files)
    sql = f"""
    SELECT
        CAST(date AS DATE) AS Date,
        UPPER(TRIM(symbol)) AS Symbol,
        CAST(close AS DOUBLE) AS Close,
        CAST(volume AS DOUBLE) AS Volume,
        CAST(turnover AS DOUBLE) AS Turnover,
        series AS Series
    FROM read_parquet([{paths_sql}], union_by_name=true)
    WHERE close IS NOT NULL AND close > 0
    """
    df = duckdb.connect().execute(sql).df()
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def _prepare_bars(panel: pd.DataFrame, *, eq_only: bool) -> pd.DataFrame:
    x = panel.dropna(subset=["Date", "Symbol", "Close"]).copy()
    x["Symbol"] = x["Symbol"].astype(str).str.upper().str.strip()
    x["Close"] = pd.to_numeric(x["Close"], errors="coerce")
    x = x.dropna(subset=["Close"])
    x = x.drop_duplicates(["Symbol", "Date"], keep="last")

    if "Volume" in x.columns:
        x["Volume"] = pd.to_numeric(x["Volume"], errors="coerce")
    else:
        x["Volume"] = float("nan")

    if "Turnover" in x.columns:
        x["Turnover"] = pd.to_numeric(x["Turnover"], errors="coerce")
    else:
        x["Turnover"] = float("nan")

    missing_turnover = x["Turnover"].isna() & x["Volume"].notna() & x["Close"].notna()
    x.loc[missing_turnover, "Turnover"] = (
        x.loc[missing_turnover, "Close"] * x.loc[missing_turnover, "Volume"]
    )

    if eq_only and "Series" in x.columns:
        x = x[x["Series"].astype(str).str.upper().str.strip().eq("EQ")]

    return x.sort_values(["Symbol", "Date"]).reset_index(drop=True)


def _liquidity_column_names() -> tuple[list[str], list[str]]:
    vol_cols = [f"avg_volume_{w}d" for w in LIQ_WINDOWS]
    turn_cols = [f"avg_turnover_{w}d" for w in LIQ_WINDOWS]
    return vol_cols, turn_cols


def attach_liquidity_features(bars: pd.DataFrame) -> pd.DataFrame:
    x = bars.copy()
    g = x.groupby("Symbol", observed=True)
    for w in LIQ_WINDOWS:
        x[f"avg_volume_{w}d"] = g["Volume"].transform(
            lambda s, win=w: s.rolling(win, min_periods=win).mean()
        )
        x[f"avg_turnover_{w}d"] = g["Turnover"].transform(
            lambda s, win=w: s.rolling(win, min_periods=win).mean()
        )
    return x


def merge_liquidity_at_start(
    result: pd.DataFrame,
    bars_with_liq: pd.DataFrame,
) -> pd.DataFrame:
    vol_cols, turn_cols = _liquidity_column_names()
    liq_keys = ["Symbol", "Date"] + vol_cols + turn_cols
    at_start = bars_with_liq[liq_keys].rename(columns={"Date": "start_date"})
    out = result.merge(at_start, on=["Symbol", "start_date"], how="left")
    lead = list(result.columns)
    return out[lead + vol_cols + turn_cols]


def _calendar_1y_one_symbol(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("Date").copy()
    ref = g[["Date", "Close"]].rename(columns={"Date": "start_date", "Close": "start_price"})
    g["lookup_date"] = g["Date"] - pd.DateOffset(years=1)
    merged = pd.merge_asof(
        g,
        ref.sort_values("start_date"),
        left_on="lookup_date",
        right_on="start_date",
        direction="backward",
    )
    merged["end_date"] = merged["Date"]
    merged["end_price"] = merged["Close"]
    merged["ret_1y"] = merged["end_price"] / merged["start_price"] - 1.0
    merged["window_days"] = (merged["end_date"] - merged["start_date"]).dt.days
    return merged


def attach_calendar_1y_returns(bars: pd.DataFrame) -> pd.DataFrame:
    return bars.groupby("Symbol", group_keys=False, observed=True).apply(_calendar_1y_one_symbol)


def _valid_window_mask(
    frame: pd.DataFrame,
    *,
    min_window_days: int,
    max_window_days: int,
) -> pd.Series:
    return (
        frame["start_price"].notna()
        & frame["start_date"].notna()
        & frame["window_days"].notna()
        & (frame["window_days"] >= min_window_days)
        & (frame["window_days"] <= max_window_days)
    )


def stocks_over_100pct_1y(
    panel: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
    min_return: float = MIN_RETURN,
    min_price: float | None = MIN_PRICE,
    eq_only: bool = EQ_ONLY,
    min_window_days: int = MIN_WINDOW_DAYS,
    max_window_days: int = MAX_WINDOW_DAYS,
) -> pd.DataFrame:
    x = _prepare_bars(panel, eq_only=eq_only)
    bars_liq = attach_liquidity_features(x)
    x = attach_calendar_1y_returns(x)

    as_of_ts = pd.Timestamp(as_of) if as_of is not None else x["Date"].max()
    snap = x.loc[x["Date"] == as_of_ts].copy()

    mask = _valid_window_mask(
        snap, min_window_days=min_window_days, max_window_days=max_window_days
    ) & (snap["ret_1y"] > min_return)
    if min_price is not None:
        mask &= snap["end_price"] >= min_price

    out = snap.loc[mask, ["Symbol", "start_date", "start_price", "end_date", "end_price", "ret_1y", "window_days"]].copy()
    out["ret_1y_pct"] = (out["ret_1y"] * 100).round(2)
    out = merge_liquidity_at_start(out, bars_liq)
    return out.sort_values("ret_1y", ascending=False).reset_index(drop=True)


def symbols_ever_100pct_in_rolling_year(
    panel: pd.DataFrame,
    *,
    min_return: float = MIN_RETURN,
    eq_only: bool = EQ_ONLY,
    min_window_days: int = MIN_WINDOW_DAYS,
    max_window_days: int = MAX_WINDOW_DAYS,
) -> pd.DataFrame:
    x = _prepare_bars(panel, eq_only=eq_only)
    bars_liq = attach_liquidity_features(x)
    x = attach_calendar_1y_returns(x)

    ok = _valid_window_mask(
        x, min_window_days=min_window_days, max_window_days=max_window_days
    )
    hits = x.loc[ok & x["ret_1y"].gt(min_return), ["Symbol", "start_date", "start_price", "end_date", "end_price", "ret_1y", "window_days"]]

    best = (
        hits.sort_values("ret_1y", ascending=False)
        .groupby("Symbol", as_index=False)
        .first()
        .rename(columns={"ret_1y": "max_ret_1y"})
    )
    best["max_ret_1y_pct"] = (best["max_ret_1y"] * 100).round(2)
    best = merge_liquidity_at_start(best, bars_liq)
    return best.sort_values("max_ret_1y", ascending=False).reset_index(drop=True)


def export_csv(winners: pd.DataFrame, ever: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    winners.to_csv(CSV_LATEST, index=False)
    ever.to_csv(CSV_EVER_BEST, index=False)


def main() -> None:
    print("DATA_DIR:", DATA_DIR)
    print("PARQUET_DATA_DIR:", PARQUET_DATA_DIR)
    print(
        "Window: 1 calendar year (merge_asof), "
        f"window_days in [{MIN_WINDOW_DAYS}, {MAX_WINDOW_DAYS}]"
    )

    panel = load_panel()
    print("Panel rows:", len(panel), "| symbols:", panel["Symbol"].nunique())
    print("Date range:", panel["Date"].min(), "→", panel["Date"].max())

    winners = stocks_over_100pct_1y(panel, as_of=AS_OF)
    as_of_str = winners["end_date"].iloc[0] if len(winners) else panel["Date"].max()
    print(f"\nAs of {as_of_str}: {len(winners)} symbols with >100% in 1 calendar year")
    pd.set_option("display.max_rows", 200)
    pd.set_option("display.width", 240)
    if len(winners):
        print(winners.to_string(index=False))

    ever = symbols_ever_100pct_in_rolling_year(panel)
    print(f"\nEver exceeded 100% in a calendar year: {len(ever)} symbols (top 30)")
    if len(ever):
        print(ever.head(30).to_string(index=False))

    export_csv(winners, ever)
    print(f"\nWrote:\n  {CSV_LATEST}\n  {CSV_EVER_BEST}")


if __name__ == "__main__":
    main()