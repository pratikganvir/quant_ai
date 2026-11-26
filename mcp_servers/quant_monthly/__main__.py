"""Run Quant Upstox cache MCP server — monthly + daily (stdio, HTTP, or SSE)."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SRC = _REPO / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from quant.config import UPSTOX_CACHE_DIR
from quant.data.daily_cache import (
    coverage_as_dicts as daily_coverage_as_dicts,
    daily_symbols_dir,
    get_daily_bars,
    list_daily_stock_coverage,
)
from quant.data.fundamentals_cache import (
    fundamentals_coverage_as_dicts,
    get_fundamentals,
    list_stocks_with_fundamentals,
)
from quant.data.monthly_cache import (
    coverage_as_dicts,
    get_monthly_bars,
    list_monthly_stock_coverage,
    monthly_symbols_dir,
)
from quant.data.upstox_fundamentals import FUNDAMENTAL_RESOURCES, fundamentals_root

server = MCPServer(
    "quant-monthly",
    title="Quant NSE OHLCV Cache",
    description=(
        "Cached Upstox monthly (nse_month/) and daily (nse/) OHLCV under QUANT_DATA_DIR/raw/upstox/symbols/."
    ),
    instructions=(
        "Monthly: list_available_stocks + get_monthly_ohlcv. "
        "Daily: list_daily_stocks + get_daily_ohlcv. "
        "Fundamentals: list_fundamentals_stocks + get_fundamentals (cache from sync_upstox_fundamentals). "
        "REST: GET /api/stocks?interval=monthly|daily, /api/monthly/{symbol}, /api/daily/{symbol}, "
        "/api/fundamentals/stocks, /api/fundamentals/{symbol}/{resource}."
    ),
    version="1.2.0",
)


@server.tool(
    title="List stocks with monthly data",
    description="Symbols that have a local Upstox monthly parquet cache (first/last bar, row count).",
)
def list_available_stocks() -> dict:
    rows = list_monthly_stock_coverage()
    return {
        "cache_dir": str(monthly_symbols_dir()),
        "count": len(rows),
        "stocks": coverage_as_dicts(rows),
    }


@server.tool(
    title="Get monthly OHLCV",
    description="Return cached monthly candles for one NSE symbol (optional date range, tail limit).",
)
def get_monthly_ohlcv(
    symbol: str,
    date_start: str | None = None,
    date_end: str | None = None,
    limit: int | None = 500,
) -> dict:
    sym = symbol.strip().upper()
    bars = get_monthly_bars(sym, date_start=date_start, date_end=date_end, limit=limit)
    return {"symbol": sym, "count": len(bars), "bars": bars}


@server.tool(
    title="List stocks with daily data",
    description="Symbols that have a local Upstox daily parquet cache (first/last bar, row count).",
)
def list_daily_stocks() -> dict:
    rows = list_daily_stock_coverage()
    return {
        "cache_dir": str(daily_symbols_dir()),
        "count": len(rows),
        "stocks": daily_coverage_as_dicts(rows),
    }


@server.tool(
    title="Get daily OHLCV",
    description="Return cached daily candles for one NSE symbol (optional date range, tail limit).",
)
def get_daily_ohlcv(
    symbol: str,
    date_start: str | None = None,
    date_end: str | None = None,
    limit: int | None = 500,
) -> dict:
    sym = symbol.strip().upper()
    bars = get_daily_bars(sym, date_start=date_start, date_end=date_end, limit=limit)
    return {"symbol": sym, "count": len(bars), "bars": bars}


@server.tool(
    title="List stocks with fundamentals",
    description="Symbols with cached Upstox fundamentals (profile, key-ratios, statements, etc.).",
)
def list_fundamentals_stocks() -> dict:
    rows = list_stocks_with_fundamentals()
    return {
        "cache_dir": str(fundamentals_root()),
        "count": len(rows),
        "stocks": fundamentals_coverage_as_dicts(rows),
        "resources": list(FUNDAMENTAL_RESOURCES),
    }


@server.tool(
    title="Get fundamentals",
    description=(
        "Return cached Upstox fundamentals for one symbol. "
        f"resource one of: {', '.join(FUNDAMENTAL_RESOURCES)}."
    ),
)
def get_fundamentals_tool(
    symbol: str,
    resource: str = "profile",
) -> dict:
    sym = symbol.strip().upper()
    res = resource.strip().lower()
    try:
        data = get_fundamentals(sym, res)
    except FileNotFoundError as exc:
        return {"symbol": sym, "resource": res, "error": str(exc), "status": "missing"}
    except ValueError as exc:
        return {"symbol": sym, "resource": res, "error": str(exc), "status": "bad_request"}
    return {"symbol": sym, "resource": res, "status": data.get("status", "ok"), "data": data.get("data"), "meta": data.get("_meta")}


def _stock_interval(request: Request) -> str:
    raw = (request.query_params.get("interval") or "monthly").strip().lower()
    if raw in ("day", "daily", "d"):
        return "daily"
    return "monthly"


@server.custom_route("/health", methods=["GET"], include_in_schema=True)
async def health(_: Request) -> Response:
    return JSONResponse(
        {
            "status": "ok",
            "monthly_cache_dir": str(monthly_symbols_dir()),
            "daily_cache_dir": str(daily_symbols_dir()),
            "fundamentals_cache_dir": str(fundamentals_root()),
        }
    )


@server.custom_route("/api/stocks", methods=["GET"], include_in_schema=True)
async def http_list_stocks(request: Request) -> Response:
    prefix = (request.query_params.get("prefix") or "").strip().upper()
    interval = _stock_interval(request)
    if interval == "daily":
        rows = list_daily_stock_coverage()
        cache_dir = daily_symbols_dir()
        stocks = daily_coverage_as_dicts(rows)
    else:
        rows = list_monthly_stock_coverage()
        cache_dir = monthly_symbols_dir()
        stocks = coverage_as_dicts(rows)
    if prefix:
        stocks = [r for r in stocks if r["symbol"].startswith(prefix)]
    return JSONResponse(
        {
            "interval": interval,
            "cache_dir": str(cache_dir),
            "count": len(stocks),
            "stocks": stocks,
        }
    )


@server.custom_route("/api/monthly/{symbol}", methods=["GET"], include_in_schema=True)
async def http_monthly_bars(request: Request) -> Response:
    symbol = request.path_params["symbol"]
    q = request.query_params
    try:
        bars = get_monthly_bars(
            symbol,
            date_start=q.get("start"),
            date_end=q.get("end"),
            limit=int(q["limit"]) if q.get("limit") else None,
        )
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"symbol": symbol.strip().upper(), "count": len(bars), "bars": bars})


@server.custom_route("/api/daily/{symbol}", methods=["GET"], include_in_schema=True)
async def http_daily_bars(request: Request) -> Response:
    symbol = request.path_params["symbol"]
    q = request.query_params
    try:
        bars = get_daily_bars(
            symbol,
            date_start=q.get("start"),
            date_end=q.get("end"),
            limit=int(q["limit"]) if q.get("limit") else None,
        )
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"symbol": symbol.strip().upper(), "count": len(bars), "bars": bars})


@server.custom_route("/api/fundamentals/stocks", methods=["GET"], include_in_schema=True)
async def http_list_fundamentals(request: Request) -> Response:
    prefix = (request.query_params.get("prefix") or "").strip().upper()
    rows = list_stocks_with_fundamentals()
    stocks = fundamentals_coverage_as_dicts(rows)
    if prefix:
        stocks = [r for r in stocks if r["symbol"].startswith(prefix)]
    return JSONResponse(
        {
            "cache_dir": str(fundamentals_root()),
            "count": len(stocks),
            "resources": list(FUNDAMENTAL_RESOURCES),
            "stocks": stocks,
        }
    )


@server.custom_route("/api/fundamentals/{symbol}/{resource}", methods=["GET"], include_in_schema=True)
async def http_fundamentals(request: Request) -> Response:
    symbol = request.path_params["symbol"]
    resource = request.path_params["resource"]
    try:
        data = get_fundamentals(symbol, resource)
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(
        {
            "symbol": symbol.strip().upper(),
            "resource": resource.strip().lower(),
            "status": data.get("status"),
            "data": data.get("data"),
            "meta": data.get("_meta"),
        }
    )


def _streamable_http_transport_security() -> TransportSecuritySettings | None:
    """Allow public Host headers when behind a reverse proxy (e.g. Cloudflare Tunnel)."""
    flag = os.environ.get("MCP_DISABLE_DNS_REBINDING", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)

    extra = os.environ.get("MCP_ALLOWED_HOSTS", "").strip()
    if not extra:
        return None

    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "127.0.0.1", "localhost"]
    allowed_origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    for part in extra.split(","):
        host = part.strip()
        if not host:
            continue
        allowed_hosts.append(host)
        if ":*" not in host and ":" not in host:
            allowed_hosts.append(f"{host}:*")
        base = host.split(":")[0]
        allowed_origins.extend([f"https://{base}", f"https://{base}:*", f"http://{base}", f"http://{base}:*"])

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=allowed_origins,
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--stdio", action="store_true", help="MCP over stdio (Cursor default)")
    g.add_argument(
        "--http",
        type=int,
        metavar="PORT",
        help="Streamable HTTP MCP + REST on PORT (path /mcp, routes /api/*)",
    )
    g.add_argument("--sse", type=int, metavar="PORT", help="Legacy SSE MCP on PORT")
    p.add_argument("--host", default="127.0.0.1")
    return p.parse_args()


async def _main() -> None:
    args = _parse_args()
    print(f"UPSTOX_CACHE_DIR={UPSTOX_CACHE_DIR}", file=sys.stderr)
    print(f"monthly_symbols={monthly_symbols_dir()}", file=sys.stderr)
    print(f"daily_symbols={daily_symbols_dir()}", file=sys.stderr)
    print(f"fundamentals={fundamentals_root()}", file=sys.stderr)
    if args.stdio:
        await server.run_stdio_async()
    elif args.http is not None:
        await server.run_streamable_http_async(
            host=args.host,
            port=args.http,
            json_response=True,
            transport_security=_streamable_http_transport_security(),
        )
    else:
        await server.run_sse_async(host=args.host, port=args.sse)


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()