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

from quant.config import UPSTOX_CACHE_DIR, UPSTOX_SYMBOL_MANIFEST_PATH
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


class UpstoxError(RuntimeError):
    pass


def upstox_access_token() -> str | None:
    return os.getenv("UPSTOX_ACCESS_TOKEN") or os.getenv("UPSTOX_API_KEY")


def instruments_map_path(cache_root: Path) -> Path:
    return cache_root / "instruments" / "nse_eq_map.json.gz"


def symbol_history_path(cache_root: Path, symbol: str) -> Path:
    return cache_root / "symbols" / "nse" / f"{symbol.upper()}.parquet"


def candle_api_cache_path(cache_root: Path, instrument_key: str, to_d: date, from_d: date) -> Path:
    safe = instrument_key.replace("|", "_")
    return cache_root / "api" / "day" / f"{safe}_{from_d.isoformat()}_{to_d.isoformat()}.json.gz"


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
    chunks: list[tuple[date, date]] = []
    d = start
    while d <= end:
        chunk_end = min(d + timedelta(days=MAX_DAILY_RANGE_DAYS), end)
        chunks.append((d, chunk_end))
        d = chunk_end + timedelta(days=1)
    return chunks


@dataclass
class UpstoxClient:
    access_token: str | None = None
    cache_dir: Path = field(default_factory=lambda: UPSTOX_CACHE_DIR)
    min_interval_s: float = 0.35
    use_cache: bool = True
    instruments_ttl_hours: float = 24.0
    _last_request_at: float = 0.0
    _symbol_to_key: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.access_token is None:
            self.access_token = upstox_access_token()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self.min_interval_s - (now - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    def _auth_headers(self) -> dict[str, str]:
        if not self.access_token:
            raise UpstoxError(
                "Set UPSTOX_ACCESS_TOKEN or UPSTOX_API_KEY in the environment (.env)."
            )
        return {"Accept": "application/json", "Authorization": f"Bearer {self.access_token}"}

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

        self._throttle()
        resp = requests.get(NSE_INSTRUMENTS_URL, timeout=120)
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
        refresh_cache: bool = False,
    ) -> list[list[Any]]:
        cache_path = candle_api_cache_path(self.cache_dir, instrument_key, to_d, from_d)
        if self.use_cache and not refresh_cache:
            cached = read_json_cache(cache_path)
            if cached is not None:
                data = cached.get("data", {})
                candles = data.get("candles", [])
                return candles if isinstance(candles, list) else []

        enc_key = quote(instrument_key, safe="")
        url = f"{UPSTOX_BASE_URL}/historical-candle/{enc_key}/day/{to_d.isoformat()}/{from_d.isoformat()}"
        self._throttle()
        resp = requests.get(url, headers=self._auth_headers(), timeout=60)
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

        ranges_to_fetch: list[tuple[date, date]] = []
        if existing.empty:
            ranges_to_fetch = _year_chunks(start, end)
        else:
            have_max = max(existing["date"])
            have_min = min(existing["date"])
            if have_min <= start and have_max >= end:
                return {
                    "symbol": sym,
                    "status": "cached",
                    "rows": len(existing),
                    "path": str(out_path),
                }
            if have_min > start:
                ranges_to_fetch.extend(_year_chunks(start, have_min - timedelta(days=1)))
            if have_max < end:
                ranges_to_fetch.extend(_year_chunks(have_max + timedelta(days=1), end))

        frames = [existing] if not existing.empty else []
        for a, b in ranges_to_fetch:
            candles = self.fetch_candles(inst, a, b, refresh_cache=refresh_cache)
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
    overwrite_parquet: bool = False,
    dry_run: bool = False,
) -> pd.DataFrame:
    """Fetch per-symbol day history (cached), then write ``nse_cm_YYYYMMDD.parquet`` files."""
    client = client or UpstoxClient()
    imap = client.load_nse_eq_map()
    syms = [s.upper() for s in (symbols or sorted(imap))]
    if max_symbols:
        syms = syms[: int(max_symbols)]

    if dry_run:
        return pd.DataFrame({"symbol": syms, "status": ["would_sync"] * len(syms)})

    rows: list[dict[str, Any]] = []
    for i, sym in enumerate(syms, 1):
        try:
            r = client.sync_symbol(sym, start, end, refresh_cache=refresh_cache)
            rows.append(r)
            append_symbol_manifest({**r, "fetched_at": datetime.utcnow().isoformat()})
        except UpstoxError as exc:
            err = {"symbol": sym, "status": f"error:{exc}", "rows": 0}
            rows.append(err)
            append_symbol_manifest({**err, "fetched_at": datetime.utcnow().isoformat()})
        if i % 50 == 0:
            print(f"  symbols {i}/{len(syms)}", flush=True)

    if materialize:
        mat = materialize_nse_cm_range(
            parquet_root,
            client.cache_dir,
            start,
            end,
            overwrite=overwrite_parquet,
        )
        if not mat.empty:
            rows.extend(mat.to_dict("records"))

    sym_df = pd.DataFrame([r for r in rows if "symbol" in r])
    return sym_df if not sym_df.empty else pd.DataFrame(rows)


def sync_upstox_incremental(
    parquet_root: Path,
    *,
    client: UpstoxClient | None = None,
    lookback_years: int = 15,
    **kwargs: Any,
) -> pd.DataFrame:
    client = client or UpstoxClient()
    end = date.today()
    start = end - timedelta(days=lookback_years * 365)
    latest = latest_local_parquet_date(parquet_root)
    if latest is not None:
        start = min(start, latest - timedelta(days=30))
    return sync_upstox_universe(parquet_root, start, end, client=client, **kwargs)