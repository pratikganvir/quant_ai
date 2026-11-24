"""Upstox candle parsing and cache helpers."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant.data.upstox import candles_to_dataframe, _year_chunks


def test_candles_to_dataframe():
    candles = [
        ["2024-06-03T00:00:00+05:30", 100.0, 105.0, 99.0, 104.0, 1000, 0],
        ["2024-06-04T00:00:00+05:30", 104.0, 106.0, 103.0, 105.5, 1100, 0],
    ]
    df = candles_to_dataframe(candles, "RELIANCE")
    assert len(df) == 2
    assert df["symbol"].iloc[0] == "RELIANCE"
    assert df["date"].iloc[0] == date(2024, 6, 3)


def test_year_chunks():
    chunks = _year_chunks(date(2020, 1, 1), date(2022, 6, 1))
    assert chunks[0][0] == date(2020, 1, 1)
    assert chunks[-1][1] == date(2022, 6, 1)