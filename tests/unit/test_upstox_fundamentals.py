"""Upstox fundamentals cache + URL helpers."""

from __future__ import annotations

from pathlib import Path

from quant.data.fundamentals_cache import get_fundamentals, list_stocks_with_fundamentals
from quant.data.upstox import write_json_cache
from quant.data.upstox_fundamentals import (
    build_fundamentals_url,
    list_fundamentals_coverage,
    symbol_fundamental_path,
)


def test_build_fundamentals_url():
    url = build_fundamentals_url(
        isin="INE002A01018",
        resource="income-statement",
        query={"type": "consolidated", "timePeriod": "yearly"},
    )
    assert url.startswith("https://api.upstox.com/v2/fundamentals/INE002A01018/income-statement?")
    assert "type=consolidated" in url


def test_fundamentals_roundtrip_cache(tmp_path: Path):
    sym = "RELIANCE"
    path = symbol_fundamental_path(sym, "profile", cache_dir=tmp_path)
    write_json_cache(
        path,
        {
            "status": "success",
            "data": {"name": "Reliance Industries"},
            "_meta": {"isin": "INE002A01018", "symbol": sym},
        },
    )
    rows = list_fundamentals_coverage(cache_dir=tmp_path)
    assert len(rows) == 1
    assert rows[0].symbol == "RELIANCE"
    assert "profile" in rows[0].resources

    data = get_fundamentals("RELIANCE", "profile", cache_dir=tmp_path)
    assert data["data"]["name"] == "Reliance Industries"

    stocks = list_stocks_with_fundamentals(cache_dir=tmp_path)
    assert stocks[0].isin == "INE002A01018"