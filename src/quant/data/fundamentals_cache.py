"""Read locally cached Upstox fundamentals (no API)."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable

from quant.config import UPSTOX_CACHE_DIR
from quant.data.upstox_fundamentals import (
    FUNDAMENTAL_RESOURCES,
    FundamentalsCoverage,
    list_fundamentals_coverage,
    symbol_fundamental_path,
)
from quant.data.upstox import read_json_cache


def fundamentals_coverage_as_dicts(rows: list[FundamentalsCoverage]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]


def get_key_ratios(
    symbol: str,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Cached Upstox key-ratios for one symbol."""
    return get_fundamentals(symbol, "key-ratios", cache_dir=cache_dir)


def list_stocks_with_key_ratios(
    *,
    cache_dir: Path | None = None,
    symbols: Iterable[str] | None = None,
) -> list[FundamentalsCoverage]:
    rows = list_fundamentals_coverage(cache_dir=cache_dir or UPSTOX_CACHE_DIR, symbols=symbols)
    return [r for r in rows if "key-ratios" in r.resources]


def get_share_holdings(
    symbol: str,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Cached Upstox share-holdings for one symbol."""
    return get_fundamentals(symbol, "share-holdings", cache_dir=cache_dir)


def list_stocks_with_share_holdings(
    *,
    cache_dir: Path | None = None,
    symbols: Iterable[str] | None = None,
) -> list[FundamentalsCoverage]:
    rows = list_fundamentals_coverage(cache_dir=cache_dir or UPSTOX_CACHE_DIR, symbols=symbols)
    return [r for r in rows if "share-holdings" in r.resources]


def get_fundamentals(
    symbol: str,
    resource: str,
    *,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    """Load one cached fundamentals JSON for a symbol."""
    sym = symbol.strip().upper()
    res = resource.strip().lower()
    if res not in FUNDAMENTAL_RESOURCES:
        raise ValueError(f"Unknown resource {resource!r}; allowed: {FUNDAMENTAL_RESOURCES}")
    path = symbol_fundamental_path(sym, res, cache_dir=cache_dir)
    if not path.is_file():
        raise FileNotFoundError(f"No fundamentals cache for {sym}/{res} at {path}")
    data = read_json_cache(path)
    if data is None:
        raise FileNotFoundError(f"Empty fundamentals cache for {sym}/{res}")
    return data


def list_stocks_with_fundamentals(
    *,
    cache_dir: Path | None = None,
    symbols: Iterable[str] | None = None,
) -> list[FundamentalsCoverage]:
    return list_fundamentals_coverage(cache_dir=cache_dir or UPSTOX_CACHE_DIR, symbols=symbols)