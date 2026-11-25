"""Run Quant monthly-cache MCP server (stdio, SSE, or Streamable HTTP with REST routes)."""

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
from quant.data.monthly_cache import (
    coverage_as_dicts,
    get_monthly_bars,
    list_monthly_stock_coverage,
    monthly_symbols_dir,
)

server = MCPServer(
    "quant-monthly",
    title="Quant NSE Monthly Cache",
    description="Cached Upstox monthly OHLCV under QUANT_DATA_DIR/raw/upstox/symbols/nse_month/",
    instructions=(
        "Use list_available_stocks to see symbols with local monthly data, "
        "then get_monthly_ohlcv for OHLCV rows. REST: GET /api/stocks and GET /api/monthly/{symbol}."
    ),
    version="1.0.0",
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


@server.custom_route("/health", methods=["GET"], include_in_schema=True)
async def health(_: Request) -> Response:
    return JSONResponse({"status": "ok", "cache_dir": str(monthly_symbols_dir())})


@server.custom_route("/api/stocks", methods=["GET"], include_in_schema=True)
async def http_list_stocks(request: Request) -> Response:
    prefix = (request.query_params.get("prefix") or "").strip().upper()
    rows = list_monthly_stock_coverage()
    if prefix:
        rows = [r for r in rows if r.symbol.startswith(prefix)]
    return JSONResponse(
        {
            "cache_dir": str(monthly_symbols_dir()),
            "count": len(rows),
            "stocks": coverage_as_dicts(rows),
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