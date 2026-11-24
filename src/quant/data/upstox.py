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
    UPSTOX_HTTP_MAX_ATTEMPTS,
    UPSTOX_MIN_INTERVAL_S,
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
MAX_DAILY_RANGE_DAYS = 364
MAX_MONTHLY_RANGE_DAYS = 364 * 10 - 1  # Upstox: up to ~10y of month candles per call
CandleInterval = str  # day | month


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


def symbol_history_path(cache_root: Path, symbol: str) -> Path:
    return cache_root / "symbols" / "nse" / f"{symbol.upper()}.parquet"


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


def _year_chunks(start: date, end: date) -> list[tuple[date, date]]:
    return _range_chunks(start, end, MAX_DAILY_RANGE_DAYS)


def _range_chunks(start: date, end: date, max_span_days: int) -> list[tuple[date, date]]:
    """Split [start, end] into widest contiguous spans allowed by the API."""
    chunks: list[tuple[date, date]] = []
    d = start
    while d <= end:
        chunk_end = min(d + timedelta(days=max_span_days), end)
        chunks.append((d, chunk_end))
        d = chunk_end + timedelta(days=1)
    return chunks


def _interval_chunks(interval: CandleInterval, start: date, end: date) -> list[tuple[date, date]]:
    if interval == "month":
        return _range_chunks(start, end, MAX_MONTHLY_RANGE_DAYS)
    return _year_chunks(start, end)


def symbol_fetch_ranges(
    existing: pd.DataFrame,
    start: date,
    end: date,
    *,
    interval: CandleInterval = "day",
) -> list[tuple[date, date]]:
    """Calendar ranges that still need API history for ``interval`` (day/month)."""
    if existing.empty or "date" not in existing.columns:
        return _interval_chunks(interval, start, end)
    have = existing.copy()
    have["date"] = pd.to_datetime(have["date"]).dt.date
    have_max = max(have["date"])
    have_min = min(have["date"])
    if have_min <= start and have_max >= end:
        return []
    ranges: list[tuple[date, date]] = []
    if have_min > start:
        ranges.append((start, have_min - timedelta(days=1)))
    if have_max < end:
        ranges.append((have_max + timedelta(days=1), end))
    chunks: list[tuple[date, date]] = []
    for a, b in ranges:
        if a <= b:
            chunks.extend(_interval_chunks(interval, a, b))
    return chunks


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
    n = 0
    for a, b in chunks:
        if read_json_cache(candle_api_cache_path(cache_root, instrument_key, b, a, interval=interval)) is None:
            n += 1
    return n


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
        refresh_cache: bool = False,
    ) -> dict[str, Any]:
        sym = symbol.strip().upper()
        imap = self.load_nse_eq_map()
        inst = imap.get(sym)
        if not inst:
            return {"symbol": sym, "status": "unknown_symbol", "rows": 0}
        out_path = symbol_history_path(self.cache_dir, sym)
        existing = pd.DataFrame()
        if out_path.is_file() and not refresh_cache:
            existing = pd.read_parquet(out_path)
            if "date" in existing.columns:
                existing["date"] = pd.to_datetime(existing["date"]).dt.date

        ranges_to_fetch = symbol_fetch_ranges(existing, start, end, interval="day")
        if not ranges_to_fetch and not existing.empty:
            return {
                "symbol": sym,
                "status": "cached",
                "rows": len(existing),
                "path": str(out_path),
                "api_calls": 0,
            }

        frames = [existing] if not existing.empty else []
        api_calls = 0
        for a, b in ranges_to_fetch:
            if not refresh_cache and read_json_cache(
                candle_api_cache_path(self.cache_dir, inst, b, a, interval="day")
            ) is None:
                api_calls += 1
            candles = self.fetch_candles(inst, a, b, interval="day", refresh_cache=refresh_cache)
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


def list_cached_symbols(cache_root: Path) -> list[str]:
    d = cache_root / "symbols" / "nse"
    if not d.is_dir():
        return []
    return sorted(p.stem.upper() for p in d.glob("*.parquet"))


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


def sync_upstox_universe(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    client: UpstoxClient | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
    materialize: bool = True,
    materialize_only_missing: bool = True,
    overwrite_parquet: bool = False,
    dry_run: bool = False,
    show_progress: bool = True,
) -> pd.DataFrame:
    """Fetch per-symbol day history (cached), then write ``nse_cm_YYYYMMDD.parquet`` files."""
    client = client or UpstoxClient()
    imap = client.load_nse_eq_map()
    syms = [s.upper() for s in (symbols or sorted(imap))]
    if max_symbols:
        syms = syms[: int(max_symbols)]

    missing_days: set[date] | None = None
    if materialize and materialize_only_missing and not overwrite_parquet:
        missing_days = set(missing_parquet_weekdays(parquet_root, start, end))

    if dry_run:
        planned: list[dict[str, Any]] = []
        for sym in syms:
            inst = imap.get(sym)
            if not inst:
                planned.append({"symbol": sym, "status": "unknown_symbol", "api_calls_est": 0})
                continue
            path = symbol_history_path(client.cache_dir, sym)
            existing = pd.DataFrame()
            if path.is_file():
                existing = pd.read_parquet(path)
            chunks = symbol_fetch_ranges(existing, start, end, interval="day")
            planned.append(
                {
                    "symbol": sym,
                    "status": "would_sync" if chunks else "cached",
                    "api_calls_est": estimate_symbol_http_calls(
                        client.cache_dir, inst, chunks, interval="day", refresh_cache=refresh_cache
                    ),
                }
            )
        return pd.DataFrame(planned)

    try:
        from tqdm.auto import tqdm as _tqdm
    except ImportError:  # pragma: no cover
        _tqdm = None  # type: ignore

    sym_iter: Iterable[str] = syms
    if show_progress and _tqdm is not None:
        sym_iter = _tqdm(syms, desc="Upstox symbol sync", unit="sym")

    rows: list[dict[str, Any]] = []
    api_calls = 0
    for sym in sym_iter:
        try:
            r = client.sync_symbol(sym, start, end, refresh_cache=refresh_cache)
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
    client: UpstoxClient | None = None,
    symbols: Sequence[str] | None = None,
    max_symbols: int | None = None,
    refresh_cache: bool = False,
    materialize_only_missing: bool = True,
    overwrite_parquet: bool = False,
    dry_run: bool = False,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Sync only absent symbol history / daily parquets for a monthly backtest window."""
    end_d = date_end or date.today()
    df = sync_upstox_universe(
        parquet_root,
        date_start,
        end_d,
        client=client,
        symbols=symbols,
        max_symbols=max_symbols,
        refresh_cache=refresh_cache,
        materialize=True,
        materialize_only_missing=materialize_only_missing,
        overwrite_parquet=overwrite_parquet,
        dry_run=dry_run,
        show_progress=show_progress,
    )
    missing = missing_parquet_weekdays(parquet_root, date_start, end_d)
    sym_rows = df[df["symbol"].notna()] if not df.empty and "symbol" in df.columns else df
    statuses = sym_rows["status"].value_counts().to_dict() if not sym_rows.empty and "status" in sym_rows.columns else {}
    return {
        "start": date_start.isoformat(),
        "end": end_d.isoformat(),
        "missing_weekdays_after": len(missing),
        "symbol_status_counts": statuses,
        "n_symbols": int(len(sym_rows)) if not sym_rows.empty else 0,
    }