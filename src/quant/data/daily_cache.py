"""Read locally cached Upstox daily OHLCV (no API)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import pyarrow.parquet as pq

from quant.config import UPSTOX_CACHE_DIR
from quant.data.upstox import list_cached_symbols, symbol_history_path


@dataclass(frozen=True)
class StockDailyCoverage:
    symbol: str
    first_date: str | None
    last_date: str | None
    n_bars: int
    cache_path: str


def daily_symbols_dir(cache_dir: Path | None = None) -> Path:
    root = cache_dir or UPSTOX_CACHE_DIR
    return root / "symbols" / "nse"


def list_daily_stock_coverage(
    *,
    cache_dir: Path | None = None,
    symbols: Iterable[str] | None = None,
) -> list[StockDailyCoverage]:
    """Inventory per-symbol daily parquets (date range + row count)."""
    root = cache_dir or UPSTOX_CACHE_DIR
    syms = [s.upper() for s in symbols] if symbols else list_cached_symbols(root, interval="day")
    out: list[StockDailyCoverage] = []
    for sym in syms:
        path = symbol_history_path(root, sym, interval="day")
        if not path.is_file():
            continue
        try:
            table = pq.read_table(path, columns=["date"])
            if table.num_rows == 0:
                out.append(StockDailyCoverage(sym, None, None, 0, str(path)))
                continue
            dates = pd.to_datetime(table.column("date").to_pandas(), errors="coerce").dropna()
            if dates.empty:
                out.append(StockDailyCoverage(sym, None, None, 0, str(path)))
                continue
            out.append(
                StockDailyCoverage(
                    sym,
                    dates.min().date().isoformat(),
                    dates.max().date().isoformat(),
                    int(table.num_rows),
                    str(path),
                )
            )
        except Exception:
            out.append(StockDailyCoverage(sym, None, None, 0, str(path)))
    return sorted(out, key=lambda r: r.symbol)


def coverage_as_dicts(rows: list[StockDailyCoverage]) -> list[dict[str, Any]]:
    return [asdict(r) for r in rows]


def get_daily_bars(
    symbol: str,
    *,
    date_start: str | date | None = None,
    date_end: str | date | None = None,
    limit: int | None = None,
    cache_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Daily OHLCV rows for one symbol from on-disk Upstox cache."""
    sym = symbol.strip().upper()
    path = symbol_history_path(cache_dir or UPSTOX_CACHE_DIR, sym, interval="day")
    if not path.is_file():
        raise FileNotFoundError(f"No daily cache for {sym} at {path}")

    df = pd.read_parquet(path)
    if df.empty:
        return []

    df = df.rename(
        columns={
            "date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
        }
    )
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])

    if date_start is not None:
        start = pd.Timestamp(date_start).date()
        df = df.loc[df["date"].dt.date >= start]
    if date_end is not None:
        end = pd.Timestamp(date_end).date()
        df = df.loc[df["date"].dt.date <= end]

    df = df.sort_values("date")
    if limit is not None and limit > 0:
        df = df.tail(int(limit))

    rows: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        rows.append(
            {
                "date": r["date"].date().isoformat(),
                "symbol": sym,
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "volume": float(r["volume"]),
            }
        )
    return rows