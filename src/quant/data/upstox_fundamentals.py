"""Upstox Fundamentals API → local gzip JSON cache (per symbol / resource)."""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from quant.config import UPSTOX_CACHE_DIR
from quant.data.upstox import (
    NSE_INSTRUMENTS_URL,
    UPSTOX_BASE_URL,
    UpstoxClient,
    UpstoxError,
    read_json_cache,
    write_json_cache,
)

# Path segment names (match Upstox URL paths).
FUNDAMENTAL_RESOURCES: tuple[str, ...] = (
    "profile",
    "key-ratios",
    "balance-sheet",
    "income-statement",
    "cash-flow",
    "share-holdings",
    "corporate-actions",
)


def fundamentals_root(cache_dir: Path | None = None) -> Path:
    return (cache_dir or UPSTOX_CACHE_DIR) / "fundamentals"


def symbol_meta_path(cache_dir: Path | None = None) -> Path:
    return (cache_dir or UPSTOX_CACHE_DIR) / "instruments" / "nse_eq_meta.json.gz"


def symbol_fundamental_path(
    symbol: str,
    resource: str,
    *,
    cache_dir: Path | None = None,
) -> Path:
    sym = symbol.strip().upper()
    res = resource.strip().lower()
    return fundamentals_root(cache_dir) / sym / f"{res}.json.gz"


@dataclass(frozen=True)
class SymbolFundamentalMeta:
    symbol: str
    isin: str | None
    instrument_key: str | None


@dataclass
class FundamentalsCoverage:
    symbol: str
    isin: str | None
    resources: list[str]
    cache_path: str


def load_nse_eq_meta(
    client: UpstoxClient | None = None,
    *,
    refresh: bool = False,
) -> dict[str, SymbolFundamentalMeta]:
    """NSE EQ trading_symbol → ISIN + instrument_key (cached)."""
    client = client or UpstoxClient()
    path = symbol_meta_path(client.cache_dir)
    if client.use_cache and not refresh and path.is_file():
        age_h = (time.time() - path.stat().st_mtime) / 3600.0
        if age_h <= client.instruments_ttl_hours:
            cached = read_json_cache(path)
            if cached and isinstance(cached.get("symbols"), dict):
                return _meta_from_raw(cached["symbols"])

    resp = client._http_get(NSE_INSTRUMENTS_URL, timeout=120.0, auth=False)
    if resp.status_code >= 400:
        raise UpstoxError(f"instruments download HTTP {resp.status_code}")
    raw = gzip.decompress(resp.content)
    items = json.loads(raw)
    raw_map: dict[str, dict[str, str]] = {}
    for item in items:
        if item.get("segment") != "NSE_EQ" or item.get("instrument_type") != "EQ":
            continue
        sym = str(item.get("trading_symbol", "")).strip().upper()
        if not sym:
            continue
        raw_map[sym] = {
            "isin": str(item.get("isin", "") or "").strip().upper(),
            "instrument_key": str(item.get("instrument_key", "") or "").strip(),
        }
    if client.use_cache:
        write_json_cache(
            path,
            {
                "fetched_at": datetime.utcnow().isoformat(),
                "n_symbols": len(raw_map),
                "symbols": raw_map,
            },
        )
    return _meta_from_raw(raw_map)


def _meta_from_raw(raw: Mapping[str, Mapping[str, str]]) -> dict[str, SymbolFundamentalMeta]:
    out: dict[str, SymbolFundamentalMeta] = {}
    for sym, v in raw.items():
        isin = (v.get("isin") or "").strip().upper() or None
        key = (v.get("instrument_key") or "").strip() or None
        out[sym.upper()] = SymbolFundamentalMeta(sym.upper(), isin, key)
    return out


def default_query_params(resource: str) -> dict[str, str]:
    res = resource.lower()
    if res in ("balance-sheet", "cash-flow"):
        return {"type": "consolidated", "fs": "false"}
    if res == "income-statement":
        return {"type": "consolidated", "timePeriod": "yearly", "fs": "false"}
    return {}


def build_fundamentals_url(
    *,
    isin: str,
    resource: str,
    query: Mapping[str, str] | None = None,
) -> str:
    res = resource.strip().lower()
    if res not in FUNDAMENTAL_RESOURCES:
        raise ValueError(f"Unknown resource {resource!r}; choose from {FUNDAMENTAL_RESOURCES}")
    base = f"{UPSTOX_BASE_URL}/fundamentals/{isin}/{res}"
    params = {**(query or {})}
    if params:
        q = "&".join(f"{k}={params[k]}" for k in sorted(params))
        return f"{base}?{q}"
    return base


