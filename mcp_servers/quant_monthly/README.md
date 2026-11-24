# Quant monthly cache — MCP + REST

Serves **local** Upstox monthly OHLCV from `raw/upstox/symbols/nse_month/` (no Upstox API calls).

## Install

```bash
pip install -r requirements-mcp.txt
```

## Run

```bash
# MCP for Cursor (stdio)
PYTHONPATH=src python -m mcp_servers.quant_monthly --stdio

# HTTP: MCP at http://127.0.0.1:8765/mcp + REST below
PYTHONPATH=src python -m mcp_servers.quant_monthly --http 8765
```

## REST endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness + cache directory |
| GET | `/api/stocks` | All symbols with cache (`?prefix=REL`) |
| GET | `/api/monthly/{symbol}` | Bars (`?start=2020-01-01&end=2024-12-31&limit=120`) |

## MCP tools

- `list_available_stocks` — coverage inventory
- `get_monthly_ohlcv` — bars for one symbol

## Cursor MCP config

Add to project or user MCP settings (adjust paths and `QUANT_DATA_DIR`):

```json
{
  "mcpServers": {
    "quant-monthly": {
      "command": "/path/to/quant/.venv/bin/python",
      "args": ["-m", "mcp_servers.quant_monthly", "--stdio"],
      "cwd": "/path/to/quant",
      "env": {
        "PYTHONPATH": "src",
        "QUANT_DATA_DIR": "/path/to/your/quant/data"
      }
    }
  }
}
```

Set `QUANT_DATA_DIR` to the folder that contains `raw/upstox/` (same as the institutional notebook).