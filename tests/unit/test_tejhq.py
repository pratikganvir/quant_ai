from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd

from quant.data.tejhq import (
    TejHQClient,
    snapshot_rows_to_dataframe,
    validate_stock_dataframe,
)


SAMPLE_ROWS = [
    {
        "symbol": "RELIANCE",
        "series": "EQ",
        "isin": "INE002A01018",
        "name": "Reliance Industries",
        "open": 100.0,
        "high": 105.0,
        "low": 99.0,
        "close": 104.0,
        "last": 104.0,
        "prev_close": 101.0,
        "volume": 10_000,
        "turnover": 1_040_000.0,
        "trades": 1000,
    },
    {
        "symbol": "ZOMATO",
        "series": "BE",
        "isin": "INE758E01017",
        "name": "Zomato",
        "open": 200.0,
        "high": 210.0,
        "low": 195.0,
        "close": 208.0,
        "last": 208.0,
        "prev_close": 198.0,
        "volume": 5_000,
        "turnover": 1_040_000.0,
        "trades": 500,
    },
]


def test_snapshot_rows_to_dataframe_eq_filter():
    day = date(2025, 11, 10)
    df = snapshot_rows_to_dataframe(SAMPLE_ROWS, day, series="EQ")
    assert len(df) == 1
    assert df.iloc[0]["symbol"] == "RELIANCE"
    assert df.iloc[0]["date"] == day
    assert df.iloc[0]["turnover"] == 1_040_000.0


def test_validate_stock_dataframe_ok():
    day = date(2025, 11, 10)
    df = snapshot_rows_to_dataframe(SAMPLE_ROWS[:1], day, series=None)
    ok, reason = validate_stock_dataframe(df, day)
    assert ok
    assert reason == "ok"


@patch("quant.data.tejhq.requests.get")
def test_client_snapshot(mock_get):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": SAMPLE_ROWS[:1], "meta": {}},
    )
    client = TejHQClient(api_key=None)
    rows, source = client.snapshot("nse", date(2025, 11, 10))
    assert rows[0]["symbol"] == "RELIANCE"
    assert source == "api"


@patch("quant.data.tejhq.requests.get")
def test_snapshot_uses_disk_cache(tmp_path, mock_get):
    cache_root = tmp_path / "cache"
    client = TejHQClient(api_key=None, cache_dir=cache_root)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": SAMPLE_ROWS[:1], "meta": {}},
    )
    client.snapshot("nse", date(2025, 11, 10))
    assert mock_get.call_count == 1
    client.snapshot("nse", date(2025, 11, 10))
    assert mock_get.call_count == 1
    _, source = client.snapshot("nse", date(2025, 11, 10))
    assert source == "cache"


@patch("quant.data.tejhq.requests.get")
def test_client_delivery_requires_key(mock_get):
    client = TejHQClient(api_key=None)
    try:
        client.delivery("RELIANCE", from_=date(2025, 11, 1), to=date(2025, 11, 10))
    except Exception as exc:
        assert "TEJHQ_API_KEY" in str(exc)
    else:
        raise AssertionError("expected TejHQError")