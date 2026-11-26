#!/usr/bin/env python3
"""Scan MarketSense monthly MCP panel for multibagger forward paths and entry-month patterns."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

MCP = "https://mcp.marketsenseanalytics.com"
BENCH = "NIFTYBEES"
CACHE = _REPO / "data" / "cache" / "mcp_monthly_universe_min48.parquet"
OUT_DIR = _REPO / "results" / "reports"


def _fetch_json(url: str, timeout: float = 120.0) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "quant-multibagger-scan/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None


def fetch_monthly(sym: str) -> pd.DataFrame | None:
    raw = _fetch_json(f"{MCP}/api/monthly/{sym}?limit=500")
    if not raw:
        return None
    bars = raw.get("bars") or []
    if len(bars) < 48:
        return None
    df = pd.DataFrame(bars)
    return pd.DataFrame(
        {
            "Date": pd.to_datetime(df["date"]),
            "Symbol": sym,
            "Open": df["open"].astype(float),
            "High": df["high"].astype(float),
            "Low": df["low"].astype(float),
            "Close": df["close"].astype(float),
            "Volume": df["volume"].astype(float),
        }
    ).sort_values("Date")


def load_universe_symbols(min_bars: int = 48) -> list[str]:
    raw = _fetch_json(f"{MCP}/api/stocks")
    if not raw:
        raise RuntimeError("Could not list stocks from MCP")
    out: list[str] = []
    for row in raw.get("stocks") or []:
        sym = str(row.get("symbol", "")).upper()
        if row.get("n_bars", 0) >= min_bars and sym:
            out.append(sym)
    return sorted(set(out))


def build_panel(force: bool = False, workers: int = 24) -> pd.DataFrame:
    if CACHE.is_file() and not force:
        df = pd.read_parquet(CACHE)
        df["Date"] = pd.to_datetime(df["Date"])
        return df

    syms = load_universe_symbols(48)
    need = [BENCH] + [s for s in syms if s != BENCH]
    parts: list[pd.DataFrame] = []

    def _one(sym: str) -> tuple[str, pd.DataFrame | None]:
        return sym, fetch_monthly(sym)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, s): s for s in need}
        done = 0
        for fut in as_completed(futs):
            done += 1
            sym, frame = fut.result()
            if frame is not None:
                parts.append(frame)
            if done % 200 == 0 or done == len(need):
                print(f"  fetched {done}/{len(need)} symbols, {len(parts)} ok")

    if not parts:
        raise RuntimeError("No monthly data")
    df = pd.concat(parts, ignore_index=True)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(CACHE, index=False)
    print(f"  cached {len(df):,} rows → {CACHE}")
    return df


def add_features(g: pd.DataFrame, bench_close: pd.Series) -> pd.DataFrame:
    g = g.sort_values("Date").copy()
    c = g["Close"]
    h = g["High"]
    g["ret_1"] = c.pct_change(1)
    g["ret_3"] = c.pct_change(3)
    g["ret_6"] = c.pct_change(6)
    g["ret_12"] = c.pct_change(12)
    g["ret_12_1"] = c.shift(1) / c.shift(13) - 1
    g["sma6"] = c.rolling(6).mean()
    g["sma12"] = c.rolling(12).mean()
    g["sma24"] = c.rolling(24).mean()
    g["above_sma12"] = c > g["sma12"]
    g["above_sma24"] = c > g["sma24"]
    g["hi12"] = h.rolling(12).max()
    g["brk12"] = c > g["hi12"].shift(1)
    g["dist_hi12"] = c / g["hi12"] - 1
    g["vol_6"] = g["ret_1"].rolling(6).std()
    g["vol_24"] = g["ret_1"].rolling(24).std()
    g["vol_ratio"] = g["vol_6"] / g["vol_24"].replace(0, np.nan)
    g["med_vol_12"] = g["Volume"].rolling(12).median()
    mfe24: list[float] = []
    close_a = c.to_numpy()
    high_a = h.to_numpy()
    n = len(g)
    for i in range(n):
        if i + 24 >= n or close_a[i] <= 0:
            mfe24.append(np.nan)
        else:
            mfe24.append(float(high_a[i + 1 : i + 25].max() / close_a[i] - 1))
    g["fwd_ret_24"] = mfe24
    g["fwd_close_36"] = c.shift(-36)
    g["fwd_ret_36"] = g["fwd_close_36"] / c - 1
    g = g.merge(
        bench_close.rename("bench_close"),
        left_on="Date",
        right_index=True,
        how="left",
    )
    g["bench_ret_12"] = g["bench_close"].pct_change(12)
    g["rel_mom_12"] = g["ret_12"] - g["bench_ret_12"]
    return g


def classify_multibagger(row: pd.Series, *, min_fwd_24_mfe: float, min_fwd_36: float) -> bool:
    if pd.isna(row.get("fwd_ret_24")) and pd.isna(row.get("fwd_ret_36")):
        return False
    if float(row.get("Close") or 0) < 20:
        return False
    # Drop obvious bad-tape months (splits / holes).
    for col in ("ret_12", "fwd_ret_24", "fwd_ret_36"):
        v = row.get(col)
        if v is not None and not pd.isna(v) and abs(float(v)) > 20:
            return False
    hit24 = float(row.get("fwd_ret_24") or -1) >= min_fwd_24_mfe
    hit36 = float(row.get("fwd_ret_36") or -1) >= min_fwd_36
    return hit24 or hit36


def summarize_patterns(labeled: pd.DataFrame) -> pd.DataFrame:
    feats = [
        "ret_3",
        "ret_6",
        "ret_12",
        "ret_12_1",
        "rel_mom_12",
        "dist_hi12",
        "vol_ratio",
        "above_sma12",
        "above_sma24",
        "brk12",
    ]
    pos = labeled.loc[labeled["is_multibagger_entry"]]
    neg = labeled.loc[~labeled["is_multibagger_entry"]]
    rows = []
    for f in feats:
        if f in ("above_sma12", "above_sma24", "brk12"):
            p = float(pos[f].mean()) if len(pos) else np.nan
            n = float(neg[f].mean()) if len(neg) else np.nan
        else:
            p = float(pos[f].median()) if len(pos) else np.nan
            n = float(neg[f].median()) if len(neg) else np.nan
        rows.append({"feature": f, "multibagger_median_or_rate": p, "control_median_or_rate": n, "lift": p - n})
    return pd.DataFrame(rows)


def episode_table(labeled: pd.DataFrame, top_n: int = 40) -> pd.DataFrame:
    hits = labeled.loc[labeled["is_multibagger_entry"]].copy()
    hits = hits.sort_values(["fwd_ret_36", "fwd_ret_24"], ascending=False)
    cols = [
        "Symbol",
        "Date",
        "Close",
        "ret_12",
        "rel_mom_12",
        "brk12",
        "above_sma12",
        "fwd_ret_24",
        "fwd_ret_36",
    ]
    return hits[cols].head(top_n)


def simple_backtest(panel: pd.DataFrame, bench: pd.DataFrame, *, top_k: int = 8) -> dict:
    """Regime on + 12m breakout leaders; hold 12m at month open (one slot per year)."""
    b = bench.set_index(bench["Date"].dt.to_period("M"))["Close"].sort_index()
    b_sma = b.rolling(12).mean()
    b_mom = b / b.shift(12) - 1
    regime = (b > b_sma) & (b_mom > 0.03)

    months = sorted(panel["Date"].dt.to_period("M").unique())
    equity = 1.0
    curve: list[tuple[pd.Period, float]] = []
    i = 0
    while i < len(months) - 12:
        per = months[i]
        if per not in regime.index or not bool(regime.loc[per]):
            i += 1
            continue
        snap = panel.loc[panel["Date"].dt.to_period("M") == per].copy()
        snap = snap.dropna(subset=["ret_12", "rel_mom_12", "med_vol_12", "Open", "Close"])
        snap = snap.loc[snap["Close"] >= 20]
        snap = snap.loc[snap["med_vol_12"] >= snap["med_vol_12"].quantile(0.6)]
        snap = snap.loc[(snap["above_sma12"]) & (snap["brk12"]) & (snap["rel_mom_12"] > 0.10)]
        if len(snap) < 3:
            i += 1
            continue
        picks = snap.nlargest(top_k, "rel_mom_12")
        entry_open = picks.set_index("Symbol")["Open"]
        exit_per = months[i + 12]
        exit_snap = panel.loc[panel["Date"].dt.to_period("M") == exit_per]
        exit_open = exit_snap.set_index("Symbol")["Open"]
        rets = []
        for sym in entry_open.index:
            if sym in exit_open.index and entry_open[sym] > 0:
                r = exit_open[sym] / entry_open[sym] - 1
                if abs(r) < 5:
                    rets.append(r)
        if rets:
            equity *= 1 + float(np.mean(rets))
        curve.append((per, equity))
        i += 12  # annual rebalance, non-overlapping
    if len(curve) < 2:
        return {"cagr": np.nan, "n_rebalances": 0}
    start, end = curve[0][0].to_timestamp(), curve[-1][0].to_timestamp()
    years = max((end - start).days / 365.25, 1e-6)
    cagr = equity ** (1 / years) - 1
    return {"cagr": float(cagr), "n_rebalances": len(curve), "final_multiple": float(equity)}


def simple_backtest_recovery(panel: pd.DataFrame, bench: pd.DataFrame, *, top_k: int = 8) -> dict:
    """Regime on + deep basing (below SMA24, vol compression), then 3m thrust; hold 24m."""
    b = bench.set_index(bench["Date"].dt.to_period("M"))["Close"].sort_index()
    b_sma = b.rolling(12).mean()
    b_mom = b / b.shift(12) - 1
    regime = (b > b_sma) & (b_mom > 0.0)

    months = sorted(panel["Date"].dt.to_period("M").unique())
    equity = 1.0
    curve: list[tuple[pd.Period, float]] = []
    i = 0
    while i < len(months) - 24:
        per = months[i]
        if per not in regime.index or not bool(regime.loc[per]):
            i += 1
            continue
        snap = panel.loc[panel["Date"].dt.to_period("M") == per].copy()
        snap = snap.dropna(subset=["ret_3", "ret_12", "vol_ratio", "med_vol_12", "Open", "dist_hi12"])
        snap = snap.loc[snap["Close"] >= 20]
        snap = snap.loc[snap["med_vol_12"] >= snap["med_vol_12"].quantile(0.6)]
        snap = snap.loc[
            (snap["ret_12"] < 0)
            & (snap["dist_hi12"] < -0.25)
            & (snap["vol_ratio"] < 0.85)
            & (snap["ret_3"] > 0.05)
        ]
        if len(snap) < 3:
            i += 1
            continue
        picks = snap.nsmallest(top_k, "dist_hi12")  # deepest bases with recent kick
        entry_open = picks.set_index("Symbol")["Open"]
        exit_per = months[i + 24]
        exit_snap = panel.loc[panel["Date"].dt.to_period("M") == exit_per]
        exit_open = exit_snap.set_index("Symbol")["Open"]
        rets = []
        for sym in entry_open.index:
            if sym in exit_open.index and entry_open[sym] > 0:
                r = exit_open[sym] / entry_open[sym] - 1
                if abs(r) < 10:
                    rets.append(r)
        if rets:
            equity *= 1 + float(np.mean(rets))
        curve.append((per, equity))
        i += 24
    if len(curve) < 2:
        return {"cagr": np.nan, "n_rebalances": 0}
    start, end = curve[0][0].to_timestamp(), curve[-1][0].to_timestamp()
    years = max((end - start).days / 365.25, 1e-6)
    cagr = equity ** (1 / years) - 1
    return {"cagr": float(cagr), "n_rebalances": len(curve), "final_multiple": float(equity)}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--force-fetch", action="store_true")
    p.add_argument("--workers", type=int, default=24)
    p.add_argument("--min-fwd-24-mfe", type=float, default=4.0, help="min forward 24m high/c-1 (5x)")
    p.add_argument("--min-fwd-36", type=float, default=4.0, help="min forward 36m close/c-1 (5x)")
    args = p.parse_args()

    print("Loading panel from MCP…")
    panel = build_panel(force=args.force_fetch, workers=args.workers)
    bench = panel.loc[panel["Symbol"] == BENCH, ["Date", "Close"]].drop_duplicates("Date")
    bench_close = bench.set_index("Date")["Close"].sort_index()

    stock_panel = panel.loc[panel["Symbol"] != BENCH].copy()
    print(f"  universe rows: {len(stock_panel):,} ({stock_panel['Symbol'].nunique()} symbols)")

    pieces: list[pd.DataFrame] = []
    for sym, g in stock_panel.groupby("Symbol", sort=False):
        pieces.append(add_features(g, bench_close))
    enriched = pd.concat(pieces, ignore_index=True)
    enriched["is_multibagger_entry"] = enriched.apply(
        lambda r: classify_multibagger(
            r,
            min_fwd_24_mfe=args.min_fwd_24_mfe,
            min_fwd_36=args.min_fwd_36,
        ),
        axis=1,
    )

    eligible = enriched.dropna(subset=["ret_12", "sma12", "fwd_ret_24"])
    eligible = eligible.loc[eligible["Close"] >= 20]
    eligible = eligible.loc[eligible["med_vol_12"] >= eligible.groupby("Date")["med_vol_12"].transform("median")]
    labeled = eligible.loc[eligible["Date"] < enriched["Date"].max() - pd.DateOffset(months=36)]

    summary = summarize_patterns(labeled)
    episodes = episode_table(labeled)
    symbols_hit = labeled.loc[labeled["is_multibagger_entry"], "Symbol"].nunique()
    months_hit = int(labeled["is_multibagger_entry"].sum())
    rate = months_hit / max(len(labeled), 1)

    feat_panel = enriched.drop(columns=[c for c in enriched.columns if c.startswith("fwd_")], errors="ignore")
    bt_mom = simple_backtest(feat_panel, bench)
    bt_rec = simple_backtest_recovery(feat_panel, bench)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary_path = OUT_DIR / "multibagger_monthly_pattern_summary.csv"
    episodes_path = OUT_DIR / "multibagger_monthly_top_episodes.csv"
    summary.to_csv(summary_path, index=False)
    episodes.to_csv(episodes_path, index=False)

    print("\n=== Multibagger definition ===")
    print(f"  Forward 24m MFE (high/entry) >= {args.min_fwd_24_mfe:.0%}  OR  36m close return >= {args.min_fwd_36:.0%}")
    print(f"  Entry months flagged: {months_hit:,} / {len(labeled):,} ({rate:.2%})")
    print(f"  Distinct symbols ever flagged: {symbols_hit}")

    print("\n=== Entry-month patterns (multibagger vs rest) ===")
    with pd.option_context("display.max_rows", 20, "display.width", 120):
        print(summary.to_string(index=False, float_format=lambda x: f"{x:+.3f}"))

    print("\n=== Top episodes ===")
    print(episodes.head(15).to_string(index=False))

    print("\n=== Rule probes (non-overlapping books, liquid names) ===")
    print(
        f"  Momentum breakout: CAGR {bt_mom['cagr']:.1%}, "
        f"{bt_mom['n_rebalances']} vintages, {bt_mom.get('final_multiple', np.nan):.2f}x"
    )
    print(
        f"  Recovery thrust:   CAGR {bt_rec['cagr']:.1%}, "
        f"{bt_rec['n_rebalances']} vintages, {bt_rec.get('final_multiple', np.nan):.2f}x"
    )
    print(f"  wrote {summary_path.name}, {episodes_path.name}")


if __name__ == "__main__":
    main()