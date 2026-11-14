"""TejHQ (api.tejhq.dev) client and NSE daily parquet sync with on-disk cache."""

from __future__ import annotations

import gzip
import json
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import requests

from quant.config import TEJHQ_CACHE_DIR, TEJHQ_MANIFEST_PATH

TEJHQ_BASE_URL = "https://api.tejhq.dev"

STOCK_COLUMNS = [
    "date",
    "symbol",
    "open",
    "high",
    "low",
    "close",
    "volume",
]

OPTIONAL_STOCK_COLUMNS = [
    "turnover",
    "deliv_qty",
    "deliv_per",
    "series",
    "prev_close",
]


class TejHQError(RuntimeError):
    pass


def tejhq_api_key() -> str | None:
    return os.getenv("TEJHQ_API_KEY") or os.getenv("TEJ_API_KEY")


def nse_cm_parquet_path(parquet_root: Path, day: date) -> Path:
    year_dir = parquet_root / str(day.year)
    return year_dir / f"nse_cm_{day:%Y%m%d}.parquet"


def snapshot_cache_path(cache_root: Path, exchange: str, day: date) -> Path:
    return (
        cache_root
        / "snapshots"
        / exchange.lower()
        / str(day.year)
        / f"snapshot_{day:%Y%m%d}.json.gz"
    )


def series_cache_path(
    cache_root: Path,
    endpoint: str,
    exchange: str,
    symbol: str,
    from_: date | None,
    to: date | None,
) -> Path:
    f = from_.isoformat() if from_ else "none"
    t = to.isoformat() if to else "none"
    name = f"{symbol.upper()}_{f}_{t}.json.gz"
    return cache_root / endpoint / exchange.lower() / name


