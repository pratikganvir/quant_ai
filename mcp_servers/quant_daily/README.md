# Quant daily cache — MCP + REST

Serves **local** Upstox **daily** OHLCV from `raw/upstox/symbols/nse/` (no Upstox API calls).

## Install

```bash
pip install -r requirements-mcp.txt
```

## Run

```bash
# MCP for Cursor (stdio)
PYTHONPATH=src python -m mcp_servers.quant_daily --stdio

# HTTP: MCP at http://127.0.0.1:8766/mcp + REST below
PYTHONPATH=src python -m mcp_servers.quant_daily --http 8766 --host 127.0.0.1
```

### Always running (VM)

```bash
sudo ./scripts/install_quant_daily_mcp_service.sh
sudo systemctl status quant-daily-mcp
sudo journalctl -u quant-daily-mcp -f
```

Overrides: `QUANT_USER`, `QUANT_DATA_DIR`, `QUANT_PORT` (default **8766**), `QUANT_BIND` (see script header).

## REST endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness + cache directory |
| GET | `/api/stocks` | All symbols with cache (`?prefix=REL`) |
| GET | `/api/daily/{symbol}` | Bars (`?start=2020-01-01&end=2024-12-31&limit=500`) |

## MCP tools

- `list_available_stocks` — coverage inventory
- `get_daily_ohlcv` — bars for one symbol

## Cursor MCP config

This repo includes **`.cursor/mcp.json`** entries:

| Name | Use when |
|------|----------|
| `quant-daily` | Remote VM (same host as monthly; route `/api/daily` + daily `/mcp` via Caddy — see below) |
| `quant-daily-local` | Same machine as daily symbol parquets; set `QUANT_DATA_DIR` |

After editing: **Cursor Settings → MCP** → enable the server → **reload window**.

Local stdio:

```json
{
  "mcpServers": {
    "quant-daily-local": {
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["-m", "mcp_servers.quant_daily", "--stdio"],
      "env": {
        "PYTHONPATH": "${workspaceFolder}/src",
        "QUANT_PROJECT_ROOT": "${workspaceFolder}",
        "QUANT_DATA_DIR": "${env:QUANT_DATA_DIR}"
      }
    }
  }
}
```

## One hostname (Marketsense / Caddy)

Run monthly on **8765** and daily on **8766**, then route by path:

```caddy
mcp.marketsenseanalytics.com {
	handle /api/daily/* {
		reverse_proxy 127.0.0.1:8766
	}
	handle /mcp/daily {
		uri strip_prefix /mcp/daily
		reverse_proxy 127.0.0.1:8766
	}
	reverse_proxy 127.0.0.1:8765
}
```

Cursor remote daily MCP URL: `https://mcp.marketsenseanalytics.com/mcp/daily` (after Caddy is updated).

Populate cache first:

```bash
PYTHONPATH=src python scripts/sync_upstox_nse_daily.py --symbol RELIANCE --start 2015-01-01
```