"""Upstox historical candles → local cache → NSE daily ``nse_cm_*.parquet`` layout."""

from __future__ import annotations

import gzip
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from quant.config import (
    UPSTOX_CACHE_DIR,
    UPSTOX_DAILY_MAX_INCLUSIVE_DAYS,
    UPSTOX_HTTP_MAX_ATTEMPTS,
    UPSTOX_MIN_INTERVAL_S,
    UPSTOX_MONTHLY_MAX_INCLUSIVE_DAYS,
    UPSTOX_RATE_PER_30MIN,
    UPSTOX_RATE_PER_MINUTE,
    UPSTOX_RATE_PER_SECOND,
    UPSTOX_SYMBOL_MANIFEST_PATH,
)
from quant.data.tejhq import (
    OPTIONAL_STOCK_COLUMNS,
    STOCK_COLUMNS,
    atomic_write_parquet,
    latest_local_parquet_date,
    nse_cm_parquet_path,
    parquet_day_is_valid,
    validate_stock_dataframe,
)

UPSTOX_BASE_URL = "https://api.upstox.com/v2"
NSE_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
CandleInterval = str  # day | month | week


class UpstoxError(RuntimeError):
    pass


WINDOW_30MIN_S = 30 * 60.0
WINDOW_1MIN_S = 60.0
WINDOW_1SEC_S = 1.0


@dataclass
class UpstoxRateLimiter:
    """Sliding-window limiter for Upstox standard APIs (50/s, 500/min, 2000/30min)."""

    max_per_second: int = 8
    max_per_minute: int = 90
    max_per_30_minutes: int = 1800
    min_interval_s: float = 1.0
    _request_times: list[float] = field(default_factory=list)
    _last_request_at: float = 0.0

    @classmethod
    def from_env(cls) -> UpstoxRateLimiter:
        return cls(
            max_per_second=UPSTOX_RATE_PER_SECOND,
            max_per_minute=UPSTOX_RATE_PER_MINUTE,
            max_per_30_minutes=UPSTOX_RATE_PER_30MIN,
            min_interval_s=UPSTOX_MIN_INTERVAL_S,
        )

    def _prune(self, now: float) -> None:
        self._request_times = [t for t in self._request_times if now - t < WINDOW_30MIN_S]

    def acquire(self) -> None:
        while True:
            now = time.monotonic()
            self._prune(now)
            n1s = sum(1 for t in self._request_times if now - t < WINDOW_1SEC_S)
            n1m = sum(1 for t in self._request_times if now - t < WINDOW_1MIN_S)
            n30 = len(self._request_times)
            floor_ok = (now - self._last_request_at) >= self.min_interval_s
            under_caps = (
                n1s < self.max_per_second
                and n1m < self.max_per_minute
                and n30 < self.max_per_30_minutes
            )
            if under_caps and floor_ok:
                self._request_times.append(now)
                self._last_request_at = now
                return

            delay = 0.05
            if not floor_ok:
                delay = max(delay, self.min_interval_s - (now - self._last_request_at))
            if n1s >= self.max_per_second:
                oldest = min(t for t in self._request_times if now - t < WINDOW_1SEC_S)
                delay = max(delay, WINDOW_1SEC_S - (now - oldest) + 0.01)
            if n1m >= self.max_per_minute:
                oldest = min(t for t in self._request_times if now - t < WINDOW_1MIN_S)
                delay = max(delay, WINDOW_1MIN_S - (now - oldest) + 0.01)
            if n30 >= self.max_per_30_minutes:
                oldest = min(self._request_times)
                delay = max(delay, WINDOW_30MIN_S - (now - oldest) + 0.05)
            time.sleep(min(delay, 120.0))


def upstox_access_token() -> str | None:
    return os.getenv("UPSTOX_ACCESS_TOKEN") or os.getenv("UPSTOX_API_KEY")


def instruments_map_path(cache_root: Path) -> Path:
    return cache_root / "instruments" / "nse_eq_map.json.gz"


def symbol_history_path(cache_root: Path, symbol: str, *, interval: CandleInterval = "day") -> Path:
    sub = "nse" if interval == "day" else f"nse_{interval}"
    return cache_root / "symbols" / sub / f"{symbol.upper()}.parquet"