def fetch_fundamental(
    client: UpstoxClient,
    isin: str,
    resource: str,
    *,
    query: Mapping[str, str] | None = None,
    refresh_cache: bool = False,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Fetch one fundamentals resource; cache under fundamentals/{SYMBOL}/{resource}.json.gz."""
    res = resource.strip().lower()
    merged = {**default_query_params(res), **(query or {})}
    cache_path = None
    if symbol:
        cache_path = symbol_fundamental_path(symbol, res, cache_dir=client.cache_dir)
        if client.use_cache and not refresh_cache:
            cached = read_json_cache(cache_path)
            if cached is not None:
                return cached

    url = build_fundamentals_url(isin=isin, resource=res, query=merged)
    resp = client._http_get(url, timeout=60.0)
    if resp.status_code == 404:
        body: dict[str, Any] = {"status": "error", "errors": [{"message": "not_found"}]}
    elif resp.status_code >= 400:
        raise UpstoxError(f"HTTP {resp.status_code} {url}: {resp.text[:400]}")
    else:
        body = resp.json()

    payload = {
        **body,
        "_meta": {
            "symbol": (symbol or "").upper(),
            "isin": isin,
            "resource": res,
            "query": merged,
            "fetched_at": datetime.utcnow().isoformat(),
        },
    }
    if cache_path and client.use_cache:
        write_json_cache(cache_path, payload)
    return payload


def list_fundamentals_coverage(
    *,
    cache_dir: Path | None = None,
    symbols: Iterable[str] | None = None,
) -> list[FundamentalsCoverage]:
    root = fundamentals_root(cache_dir)
    if not root.is_dir():
        return []
    syms = sorted({s.strip().upper() for s in symbols}) if symbols else sorted(
        p.name for p in root.iterdir() if p.is_dir()
    )
    rows: list[FundamentalsCoverage] = []
    for sym in syms:
        sym_dir = root / sym
        if not sym_dir.is_dir():
            continue
        resources = sorted(
            f.name.removesuffix(".json.gz")
            for f in sym_dir.glob("*.json.gz")
        )
        isin = None
        profile_path = sym_dir / "profile.json.gz"
        if profile_path.is_file():
            prof = read_json_cache(profile_path)
            if prof and prof.get("_meta", {}).get("isin"):
                isin = prof["_meta"]["isin"]
        rows.append(
            FundamentalsCoverage(
                symbol=sym,
                isin=isin,
                resources=resources,
                cache_path=str(sym_dir),
            )
        )
    return rows


def resolve_fundamentals_universe(
    client: UpstoxClient,
    *,
    symbols: Sequence[str] | None = None,
    all_eq: bool = False,
) -> list[str]:
    """Symbol list for sync: explicit, all NSE EQ, or OHLCV-cached union."""
    if symbols:
        return [s.strip().upper() for s in symbols if s.strip()]
    meta = load_nse_eq_meta(client)
    if all_eq:
        return sorted(sym for sym, m in meta.items() if m.isin)
    from quant.data.upstox import list_cached_symbols

    return sorted(
        set(list_cached_symbols(client.cache_dir, interval="day"))
        | set(list_cached_symbols(client.cache_dir, interval="month"))
    )


def sync_fundamentals(
    symbols: Sequence[str] | None = None,
    *,
    resources: Sequence[str] | None = None,
    client: UpstoxClient | None = None,
    refresh_cache: bool = False,
    dry_run: bool = False,
    max_symbols: int | None = None,
    all_eq: bool = False,
    show_progress: bool = True,
) -> dict[str, Any]:
    """Pull fundamentals (default universe: symbols with daily/monthly OHLCV cache)."""
    client = client or UpstoxClient()
    meta = load_nse_eq_meta(client)
    res_list = tuple(resources) if resources else FUNDAMENTAL_RESOURCES

    syms = resolve_fundamentals_universe(client, symbols=symbols, all_eq=all_eq)
    if max_symbols is not None:
        syms = syms[: int(max_symbols)]

    ok, skip, err, cached_skip = 0, 0, 0, 0
    errors: list[dict[str, str]] = []

    try:
        from tqdm.auto import tqdm
    except ImportError:  # pragma: no cover
        tqdm = None  # type: ignore

    sym_iter: Iterable[str] = syms
    if show_progress and tqdm is not None and len(syms) > 1:
        sym_iter = tqdm(syms, desc="Upstox fundamentals", unit="sym")

    for sym in sym_iter:
        m = meta.get(sym)
        if not m or not m.isin:
            skip += 1
            errors.append({"symbol": sym, "error": "no_isin"})
            continue
        if dry_run:
            ok += len(res_list)
            continue
        for res in res_list:
            cache_path = symbol_fundamental_path(sym, res, cache_dir=client.cache_dir)
            if not refresh_cache and cache_path.is_file():
                cached_skip += 1
                continue
            try:
                fetch_fundamental(
                    client,
                    m.isin,
                    res,
                    query=default_query_params(res),
                    refresh_cache=refresh_cache,
                    symbol=sym,
                )
                ok += 1
            except UpstoxError as exc:
                err += 1
                errors.append({"symbol": sym, "resource": res, "error": str(exc)[:200]})

    return {
        "universe": "all_eq" if all_eq else ("explicit" if symbols else "ohlcv_cached"),
        "symbols_requested": len(syms),
        "resources_per_symbol": len(res_list),
        "fetches_ok": ok,
        "cache_hits_skipped": cached_skip,
        "symbols_skipped_no_isin": skip,
        "fetches_error": err,
        "cache_dir": str(fundamentals_root(client.cache_dir)),
        "errors": errors[:50],
    }