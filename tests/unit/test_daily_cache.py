"""Daily cache inventory + read helpers."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from quant.data.daily_cache import (
    daily_symbols_dir,
    get_daily_bars,
    list_daily_stock_coverage,
)
from quant.data.upstox import symbol_history_path


def test_daily_cache_roundtrip(tmp_path):
    cache = tmp_path / "raw" / "upstox"

    path = symbol_history_path(cache, "TESTME", interval="day")
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(
        [
            {
                "date": date(2024, 1, 2),
                "symbol": "TESTME",
                "open": 1.0,
                "high": 2.0,
                "low": 0.5,
                "close": 1.5,
                "volume": 100.0,
                "series": "EQ",
            },
            {
                "date": date(2024, 1, 3),
                "symbol": "TESTME",
                "open": 1.5,
                "high": 2.5,
                "low": 1.0,
                "close": 2.0,
                "volume": 110.0,
                "series": "EQ",
            },
        ]
    )
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), path)

    rows = list_daily_stock_coverage(cache_dir=cache)
    assert len(rows) == 1
    assert rows[0].symbol == "TESTME"
    assert rows[0].n_bars == 2
    assert rows[0].first_date == "2024-01-02"

    bars = get_daily_bars("TESTME", date_start="2024-01-03", cache_dir=cache)
    assert len(bars) == 1
    assert bars[0]["close"] == 2.0
    assert daily_symbols_dir(cache).is_dir()