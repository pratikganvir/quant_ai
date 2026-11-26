"""Walk-forward helper smoke test."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.research.regime_momentum_portfolio import (
    ROBUST_PARAM_GRID,
    evaluate_params_walkforward,
)


def test_walkforward_runs_on_tiny_panel():
    rng = np.random.default_rng(0)
    months = pd.date_range("2010-01-31", periods=80, freq="ME")
    parts = []
    for sym in ("AAA", "BBB", "NIFTYBEES"):
        close = 100 * np.cumprod(1 + 0.01 + 0.03 * rng.standard_normal(len(months)))
        parts.append(
            pd.DataFrame(
                {
                    "Date": months,
                    "Symbol": sym,
                    "Open": close,
                    "High": close * 1.01,
                    "Low": close * 0.99,
                    "Close": close,
                    "Volume": 1e6,
                }
            )
        )
    monthly = pd.concat(parts, ignore_index=True)
    r = evaluate_params_walkforward(monthly, ROBUST_PARAM_GRID[0], train_end="2014-12-31", val_end="2016-12-31")
    assert r["ok"] is True
    assert "test_cagr" in r