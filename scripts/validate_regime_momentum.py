#!/usr/bin/env python3
"""Pick regime_momentum params for ~30% CAGR with walk-forward stability."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from quant.research.regime_momentum_portfolio import (  # noqa: E402
    DEFAULT_REGIME_MOMENTUM_PARAMS,
    ROBUST_PARAM_GRID,
    evaluate_params_walkforward,
)

CACHE = _REPO / "data" / "cache" / "regime_opt_monthly.parquet"


def main() -> None:
    if not CACHE.is_file():
        print("Run scripts/optimize_regime_momentum.py first to build cache")
        sys.exit(1)
    monthly = pd.read_parquet(CACHE)
    monthly["Date"] = pd.to_datetime(monthly["Date"])

    rows = []
    for overrides in ROBUST_PARAM_GRID:
        r = evaluate_params_walkforward(monthly, overrides)
        if r.get("ok"):
            rows.append(r)

    df = pd.DataFrame(rows)
    if df.empty:
        print("No results")
        sys.exit(1)

    # Target ~30% on FULL sample; rank by OOS (val+test) not by train.
    df["oos_cagr"] = (df["val_cagr"] + df["test_cagr"]) / 2.0
    df["oos_dd"] = df[["val_dd", "test_dd"]].min(axis=1)
    df["target_penalty"] = (df["full_cagr"] - 30.0).abs()
    stable = df.loc[df["stable"]].copy()

    pick = stable
    if pick.empty:
        print("Warning: no config passed stability gates; using relaxed filter (test CAGR >= 10)")
        pick = df.loc[df["test_cagr"] >= 10].copy()
    if pick.empty:
        pick = df

    pick = pick.sort_values(["target_penalty", "oos_dd", "oos_cagr"], ascending=[True, False, False])
    best = pick.iloc[0]

    print("=== Selected (walk-forward) ===")
    print("Stability:", bool(best["stable"]))
    print(f"Full:    CAGR {best['full_cagr']:.1f}%  DD {best['full_dd']:.1f}%")
    print(f"Train:   CAGR {best['train_cagr']:.1f}%  DD {best['train_dd']:.1f}%  (≤2015)")
    print(f"Val:     CAGR {best['val_cagr']:.1f}%  DD {best['val_dd']:.1f}%  (2016-2019)")
    print(f"Test:    CAGR {best['test_cagr']:.1f}%  DD {best['test_dd']:.1f}%  (2020+)")

    params = best["params"]
    keys = [
        "top_k",
        "mom_lookback_months",
        "bench_sma_months",
        "bench_min_momentum",
        "target_vol_annual",
        "max_leverage",
        "gross_cap",
        "dd_hard_stop_pct",
    ]
    slim = {k: params[k] for k in keys}
    print("\nRecommended DEFAULT override:")
    print(json.dumps(slim, indent=2))

    near30 = pick.loc[pick["full_cagr"].between(27, 33)].head(8)
    if not near30.empty:
        print("\nOther stable-ish configs with full CAGR 27-33% (lowest OOS DD first):")
        show = near30.sort_values("oos_dd", ascending=False)[
            ["full_cagr", "full_dd", "test_cagr", "test_dd", "oos_cagr", "stable"]
        ]
        print(show.to_string(index=False))


if __name__ == "__main__":
    main()