def _unwrap_payload(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    data = body.get("data", body)
    if not isinstance(data, list):
        raise TejHQError(f"Unexpected API payload shape: {type(data)!r}")
    return data


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


def snapshot_rows_to_dataframe(
    rows: Sequence[Mapping[str, Any]],
    trade_date: date,
    *,
    series: str | None = "EQ",
) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(columns=STOCK_COLUMNS + OPTIONAL_STOCK_COLUMNS)

    df = pd.DataFrame(rows)
    if series is not None and "series" in df.columns:
        df = df.loc[df["series"].astype(str).str.upper().eq(series.upper())]

    out = pd.DataFrame(
        {
            "date": trade_date,
            "symbol": df["symbol"].astype(str).str.strip(),
            "open": pd.to_numeric(df["open"], errors="coerce"),
            "high": pd.to_numeric(df["high"], errors="coerce"),
            "low": pd.to_numeric(df["low"], errors="coerce"),
            "close": pd.to_numeric(df["close"], errors="coerce"),
            "volume": pd.to_numeric(df["volume"], errors="coerce"),
            "turnover": pd.to_numeric(df["turnover"], errors="coerce")
            if "turnover" in df.columns
            else float("nan"),
            "series": df["series"].astype(str).str.upper()
            if "series" in df.columns
            else "",
            "prev_close": pd.to_numeric(df["prev_close"], errors="coerce")
            if "prev_close" in df.columns
            else float("nan"),
        }
    )
    out = out.dropna(subset=["symbol", "close"])
    out = out.drop_duplicates(subset=["date", "symbol"], keep="last")
    cols = STOCK_COLUMNS + [c for c in OPTIONAL_STOCK_COLUMNS if c in out.columns]
    return out[cols]


def validate_stock_dataframe(df: pd.DataFrame, expected_day: date) -> tuple[bool, str]:
    if df.empty:
        return False, "empty_dataframe"
    missing = [c for c in STOCK_COLUMNS if c not in df.columns]
    if missing:
        return False, f"missing_columns:{missing}"
    if not (df["date"] == expected_day).all():
        return False, "wrong_date"
    if df["symbol"].isna().any():
        return False, "invalid_symbols"
    numeric = ["open", "high", "low", "close", "volume"]
    if df[numeric].isna().all(axis=1).any():
        return False, "rows_with_all_numeric_values_null"
    return True, "ok"


def parquet_day_is_valid(path: Path, expected_day: date) -> tuple[bool, int]:
    if not path.is_file():
        return False, 0
    try:
        df = pq.read_table(path).to_pandas()
        ok, _ = validate_stock_dataframe(df, expected_day)
        return ok, len(df)
    except Exception:
        return False, 0


def atomic_write_parquet(df: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    tmp.unlink(missing_ok=True)
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp, compression="snappy")
    tmp.replace(path)
    return len(df)


def append_manifest(row: Mapping[str, Any]) -> None:
    TEJHQ_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame([row])
    if TEJHQ_MANIFEST_PATH.is_file():
        prev = pd.read_csv(TEJHQ_MANIFEST_PATH)
        frame = pd.concat([prev, frame], ignore_index=True)
        frame = frame.drop_duplicates(subset=["date"], keep="last")
    frame.to_csv(TEJHQ_MANIFEST_PATH, index=False)


@dataclass
class ManifestBatch:
    """Buffer manifest rows to avoid re-reading the CSV on every trading day."""

    flush_every: int = 50
    path: Path = field(default_factory=lambda: TEJHQ_MANIFEST_PATH)
    _rows: list[dict[str, Any]] = field(default_factory=list)

    def add(self, row: Mapping[str, Any]) -> None:
        self._rows.append(dict(row))
        if len(self._rows) >= self.flush_every:
            self.flush()

    def flush(self) -> None:
        if not self._rows:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        chunk = pd.DataFrame(self._rows)
        if self.path.is_file():
            prev = pd.read_csv(self.path)
            chunk = pd.concat([prev, chunk], ignore_index=True)
        chunk = chunk.drop_duplicates(subset=["date"], keep="last")
        chunk.to_csv(self.path, index=False)
        self._rows.clear()


def latest_local_parquet_date(parquet_root: Path) -> date | None:
    dates: list[date] = []
    for path in parquet_root.rglob("nse_cm_*.parquet"):
        stem = path.stem
        if len(stem) != 15 or not stem.startswith("nse_cm_"):
            continue
        try:
            dates.append(datetime.strptime(stem[7:], "%Y%m%d").date())
        except ValueError:
            continue
    return max(dates) if dates else None


def parquet_candidate_paths(parquet_root: Path, day: date) -> list[Path]:
    """Canonical TejHQ layout plus legacy hive-style folders."""
    return [
        nse_cm_parquet_path(parquet_root, day),
        parquet_root / f"year={day.year}" / f"nse_cm_{day:%Y%m%d}.parquet",
    ]


def day_has_valid_parquet(parquet_root: Path, day: date) -> bool:
    for path in parquet_candidate_paths(parquet_root, day):
        ok, _ = parquet_day_is_valid(path, day)
        if ok:
            return True
    return False


def missing_weekdays(parquet_root: Path, start: date, end: date) -> list[date]:
    return [d for d in iter_weekdays(start, end) if not day_has_valid_parquet(parquet_root, d)]


def snapshot_cache_empty(cache_root: Path, day: date, exchange: str = "nse") -> bool | None:
    """True = cached empty snapshot (non-trading). None = no cache yet."""
    body = read_json_cache(snapshot_cache_path(cache_root, exchange, day))
    if body is None:
        return None
    data = body.get("data", body if isinstance(body, list) else [])
    if not isinstance(data, list):
        return None
    return len(data) == 0


def manifest_non_trading_dates(manifest_path: Path) -> set[date]:
    if not manifest_path.is_file():
        return set()
    df = pd.read_csv(manifest_path)
    if "date" not in df.columns or "status" not in df.columns:
        return set()
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return set(df.loc[df["status"] == "no_rows", "date"].tolist())


def classify_data_gaps(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    cache_root: Path | None = None,
    manifest_path: Path | None = None,
) -> dict[str, list[date]]:
    """
    Split missing weekdays into sync gaps vs likely non-trading (holidays).

    Non-trading: TejHQ returned no rows (manifest or empty snapshot cache).
    """
    cache_root = cache_root or TEJHQ_CACHE_DIR
    manifest_path = manifest_path or TEJHQ_MANIFEST_PATH
    missing = missing_weekdays(parquet_root, start, end)
    holidays: set[date] = manifest_non_trading_dates(manifest_path)

    gaps: list[date] = []
    unknown: list[date] = []

    for day in missing:
        if day in holidays:
            continue
        empty = snapshot_cache_empty(cache_root, day)
        if empty is True:
            continue
        if empty is None:
            unknown.append(day)
        gaps.append(day)

    return {
        "gaps": gaps,
        "unknown_never_fetched": unknown,
        "non_trading_skipped": sorted(holidays.intersection(missing)),
        "missing_weekdays_total": missing,
    }


def days_to_sync(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    exclude_holidays: bool = True,
) -> list[date]:
    """Weekdays needing work, skipping known holidays (manifest / empty snapshot cache)."""
    if exclude_holidays:
        info = classify_data_gaps(parquet_root, start, end)
        return sorted(set(info["gaps"]))
    return missing_weekdays(parquet_root, start, end)


def estimate_snapshot_api_calls(
    days: Sequence[date],
    cache_root: Path,
    *,
    refresh_cache: bool = False,
) -> int:
    """One TejHQ snapshot HTTP call per day without a gzip cache (no batch snapshot API)."""
    if refresh_cache:
        return len(days)
    n = 0
    for day in days:
        if read_json_cache(snapshot_cache_path(cache_root, "nse", day)) is None:
            n += 1
    return n


def gap_ranges(days: Sequence[date]) -> list[tuple[date, date, int]]:
    """Merge consecutive calendar dates into [start, end, count] ranges."""
    if not days:
        return []
    sorted_days = sorted(days)
    ranges: list[tuple[date, date, int]] = []
    start = prev = sorted_days[0]
    count = 1
    for d in sorted_days[1:]:
        if (d - prev).days <= 3:  # allow weekend between trading gaps
            count += 1
            prev = d
            continue
        ranges.append((start, prev, count))
        start = prev = d
        count = 1
    ranges.append((start, prev, count))
    return ranges


@dataclass
class TejHQClient:
    api_key: str | None = None
    base_url: str = TEJHQ_BASE_URL
    timeout: float = 60.0
    min_interval_s: float = 0.05
    cache_dir: Path = field(default_factory=lambda: TEJHQ_CACHE_DIR)
    use_cache: bool = True
    _last_request_at: float = 0.0

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = tejhq_api_key()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _headers(self, *, auth: bool) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if auth:
            if not self.api_key:
                raise TejHQError(
                    "This endpoint requires TEJHQ_API_KEY or TEJ_API_KEY in the environment."
                )
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self.min_interval_s - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        auth: bool = False,
        cache_path: Path | None = None,
        refresh_cache: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        if self.use_cache and cache_path and not refresh_cache:
            cached = read_json_cache(cache_path)
            if cached is not None:
                return cached, True

        self._throttle()
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"
        resp = requests.get(
            url,
            params=params,
            headers=self._headers(auth=auth),
            timeout=self.timeout,
        )
        if resp.status_code == 404:
            body: dict[str, Any] = {"data": []}
        elif resp.status_code >= 400:
            raise TejHQError(f"HTTP {resp.status_code} for {path}: {resp.text[:300]}")
        else:
            body = resp.json()

        if self.use_cache and cache_path:
            write_json_cache(cache_path, body)
        return body, False

    def status(self) -> dict[str, Any]:
        cache = self.cache_dir / "status.json.gz"
        body, _ = self._get("/v1/status", auth=False, cache_path=cache)
        return body

    def latest_trading_date(self) -> date | None:
        body = self.status()
        raw = body.get("trading_date")
        if not raw:
            return None
        return datetime.strptime(str(raw), "%Y-%m-%d").date()

    def snapshot(
        self,
        exchange: str,
        day: date,
        *,
        refresh_cache: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        cache = snapshot_cache_path(self.cache_dir, exchange, day)
        body, from_cache = self._get(
            f"/v1/snapshot/{exchange.lower()}",
            params={"date": day.isoformat()},
            auth=False,
            cache_path=cache,
            refresh_cache=refresh_cache,
        )
        source = "cache" if from_cache else "api"
        return _unwrap_payload(body), source

    def ohlcv(
        self,
        symbol: str,
        exchange: str = "nse",
        from_: date | None = None,
        to: date | None = None,
        *,
        refresh_cache: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if from_ is not None:
            params["from"] = from_.isoformat()
        if to is not None:
            params["to"] = to.isoformat()
        cache = series_cache_path(self.cache_dir, "ohlcv", exchange, symbol, from_, to)
        body, _ = self._get(
            f"/v1/ohlcv/{exchange.lower()}/{symbol.upper()}",
            params=params or None,
            auth=False,
            cache_path=cache,
            refresh_cache=refresh_cache,
        )
        return _unwrap_payload(body)

    def delivery(
        self,
        symbol: str,
        exchange: str = "nse",
        from_: date | None = None,
        to: date | None = None,
        *,
        refresh_cache: bool = False,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if from_ is not None:
            params["from"] = from_.isoformat()
        if to is not None:
            params["to"] = to.isoformat()
        cache = series_cache_path(self.cache_dir, "delivery", exchange, symbol, from_, to)
        body, _ = self._get(
            f"/v1/delivery/{exchange.lower()}/{symbol.upper()}",
            params=params or None,
            auth=True,
            cache_path=cache,
            refresh_cache=refresh_cache,
        )
        return _unwrap_payload(body)

    def fetch_snapshot_frame(
        self,
        day: date,
        *,
        exchange: str = "nse",
        series: str | None = "EQ",
        refresh_cache: bool = False,
    ) -> tuple[pd.DataFrame, str]:
        rows, source = self.snapshot(exchange, day, refresh_cache=refresh_cache)
        return snapshot_rows_to_dataframe(rows, day, series=series), source

    def _manifest_record(
        self,
        result: Mapping[str, Any],
        *,
        write_manifest: bool,
        manifest_batch: ManifestBatch | None,
    ) -> None:
        if not write_manifest:
            return
        row = {
            **dict(result),
            "parquet_path": result.get("path", ""),
            "fetched_at": datetime.utcnow().isoformat(),
        }
        if "rows" not in row and result.get("status") == "no_rows":
            row["rows"] = 0
        if manifest_batch is not None:
            manifest_batch.add(row)
        else:
            append_manifest(row)

    def write_nse_cm_parquet(
        self,
        day: date,
        parquet_root: Path,
        *,
        series: str | None = "EQ",
        overwrite: bool = False,
        refresh_cache: bool = False,
        write_manifest: bool = True,
        manifest_batch: ManifestBatch | None = None,
    ) -> dict[str, Any]:
        path = nse_cm_parquet_path(parquet_root, day)
        cache = snapshot_cache_path(self.cache_dir, "nse", day)
        if not overwrite:
            ok, nrows = parquet_day_is_valid(path, day)
            if ok:
                result = {
                    "date": day,
                    "status": "skipped_valid_parquet",
                    "source": "parquet",
                    "rows": nrows,
                    "path": str(path),
                    "cache_path": str(cache),
                }
                self._manifest_record(
                    result, write_manifest=write_manifest, manifest_batch=manifest_batch
                )
                return result

        source = "cache"
        df: pd.DataFrame
        if not refresh_cache:
            cached = read_json_cache(cache)
            if cached is not None:
                rows = _unwrap_payload(cached)
                df = snapshot_rows_to_dataframe(rows, day, series=series)
            else:
                df, source = self.fetch_snapshot_frame(
                    day,
                    exchange="nse",
                    series=series,
                    refresh_cache=False,
                )
        else:
            df, source = self.fetch_snapshot_frame(
                day,
                exchange="nse",
                series=series,
                refresh_cache=True,
            )
        if df.empty:
            result = {
                "date": day,
                "status": "no_rows",
                "source": source,
                "path": str(path),
                "cache_path": str(cache),
            }
            self._manifest_record(
                result, write_manifest=write_manifest, manifest_batch=manifest_batch
            )
            return result

        ok, reason = validate_stock_dataframe(df, day)
        if not ok:
            result = {
                "date": day,
                "status": f"invalid:{reason}",
                "source": source,
                "path": str(path),
                "cache_path": str(cache),
            }
            self._manifest_record(
                result, write_manifest=write_manifest, manifest_batch=manifest_batch
            )
            return result

        nrow = atomic_write_parquet(df, path)
        result = {
            "date": day,
            "status": "written",
            "source": source,
            "rows": nrow,
            "path": str(path),
            "cache_path": str(cache),
        }
        self._manifest_record(
            result, write_manifest=write_manifest, manifest_batch=manifest_batch
        )
        return result


def iter_weekdays(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


def sync_fill_all_gaps(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    client: TejHQClient | None = None,
    series: str | None = "EQ",
    overwrite: bool = False,
    refresh_cache: bool = False,
    exclude_holidays: bool = True,
    dry_run: bool = False,
    manifest_flush_every: int = 50,
) -> pd.DataFrame:
    """
    Fill missing weekdays using TejHQ full-market snapshots.

    TejHQ exposes ``/v1/snapshot/{exchange}?date=`` only (one date per call).
    Range params exist on ``/v1/ohlcv``, ``/v1/delivery``, and ``/v1/adjusted`` per
    symbol — not for universe-wide daily bhavcopy. This path minimizes calls by:

    - Skipping known holidays (manifest ``no_rows`` / empty snapshot cache)
    - Serving from gzip snapshot cache when parquet is missing
    - Batched manifest writes
    """
    client = client or TejHQClient()
    days = days_to_sync(parquet_root, start, end, exclude_holidays=exclude_holidays)
    api_calls = estimate_snapshot_api_calls(
        days, client.cache_dir, refresh_cache=refresh_cache
    )
    ranges = gap_ranges(days)

    print(
        f"Fill plan [{start} → {end}]: {len(days)} weekdays, "
        f"~{api_calls} snapshot HTTP calls, {len(ranges)} contiguous ranges"
    )
    if ranges:
        a, b, n = ranges[0]
        print(f"  first range: {a} → {b} ({n} days)")
        a, b, n = ranges[-1]
        print(f"  last range:  {a} → {b} ({n} days)")
    print(
        "  Note: per-symbol endpoints use from/to in one call; "
        "universe daily files use one snapshot per trading day."
    )

    if dry_run:
        return pd.DataFrame(
            {
                "date": days,
                "status": ["would_sync"] * len(days),
                "needs_api": [
                    read_json_cache(snapshot_cache_path(client.cache_dir, "nse", d)) is None
                    for d in days
                ],
            }
        )

    manifest = ManifestBatch(flush_every=manifest_flush_every)
    rows: list[dict[str, Any]] = []
    for i, day in enumerate(days, 1):
        try:
            rows.append(
                client.write_nse_cm_parquet(
                    day,
                    parquet_root,
                    series=series,
                    overwrite=overwrite,
                    refresh_cache=refresh_cache,
                    manifest_batch=manifest,
                )
            )
        except TejHQError as exc:
            err = {
                "date": day,
                "status": f"error:{exc}",
                "source": "api",
                "path": str(nse_cm_parquet_path(parquet_root, day)),
            }
            manifest.add(
                {
                    **err,
                    "parquet_path": err["path"],
                    "fetched_at": datetime.utcnow().isoformat(),
                    "rows": 0,
                }
            )
            rows.append(err)
        if i % 100 == 0:
            print(f"  progress {i}/{len(days)}", flush=True)
    manifest.flush()
    return pd.DataFrame(rows)


def sync_nse_cm_missing_days(
    parquet_root: Path,
    start: date,
    end: date,
    **kwargs: Any,
) -> pd.DataFrame:
    """Alias for :func:`sync_fill_all_gaps` (backward compatible)."""
    return sync_fill_all_gaps(parquet_root, start, end, **kwargs)


def sync_nse_cm_parquet_range(
    parquet_root: Path,
    start: date,
    end: date,
    *,
    client: TejHQClient | None = None,
    series: str | None = "EQ",
    overwrite: bool = False,
    refresh_cache: bool = False,
) -> pd.DataFrame:
    client = client or TejHQClient()
    rows: list[dict[str, Any]] = []
    for day in iter_weekdays(start, end):
        try:
            result = client.write_nse_cm_parquet(
                day,
                parquet_root,
                series=series,
                overwrite=overwrite,
                refresh_cache=refresh_cache,
            )
        except TejHQError as exc:
            path = nse_cm_parquet_path(parquet_root, day)
            result = {
                "date": day,
                "status": f"error:{exc}",
                "source": "api",
                "path": str(path),
            }
        rows.append(result)
    return pd.DataFrame(rows)