#!/usr/bin/env python3
"""How deep do multibaggers draw down on the path to 2x/5x? (monthly bars, no strategy PnL)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

PANEL = _REPO / "data/cache/mcp_monthly_universe_min48.parquet"
OUT = _REPO / "results/reports/multibagger_episode_drawdown_stats.csv"


def episode_stats_for_symbol(
    g: pd.DataFrame,
    *,
    min_px: float,
    min_m: float,
) -> list[dict]:
    g = g.sort_values("Date").copy()
    if len(g) < 48:
        return []
    c = g["Close"].to_numpy(dtype=float)
    h = g["High"].to_numpy(dtype=float)
    dates = g["Date"].to_numpy()
    out: list[dict] = []
    n = len(g)
    for i in range(36, n - 24):
        if c[i] < min_px:
            continue
        entry = c[i]
        if not np.isfinite(entry) or entry <= 0:
            continue
        # Forward window: up to 48 months
        end = min(i + 48, n - 1)
        path_c = c[i : end + 1]
        path_h = h[i : end + 1]
        fwd_close_ret = path_c[-1] / entry - 1.0
        fwd_mfe = float(np.nanmax(path_h[1:]) / entry - 1.0)
        if fwd_mfe < min_m:
            continue
        peak = np.maximum.accumulate(path_c)
        # After entry month (you're already long).
        after = path_c[1:]
        dd_from_entry = float(np.nanmin(after / entry - 1.0)) if len(after) else 0.0
        dd_from_peak = float(np.nanmin(path_c / np.maximum(peak, 1e-12) - 1.0))
        months = int(end - i)
        out.append(
            {
                "symbol": g["Symbol"].iloc[0],
                "entry_date": pd.Timestamp(dates[i]).strftime("%Y-%m-%d"),
                "fwd_mfe_pct": fwd_mfe * 100,
                "fwd_close_ret_pct": fwd_close_ret * 100,
                "max_dd_from_entry_pct": dd_from_entry * 100,
                "max_dd_from_peak_pct": dd_from_peak * 100,
                "months_to_peak": int(np.argmax(path_c) - 0),
            }
        )
    return out


def dedupe_episodes(rows: list[dict], gap_months: int = 12) -> list[dict]:
    """One row per symbol per roughly non-overlapping run (keep best mfe in cluster)."""
    if not rows:
        return []
    df = pd.DataFrame(rows).sort_values(["symbol", "entry_date"])
    kept: list[dict] = []
    for sym, grp in df.groupby("symbol"):
        grp = grp.sort_values("entry_date")
        last_end = None
        best = None
        for _, r in grp.iterrows():
            d = pd.Timestamp(r["entry_date"])
            if last_end is not None and (d - last_end).days < gap_months * 28:
                if best is not None and r["fwd_mfe_pct"] > best["fwd_mfe_pct"]:
                    best = r.to_dict()
                continue
            if best is not None:
                kept.append(best)
            best = r.to_dict()
            last_end = d
        if best is not None:
            kept.append(best)
    return kept


def summarize(df: pd.DataFrame, label: str) -> None:
    print(f"\n=== {label} (n={len(df)}) ===")
    for col, title in [
        ("max_dd_from_entry_pct", "Worst dip vs entry (monthly close)"),
        ("max_dd_from_peak_pct", "Worst dip vs running peak since entry"),
    ]:
        s = df[col]
        print(f"  {title}")
        for q in (50, 75, 90, 95):
            print(f"    p{q}: {s.quantile(q/100):6.1f}%")
        print(f"    mean: {s.mean():6.1f}%  min: {s.min():6.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-mfe", type=float, default=1.0, help="min forward MFE (1.0 = 2x on monthly high)")
    ap.add_argument("--min-mfe-5x", type=float, default=4.0, help="5x threshold for separate stats")
    ap.add_argument("--min-price", type=float, default=20.0)
    args = ap.parse_args()

    panel = pd.read_parquet(PANEL)
    panel["Date"] = pd.to_datetime(panel["Date"])
    sym = panel.loc[panel["Symbol"] != "NIFTYBEES", "Symbol"].unique()

    all_rows: list[dict] = []
    for i, s in enumerate(sym, 1):
        g = panel.loc[panel["Symbol"] == s, ["Date", "Symbol", "Close", "High", "Low"]]
        all_rows.extend(episode_stats_for_symbol(g, min_px=args.min_price, min_m=args.min_mfe))
        if i % 400 == 0:
            print(f"  scanned {i}/{len(sym)}")

    raw = pd.DataFrame(all_rows)
    episodes = pd.DataFrame(dedupe_episodes(all_rows))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    episodes.to_csv(OUT, index=False)

    summarize(episodes, f"Multibaggers forward MFE ≥{args.min_mfe+1:.0f}× (deduped episodes, price≥{args.min_price:.0f})")

    five = episodes.loc[episodes["fwd_mfe_pct"] >= (args.min_mfe_5x + 1) * 100]
    if len(five):
        summarize(five, f"Subset forward MFE ≥{args.min_mfe_5x+1:.0f}×")

    print(f"\nEpisodes CSV: {OUT}")
    print(f"Raw entry-months (overlapping): {len(raw)}")


if __name__ == "__main__":
    main()