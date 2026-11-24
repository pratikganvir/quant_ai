"""Upstox candle parsing and cache helpers."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant.data.upstox import (
    UpstoxRateLimiter,
    _year_chunks,
    candles_to_dataframe,
    symbol_fetch_ranges,
)


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


def test_rate_limiter_defaults_under_upstox_caps():
    lim = UpstoxRateLimiter.from_env()
    assert lim.max_per_second <= 50
    assert lim.max_per_minute <= 500
    assert lim.max_per_30_minutes <= 2000
    assert lim.min_interval_s >= 0.0


def test_symbol_fetch_ranges_skips_covered_window():
    existing = pd.DataFrame(
        {"date": [date(2024, 1, 2), date(2024, 6, 1), date(2024, 12, 30)]}
    )
    assert symbol_fetch_ranges(existing, date(2024, 1, 2), date(2024, 12, 30)) == []
    tail = symbol_fetch_ranges(existing, date(2024, 1, 2), date(2025, 1, 15))
    assert tail and tail[0][0] == date(2024, 12, 31)