def candle_api_cache_path(
    cache_root: Path,
    instrument_key: str,
    to_d: date,
    from_d: date,
    *,
    interval: CandleInterval = "day",
) -> Path:
    safe = instrument_key.replace("|", "_")
    return (
        cache_root
        / "api"
        / interval
        / f"{safe}_{from_d.isoformat()}_{to_d.isoformat()}.json.gz"
    )


def read_json_cache(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_json_cache(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    tmp.replace(path)


def candles_to_dataframe(candles: Sequence[Sequence[Any]], symbol: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for c in candles:
        if not c or len(c) < 6:
            continue
        ts = pd.to_datetime(c[0], errors="coerce")
        if pd.isna(ts):
            continue
        d = ts.date()
        rows.append(
            {
                "date": d,
                "symbol": symbol.upper(),
                "open": float(c[1]),
                "high": float(c[2]),
                "low": float(c[3]),
                "close": float(c[4]),
                "volume": float(c[5]),
                "series": "EQ",
            }
        )
    if not rows:
        return pd.DataFrame(columns=STOCK_COLUMNS + OPTIONAL_STOCK_COLUMNS)
    out = pd.DataFrame(rows).sort_values("date").drop_duplicates("date", keep="last")
    cols = STOCK_COLUMNS + [c for c in OPTIONAL_STOCK_COLUMNS if c in out.columns]
    return out[cols]


def max_inclusive_calendar_days(interval: CandleInterval) -> int:
    """Widest inclusive date span per Upstox historical-candle request."""
    if interval in ("month", "week"):
        return UPSTOX_MONTHLY_MAX_INCLUSIVE_DAYS
    return UPSTOX_DAILY_MAX_INCLUSIVE_DAYS


def _max_span_add_days(interval: CandleInterval) -> int:
    """``timedelta`` days to add to chunk start (inclusive end = start + this)."""
    return max_inclusive_calendar_days(interval) - 1


def expand_fetch_window(
    from_d: date,
    to_d: date,
    *,
    interval: CandleInterval = "day",
) -> tuple[date, date]:
    """Widen a short gap to the API's maximum window ending at ``to_d`` (fewer redundant calls)."""
    if to_d < from_d:
        return from_d, to_d
    max_inc = max_inclusive_calendar_days(interval)
    span = (to_d - from_d).days + 1
    if span >= max_inc:
        return from_d, to_d
    from_wide = to_d - timedelta(days=max_inc - 1)
    return from_wide, to_d


def _range_chunks(
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
) -> list[tuple[date, date]]:
    """Split [start, end] into the fewest contiguous chunks (all full width except possibly the last)."""
    if end < start:
        return []
    max_add = _max_span_add_days(interval)
    chunks: list[tuple[date, date]] = []
    d = start
    while d <= end:
        chunk_end = min(d + timedelta(days=max_add), end)
        chunks.append((d, chunk_end))
        d = chunk_end + timedelta(days=1)
    return chunks


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    return _range_chunks(start, end, interval="day")


def _interval_chunks(interval: CandleInterval, start: date, end: date) -> list[tuple[date, date]]:
    return _range_chunks(start, end, interval=interval)


def _last_day_of_month(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _expected_units_in_range(start: date, end: date, *, interval: CandleInterval) -> list[date]:
    """Calendar units we expect bars for in ``[start, end]`` (weekdays / month-ends / weeks)."""
    if end < start:
        return []
    if interval == "day":
        from quant.data.tejhq import iter_weekdays

        return list(iter_weekdays(start, end))
    if interval == "month":
        units: list[date] = []
        y, m = start.year, start.month
        while True:
            last = _last_day_of_month(y, m)
            if last >= start and last <= end:
                units.append(last)
            if y > end.year or (y == end.year and m >= end.month):
                break
            if m == 12:
                y, m = y + 1, 1
            else:
                m += 1
        return units
    if interval == "week":
        units = []
        d = start
        while d.weekday() != 0:
            d += timedelta(days=1)
            if d > end:
                return units
        while d <= end:
            units.append(d)
            d += timedelta(days=7)
        return units
    units = []
    d = start
    while d <= end:
        units.append(d)
        d += timedelta(days=1)
    return units


def missing_symbol_calendar_units(
    existing: pd.DataFrame,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
) -> list[date]:
    """Units in ``[start,end]`` absent from per-symbol cache (including interior gaps)."""
    expected = _expected_units_in_range(start, end, interval=interval)
    if not expected:
        return []
    if existing.empty or "date" not in existing.columns:
        return expected

    have_dates = pd.to_datetime(existing["date"], errors="coerce").dt.date.dropna()
    if have_dates.empty:
        return expected

    have = set(have_dates)
    if interval == "month":
        have_months = {(d.year, d.month) for d in have}
        return [d for d in expected if (d.year, d.month) not in have_months]
    if interval == "week":
        have_weeks = {(d.isocalendar().year, d.isocalendar().week) for d in have}
        return [
            d
            for d in expected
            if (d.isocalendar().year, d.isocalendar().week) not in have_weeks
        ]
    return [d for d in expected if d not in have]


def _contiguous_calendar_ranges(sorted_dates: Sequence[date]) -> list[tuple[date, date]]:
    if not sorted_dates:
        return []
    uniq = sorted(set(sorted_dates))
    ranges: list[tuple[date, date]] = []
    a = b = uniq[0]
    for d in uniq[1:]:
        if (d - b).days == 1:
            b = d
        else:
            ranges.append((a, b))
            a = b = d
    ranges.append((a, b))
    return ranges


def _contiguous_month_ranges(month_ends: Sequence[date]) -> list[tuple[date, date]]:
    if not month_ends:
        return []
    periods = sorted({(d.year, d.month) for d in month_ends})

    def _next_month(y: int, m: int) -> tuple[int, int]:
        return (y + 1, 1) if m == 12 else (y, m + 1)

    groups: list[list[tuple[int, int]]] = []
    grp = [periods[0]]
    for p in periods[1:]:
        if p == _next_month(*grp[-1]):
            grp.append(p)
        else:
            groups.append(grp)
            grp = [p]
    groups.append(grp)

    ranges: list[tuple[date, date]] = []
    for grp in groups:
        y0, m0 = grp[0]
        y1, m1 = grp[-1]
        ranges.append((date(y0, m0, 1), _last_day_of_month(y1, m1)))
    return ranges


def _contiguous_week_ranges(week_starts: Sequence[date]) -> list[tuple[date, date]]:
    if not week_starts:
        return []
    uniq = sorted(set(week_starts))
    ranges: list[tuple[date, date]] = []
    a = b = uniq[0]
    for d in uniq[1:]:
        if (d - b).days == 7:
            b = d
        else:
            ranges.append((a, b + timedelta(days=6)))
            a = b = d
    ranges.append((a, b + timedelta(days=6)))
    return ranges


def gap_ranges_from_missing_units(
    missing: Sequence[date],
    *,
    interval: CandleInterval = "day",
) -> list[tuple[date, date]]:
    """Merge missing units into contiguous ``[from,to]`` gaps before max-width chunking."""
    if not missing:
        return []
    if interval == "month":
        gaps = _contiguous_month_ranges(list(missing))
    elif interval == "week":
        gaps = _contiguous_week_ranges(list(missing))
    else:
        gaps = _contiguous_calendar_ranges(list(missing))
    return gaps


def symbol_api_chunks(
    existing: pd.DataFrame,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
) -> list[tuple[date, date]]:
    """API date windows (max Upstox width) for units missing from ``existing``."""
    missing = missing_symbol_calendar_units(existing, start, end, interval=interval)
    if not missing and not (existing.empty or "date" not in existing.columns):
        return []
    if existing.empty or "date" not in existing.columns:
        return _interval_chunks(interval, start, end)
    chunks: list[tuple[date, date]] = []
    for gap_start, gap_end in gap_ranges_from_missing_units(missing, interval=interval):
        clipped_start = max(gap_start, start)
        clipped_end = min(gap_end, end)
        if clipped_start <= clipped_end:
            chunks.extend(_interval_chunks(interval, clipped_start, clipped_end))
    return chunks


def symbol_fetch_ranges(
    existing: pd.DataFrame,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
) -> list[tuple[date, date]]:
    """Calendar ranges that still need API history for ``interval`` (day/month/week)."""
    return symbol_api_chunks(existing, start, end, interval=interval)


def planned_http_windows(
    cache_root: Path,
    instrument_key: str,
    chunks: Sequence[tuple[date, date]],
    *,
    interval: CandleInterval = "day",
    refresh_cache: bool = False,
) -> list[tuple[date, date, date, date]]:
    """List API calls as ``(gap_from, gap_to, fetch_from, fetch_to)``; skip gzip-backed windows."""
    planned: list[tuple[date, date, date, date]] = []
    for a, b in chunks:
        fetch_from, fetch_to = expand_fetch_window(a, b, interval=interval)
        if not refresh_cache and read_json_cache(
            candle_api_cache_path(cache_root, instrument_key, fetch_to, fetch_from, interval=interval)
        ) is not None:
            continue
        planned.append((a, b, fetch_from, fetch_to))
    return planned


def plan_symbol_sync(
    cache_root: Path,
    instrument_key: str,
    existing: pd.DataFrame,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
    refresh_cache: bool = False,
) -> dict[str, Any]:
    """Plan missing units and HTTP windows before any API calls."""
    missing = missing_symbol_calendar_units(existing, start, end, interval=interval)
    chunks = symbol_api_chunks(existing, start, end, interval=interval)
    http = planned_http_windows(
        cache_root, instrument_key, chunks, interval=interval, refresh_cache=refresh_cache
    )
    return {
        "missing_units": missing,
        "n_missing_units": len(missing),
        "api_chunks": chunks,
        "n_api_chunks": len(chunks),
        "http_windows": http,
        "n_http_calls": len(http),
        "max_inclusive_days": max_inclusive_calendar_days(interval),
    }


def estimate_symbol_http_calls(
    cache_root: Path,
    instrument_key: str,
    chunks: Sequence[tuple[date, date]],
    *,
    interval: CandleInterval = "day",
    refresh_cache: bool = False,
) -> int:
    if refresh_cache:
        return len(chunks)
    return len(
        planned_http_windows(
            cache_root, instrument_key, chunks, interval=interval, refresh_cache=refresh_cache
        )
    )


@dataclass
class UpstoxClient:
    access_token: str | None = None
    cache_dir: Path = field(default_factory=lambda: UPSTOX_CACHE_DIR)
    use_cache: bool = True
    instruments_ttl_hours: float = 24.0
    rate_limiter: UpstoxRateLimiter = field(default_factory=UpstoxRateLimiter.from_env)
    http_max_attempts: int = UPSTOX_HTTP_MAX_ATTEMPTS
    _symbol_to_key: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.access_token is None:
            self.access_token = upstox_access_token()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            raise UpstoxError(
                "Set UPSTOX_ACCESS_TOKEN or UPSTOX_API_KEY in the environment (.env)."
            )
        return {"Accept": "application/json", "Authorization": f"Bearer {self.access_token}"}

    def _http_get(self, url: str, *, timeout: float = 60.0, auth: bool = True) -> requests.Response:
        headers = self._auth_headers() if auth else {"Accept": "application/json"}
        last: requests.Response | None = None
        for attempt in range(self.http_max_attempts):
            self.rate_limiter.acquire()
            last = requests.get(url, headers=headers, timeout=timeout)
            if last.status_code != 429:
                return last
            retry_after = last.headers.get("Retry-After")
            if retry_after:
                try:
                    pause = float(retry_after)
                except ValueError:
                    pause = 30.0
            else:
                pause = min(120.0, 2.0**attempt * 2.0)
            time.sleep(pause)
        code = last.status_code if last is not None else "?"
        raise UpstoxError(f"HTTP {code} rate limited after {self.http_max_attempts} tries: {url}")

    def load_nse_eq_map(self, *, refresh: bool = False) -> dict[str, str]:
        if self._symbol_to_key and not refresh:
            return self._symbol_to_key
        path = instruments_map_path(self.cache_dir)
        if self.use_cache and not refresh and path.is_file():
            age_h = (time.time() - path.stat().st_mtime) / 3600.0
            if age_h <= self.instruments_ttl_hours:
                cached = read_json_cache(path)
                if cached and isinstance(cached.get("symbols"), dict):
                    self._symbol_to_key = {k.upper(): v for k, v in cached["symbols"].items()}
                    return self._symbol_to_key

        resp = self._http_get(NSE_INSTRUMENTS_URL, timeout=120.0, auth=False)
        if resp.status_code >= 400:
            raise UpstoxError(f"instruments download HTTP {resp.status_code}")
        raw = gzip.decompress(resp.content)
        items = json.loads(raw)
        mapping: dict[str, str] = {}
        for item in items:
            if item.get("segment") != "NSE_EQ" or item.get("instrument_type") != "EQ":
                continue
            sym = str(item.get("trading_symbol", "")).strip().upper()
            key = item.get("instrument_key")
            if sym and key:
                mapping[sym] = str(key)
        self._symbol_to_key = mapping
        if self.use_cache:
            write_json_cache(
                path,
                {
                    "fetched_at": datetime.utcnow().isoformat(),
                    "n_symbols": len(mapping),
                    "symbols": mapping,
                },
            )
        return mapping

    def fetch_candles(
        self,
        instrument_key: str,
        from_d: date,
        to_d: date,
        *,
        interval: CandleInterval = "day",
        refresh_cache: bool = False,
    ) -> list[list[Any]]:
        cache_path = candle_api_cache_path(
            self.cache_dir, instrument_key, to_d, from_d, interval=interval
        )
        if self.use_cache and not refresh_cache:
            cached = read_json_cache(cache_path)
            if cached is not None:
                data = cached.get("data", {})
                candles = data.get("candles", [])
                return candles if isinstance(candles, list) else []

        enc_key = quote(instrument_key, safe="")
        url = (
            f"{UPSTOX_BASE_URL}/historical-candle/{enc_key}/{interval}/"
            f"{to_d.isoformat()}/{from_d.isoformat()}"
        )
        resp = self._http_get(url, timeout=60.0)
        if resp.status_code == 404:
            body: dict[str, Any] = {"status": "success", "data": {"candles": []}}
        elif resp.status_code >= 400:
            raise UpstoxError(f"HTTP {resp.status_code} {url}: {resp.text[:400]}")
        else:
            body = resp.json()
        if self.use_cache:
            write_json_cache(cache_path, body)
        data = body.get("data", {})
        candles = data.get("candles", [])
        return candles if isinstance(candles, list) else []

    def sync_symbol(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        interval: CandleInterval = "day",
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        sym = symbol.strip().upper()
        imap = self.load_nse_eq_map()
        inst = imap.get(sym)
        if not inst:
            return {"symbol": sym, "status": "unknown_symbol", "rows": 0, "interval": interval}
        out_path = symbol_history_path(self.cache_dir, sym, interval=interval)
        existing = pd.DataFrame()
        if out_path.is_file() and not refresh_cache:
            existing = pd.read_parquet(out_path)
            if "date" in existing.columns:
                existing["date"] = pd.to_datetime(existing["date"]).dt.date

        ranges_to_fetch = symbol_api_chunks(existing, start, end, interval=interval)
        if not ranges_to_fetch and not existing.empty:
            return {
                "symbol": sym,
                "status": "cached",
                "rows": len(existing),
                "path": str(out_path),
                "api_calls": 0,
                "interval": interval,
            }

        frames = [existing] if not existing.empty else []
        api_calls = 0
        for a, b in ranges_to_fetch:
            fetch_from, fetch_to = expand_fetch_window(a, b, interval=interval)
            if not refresh_cache and read_json_cache(
                candle_api_cache_path(
                    self.cache_dir, inst, fetch_to, fetch_from, interval=interval
                )
            ) is None:
                api_calls += 1
            candles = self.fetch_candles(
                inst, fetch_from, fetch_to, interval=interval, refresh_cache=refresh_cache
            )
            chunk = candles_to_dataframe(candles, sym)
            if not chunk.empty:
                frames.append(chunk)

        if not frames:
            return {"symbol": sym, "status": "no_rows", "rows": 0, "path": str(out_path)}

        merged = pd.concat(frames, ignore_index=True)
        merged["date"] = pd.to_datetime(merged["date"]).dt.date
        merged = merged.sort_values("date").drop_duplicates("date", keep="last")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pandas(merged, preserve_index=False), out_path, compression="snappy")
        return {
            "symbol": sym,
            "status": "updated",
            "rows": len(merged),
            "path": str(out_path),
            "first": str(merged["date"].min()),
            "last": str(merged["date"].max()),
            "api_calls": api_calls,
            "interval": interval,
        }

    def materialize_day(self, parquet_root: Path, day: date, *, overwrite: bool = False) -> dict[str, Any]:
        path = nse_cm_parquet_path(parquet_root, day)
        if not overwrite:
            ok, nrows = parquet_day_is_valid(path, day)
            if ok:
                return {"date": day, "status": "skipped_valid_parquet", "rows": nrows, "path": str(path)}
        return {"date": day, "status": "use_materialize_range", "rows": 0, "path": str(path)}


def materialize_nse_cm_range(
    parquet_root: Path,
    cache_root: Path,
    start: date,
    end: date,
    *,
    only_days: set[date] | None = None,
    overwrite: bool = False,
    show_progress: bool = True,
) -> pd.DataFrame:
    """One pass over per-symbol Upstox cache → daily ``nse_cm_*.parquet`` files."""
    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    sym_dir = cache_root / "symbols" / "nse"
    if not sym_dir.is_dir():
        raise UpstoxError(f"No Upstox symbol cache at {sym_dir}")

    parts: list[pd.DataFrame] = []
    paths = sorted(sym_dir.glob("*.parquet"))
    path_iter: Iterable[Path] = paths
    if show_progress and tqdm is not None:
        path_iter = tqdm(paths, desc="Load symbol caches", unit="symbol")
    for sp in path_iter:
        df = pd.read_parquet(sp)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        if only_days is not None:
            df = df[df["date"].isin(only_days)]
        if not df.empty:
            parts.append(df)

    if not parts:
        return pd.DataFrame(columns=["date", "status", "rows", "path"])

    full = pd.concat(parts, ignore_index=True)
    rows: list[dict[str, Any]] = []
    groups = list(full.groupby("date", sort=True))
    grp_iter: Iterable[Any] = groups
    if show_progress and tqdm is not None:
        grp_iter = tqdm(groups, desc="Write nse_cm parquets", unit="day")
    for day, g in grp_iter:
        day = pd.Timestamp(day).date()
        if only_days is not None and day not in only_days:
            continue
        path = nse_cm_parquet_path(parquet_root, day)
        if not overwrite:
            ok, nrows = parquet_day_is_valid(path, day)
            if ok:
                rows.append({"date": day, "status": "skipped_valid_parquet", "rows": nrows, "path": str(path)})
                continue
        ok, reason = validate_stock_dataframe(g, day)
        if not ok:
            rows.append({"date": day, "status": f"invalid:{reason}", "rows": 0, "path": str(path)})
            continue
        nrow = atomic_write_parquet(g, path)
        rows.append({"date": day, "status": "written", "rows": nrow, "path": str(path)})
    return pd.DataFrame(rows)


def list_cached_symbols(cache_root: Path, *, interval: CandleInterval = "day") -> list[str]:
    sub = "nse" if interval == "day" else f"nse_{interval}"
    d = cache_root / "symbols" / sub
    if not d.is_dir():
        return []
    return sorted(p.stem.upper() for p in d.glob("*.parquet"))


def latest_monthly_cache_date(cache_root: Path) -> date | None:
    """Latest bar date across per-symbol monthly Upstox symbol parquets."""
    d = cache_root / "symbols" / "nse_month"
    if not d.is_dir():
        return None
    latest: date | None = None
    for path in d.glob("*.parquet"):
        try:
            df = pd.read_parquet(path, columns=["date"])
            if df.empty:
                continue
            mx = pd.to_datetime(df["date"]).max()
            if pd.isna(mx):
                continue
            bar = mx.date()
            if latest is None or bar > latest:
                latest = bar
        except Exception:
            continue
    return latest


def append_symbol_manifest(row: Mapping[str, Any], path: Path = UPSTOX_SYMBOL_MANIFEST_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([row])
    if path.is_file():
        prev = pd.read_csv(path)
        frame = pd.concat([prev, frame], ignore_index=True)
        frame = frame.drop_duplicates(subset=["symbol"], keep="last")
    frame.to_csv(path, index=False)


def missing_parquet_weekdays(parquet_root: Path, start: date, end: date) -> list[date]:
    """Weekdays in ``[start,end]`` without a valid local ``nse_cm_YYYYMMDD.parquet``."""
    from quant.data.tejhq import iter_weekdays, day_has_valid_parquet

    return [d for d in iter_weekdays(start, end) if not day_has_valid_parquet(parquet_root, d)]


def plan_upstox_universe(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
    client: UpstoxClient | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
) -> dict[str, Any]:
    """Aggregate per-symbol plans: missing units and HTTP call count."""
    client = client or UpstoxClient()
    imap = client.load_nse_eq_map()
    syms = [s.upper() for s in (symbols or sorted(imap))]
    if max_symbols:
        syms = syms[: int(max_symbols)]

    sym_plans: list[dict[str, Any]] = []
    total_http = 0
    all_missing: set[date] = set()
    for sym in syms:
        inst = imap.get(sym)
        if not inst:
            sym_plans.append({"symbol": sym, "status": "unknown_symbol", "n_http_calls": 0})
            continue
        path = symbol_history_path(client.cache_dir, sym, interval=interval)
        existing = pd.DataFrame()
        if path.is_file():
            existing = pd.read_parquet(path)
        plan = plan_symbol_sync(
            client.cache_dir, inst, existing, start, end, interval=interval, refresh_cache=refresh_cache
        )
        total_http += int(plan["n_http_calls"])
        all_missing.update(plan["missing_units"])
        sym_plans.append(
            {
                "symbol": sym,
                "status": "needs_fetch" if plan["n_missing_units"] else "cached",
                "n_missing_units": plan["n_missing_units"],
                "n_api_chunks": plan["n_api_chunks"],
                "n_http_calls": plan["n_http_calls"],
            }
        )

    missing_days_parquet: list[date] = []
    if interval == "day":
        missing_days_parquet = missing_parquet_weekdays(parquet_root, start, end)

    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "interval": interval,
        "max_inclusive_days": max_inclusive_calendar_days(interval),
        "n_symbols": len(syms),
        "n_http_calls_total": total_http,
        "missing_unit_dates_union": sorted(all_missing),
        "n_missing_units_union": len(all_missing),
        "missing_parquet_weekdays": missing_days_parquet,
        "symbols": sym_plans,
    }


def print_upstox_sync_plan(plan: Mapping[str, Any], *, list_missing_dates: bool = False) -> None:
    """Human-readable plan emitted before HTTP sync."""
    print(
        f"Plan {plan['start']} .. {plan['end']} interval={plan['interval']} "
        f"(max {plan['max_inclusive_days']}d/request)"
    )
    print(
        f"  symbols={plan['n_symbols']} missing_units_union={plan['n_missing_units_union']} "
        f"http_calls={plan['n_http_calls_total']}"
    )
    if plan.get("missing_parquet_weekdays") is not None:
        mp = plan["missing_parquet_weekdays"]
        print(f"  nse_cm parquets missing (weekdays): {len(mp)}")
        if list_missing_dates and mp:
            for d in mp[:50]:
                print(f"    {d.isoformat()}")
            if len(mp) > 50:
                print(f"    ... +{len(mp) - 50} more")
    needing = [s for s in plan["symbols"] if s.get("n_http_calls", 0) > 0]
    if needing:
        print(f"  symbols needing HTTP: {len(needing)}")
        for row in needing[:20]:
            print(
                f"    {row['symbol']}: missing_units={row.get('n_missing_units', 0)} "
                f"api_chunks={row.get('n_api_chunks', 0)} http={row.get('n_http_calls', 0)}"
            )
        if len(needing) > 20:
            print(f"    ... +{len(needing) - 20} more symbols")


def sync_upstox_universe(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
    client: UpstoxClient | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
    materialize: bool | None = None,
    materialize_only_missing: bool = True,
    overwrite_parquet: bool = False,
    dry_run: bool = False,
    show_progress: bool = True,
    plan_before: bool = True,
    list_missing_dates: bool = False,
) -> pd.DataFrame:
    """Fetch per-symbol history (cached). Materializes ``nse_cm_*`` only for ``interval=day``."""
    if materialize is None:
        materialize = interval == "day"
    client = client or UpstoxClient()
    imap = client.load_nse_eq_map()
    syms = [s.upper() for s in (symbols or sorted(imap))]
    if max_symbols:
        syms = syms[: int(max_symbols)]

    if not dry_run and plan_before:
        plan = plan_upstox_universe(
            parquet_root,
            start,
            end,
            interval=interval,
            client=client,
            symbols=syms,
            refresh_cache=refresh_cache,
        )
        print_upstox_sync_plan(plan, list_missing_dates=list_missing_dates)

    missing_days: set[date] | None = None
    if materialize and materialize_only_missing and not overwrite_parquet:
        missing_days = set(missing_parquet_weekdays(parquet_root, start, end))

    if dry_run:
        aggregate = plan_upstox_universe(
            parquet_root,
            start,
            end,
            interval=interval,
            client=client,
            symbols=syms,
            refresh_cache=refresh_cache,
        )
        planned = [
            {**row, "api_calls_est": row.get("n_http_calls", 0)}
            for row in aggregate["symbols"]
        ]
        planned.append(
            {
                "status": "plan_summary",
                "n_http_calls_total": aggregate["n_http_calls_total"],
                "n_missing_units_union": aggregate["n_missing_units_union"],
                "max_inclusive_days": aggregate["max_inclusive_days"],
            }
        )
        return pd.DataFrame(planned)

    try:
        from tqdm.auto import tqdm as _tqdm
    except ImportError:  # pragma: no cover
        _tqdm = None  # type: ignore

    sym_iter: Iterable[str] = syms
    if show_progress and _tqdm is not None:
        sym_iter = _tqdm(syms, desc=f"Upstox sync ({interval})", unit="sym")

    rows: list[dict[str, Any]] = []
    api_calls = 0
    for sym in sym_iter:
        try:
            r = client.sync_symbol(sym, start, end, interval=interval, refresh_cache=refresh_cache)
            rows.append(r)
            api_calls += int(r.get("api_calls") or 0)
            append_symbol_manifest({**r, "fetched_at": datetime.utcnow().isoformat()})
        except UpstoxError as exc:
            err = {"symbol": sym, "status": f"error:{exc}", "rows": 0, "api_calls": 0}
            rows.append(err)
            append_symbol_manifest({**err, "fetched_at": datetime.utcnow().isoformat()})

    if materialize:
        if missing_days is not None and not missing_days and not overwrite_parquet:
            rows.append(
                {
                    "status": "materialize_skipped",
                    "reason": "all_weekdays_present",
                    "missing_days": 0,
                    "api_calls_total": api_calls,
                }
            )
        else:
            mat = materialize_nse_cm_range(
                parquet_root,
                client.cache_dir,
                start,
                end,
                only_days=missing_days,
                overwrite=overwrite_parquet,
                show_progress=show_progress,
            )
            if not mat.empty:
                rows.extend(mat.to_dict("records"))
            rows.append({"status": "sync_summary", "api_calls_total": api_calls})

    sym_df = pd.DataFrame([r for r in rows if "symbol" in r])
    return sym_df if not sym_df.empty else pd.DataFrame(rows)


def sync_upstox_incremental(
    parquet_root: Path,
    *,
    client: UpstoxClient | None = None,
    lookback_years: int = 15,
    start: date | None = None,
    end: date | None = None,
    **kwargs: Any,
) -> pd.DataFrame:
    client = client or UpstoxClient()
    end_d = end or date.today()
    start_d = start or (end_d - timedelta(days=lookback_years * 365))
    latest = latest_local_parquet_date(parquet_root)
    if latest is not None and start is None:
        start_d = min(start_d, latest - timedelta(days=30))
    return sync_upstox_universe(parquet_root, start_d, end_d, client=client, **kwargs)


def sync_upstox_for_monthly_backtest(
    parquet_root: Path,
    *,
    date_start: date,
    date_end: date | None = None,
    interval: CandleInterval = "month",
    client: UpstoxClient | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
    materialize_only_missing: bool = True,
    overwrite_parquet: bool = False,
    dry_run: bool = False,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Sync absent per-symbol history for institutional monthly backtest (default: ``month`` candles)."""
    end_d = date_end or date.today()
    client = client or UpstoxClient()
    df = sync_upstox_universe(
        parquet_root,
        date_start,
        end_d,
        interval=interval,
        client=client,
        symbols=symbols,
        max_symbols=max_symbols,
        refresh_cache=refresh_cache,
        materialize_only_missing=materialize_only_missing,
        overwrite_parquet=overwrite_parquet,
        dry_run=dry_run,
        show_progress=show_progress,
    )
    missing = (
        missing_parquet_weekdays(parquet_root, date_start, end_d)
        if interval == "day"
        else []
    )
    sym_rows = df[df["symbol"].notna()] if not df.empty and "symbol" in df.columns else df
    statuses = sym_rows["status"].value_counts().to_dict() if not sym_rows.empty and "status" in sym_rows.columns else {}
    return {
        "start": date_start.isoformat(),
        "end": end_d.isoformat(),
        "interval": interval,
        "max_inclusive_days": max_inclusive_calendar_days(interval),
        "missing_weekdays_after": len(missing),
        "monthly_cache_latest": (
            str(latest_monthly_cache_date(client.cache_dir))
            if interval == "month"
            else None
        ),
        "symbol_status_counts": statuses,
        "n_symbols": int(len(sym_rows)) if not sym_rows.empty else 0,
    }