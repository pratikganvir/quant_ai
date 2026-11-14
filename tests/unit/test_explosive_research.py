from datetime import datetime

import numpy as np
import pandas as pd

from quant.research.explosive_research import (
    calendar_shift,
    forward_excursion_calendar,
    trading_calendar_from_panel,
)


def _mini_panel():
    dates = pd.bdate_range("2025-01-01", periods=30)
    rows = []
    for i, d in enumerate(dates):
        rows.append(
            {
                "Date": d,
                "Symbol": "AAA",
                "Open": 100 + i,
                "High": 101 + i,
                "Low": 99 + i,
                "Close": 100 + i,
                "Volume": 1_000_000,
            }
        )
    return pd.DataFrame(rows)


def test_forward_excursion_uses_calendar_not_rows():
    panel = _mini_panel()
    cal = trading_calendar_from_panel(panel)
    out = forward_excursion_calendar(panel, cal, horizons=[5])
    row = out.iloc[10]
    assert np.isfinite(row["mfe_5"])


def test_calendar_shift():
    cal = pd.DatetimeIndex(pd.bdate_range("2025-01-01", periods=10))
    d0 = cal[5]
    shifted = calendar_shift(cal, pd.Series([d0]), -2)
    assert shifted.iloc[0] == cal[3]