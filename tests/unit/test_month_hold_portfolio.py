"""Month-hold portfolio smoke tests."""

from __future__ import annotations

import numpy as np
import pandas as pd

from quant.research.month_hold_portfolio import (
    DEFAULT_MONTH_HOLD_PARAMS,
    backtest_month_hold_portfolio,
    summarize_month_hold,
)


def _tiny_panel() -> pd.DataFrame:
    rng = np.random.default_rng(1)
    months = pd.date_range("2012-01-31", periods=48, freq="ME")
    parts = []
    for sym in ("AAA", "BBB", "CCC", "NIFTYBEES"):
        close = 100 * np.cumprod(1 + 0.02 * rng.standard_normal(len(months)))
        parts.append(
            pd.DataFrame(
                {
                    "Date": months,
                    "Symbol": sym,
                    "Open": close,
                    "High": close * 1.02,
                    "Low": close * 0.98,
                    "Close": close,
                    "Volume": 5e5,
                }
            )
        )
    return pd.concat(parts, ignore_index=True)


def test_month_hold_backtest_runs():
    monthly = _tiny_panel()
    trades, eq = backtest_month_hold_portfolio(monthly, DEFAULT_MONTH_HOLD_PARAMS)
    s = summarize_month_hold(trades, eq, DEFAULT_MONTH_HOLD_PARAMS)
    assert not eq.empty
    assert s.initial_capital == DEFAULT_MONTH_HOLD_PARAMS["initial_capital"]