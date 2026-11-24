"""Upstox candle parsing and cache helpers."""

from __future__ import annotations

from datetime import date

import pandas as pd

from quant.data.upstox import (
    UpstoxRateLimiter,
    _year_chunks,
    candles_to_dataframe,
    expand_fetch_window,
    max_inclusive_calendar_days,
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
    max_inc = max_inclusive_calendar_days("day")
    for a, b in chunks[:-1]:
        assert (b - a).days + 1 == max_inc, f"non-max chunk {a}..{b}"
    if len(chunks) > 1:
        last_span = (chunks[-1][1] - chunks[-1][0]).days + 1
        assert last_span <= max_inc


def test_year_chunks_minimizes_api_calls_for_long_span():
    chunks = _year_chunks(date(2015, 1, 1), date(2020, 12, 31))
    max_inc = max_inclusive_calendar_days("day")
    total_days = (date(2020, 12, 31) - date(2015, 1, 1)).days + 1
    expected_calls = (total_days + max_inc - 1) // max_inc
    assert len(chunks) == expected_calls


def test_month_interval_chunks_use_ten_year_windows():
    start = date(2005, 1, 1)
    end = date(2025, 6, 1)
    from quant.data.upstox import _interval_chunks

    chunks = _interval_chunks("month", start, end)
    max_inc = max_inclusive_calendar_days("month")
    for a, b in chunks[:-1]:
        assert (b - a).days + 1 == max_inc
    assert chunks[0][0] == start
    assert chunks[-1][1] == end


def test_expand_fetch_window_uses_max_year_ending_at_to():
    short_from = date(2024, 11, 1)
    to = date(2024, 11, 25)
    wide_from, wide_to = expand_fetch_window(short_from, to, interval="day")
    assert wide_to == to
    assert (wide_to - wide_from).days + 1 == max_inclusive_calendar_days("day")


def test_expand_fetch_window_month_interval():
    short_from = date(2024, 1, 1)
    to = date(2024, 6, 1)
    wide_from, wide_to = expand_fetch_window(short_from, to, interval="month")
    assert wide_to == to
    assert (wide_to - wide_from).days + 1 == max_inclusive_calendar_days("month")


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