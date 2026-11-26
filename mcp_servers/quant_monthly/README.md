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
PYTHONPATH=src python -m mcp_servers.quant_monthly --http 8765 --host 127.0.0.1
```

### Always running (VM)

```bash
sudo ./scripts/install_quant_monthly_mcp_service.sh
sudo systemctl status quant-monthly-mcp
sudo journalctl -u quant-monthly-mcp -f
```

Overrides: `QUANT_USER`, `QUANT_DATA_DIR`, `QUANT_PORT`, `QUANT_BIND` (see script header).

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

This repo includes **`.cursor/mcp.json`**:

| Name | Use when |
|------|----------|
| `quant-monthly` | Remote VM (`https://mcp.marketsenseanalytics.com/mcp`) — default |
| `quant-monthly-local` | Same machine as data cache; set `QUANT_DATA_DIR` in your shell/env |

After editing: **Cursor Settings → MCP** → enable the server → **reload window**.

Remote entry (Streamable HTTP, no auth):

```json
{
  "mcpServers": {
    "quant-monthly": {
      "url": "https://mcp.marketsenseanalytics.com/mcp"
    }
  }
}
```

Local stdio (Mac with VM data path or synced cache):

```json
{
  "mcpServers": {
    "quant-monthly-local": {
      "command": "${workspaceFolder}/.venv/bin/python",
      "args": ["-m", "mcp_servers.quant_monthly", "--stdio"],
      "env": {
        "PYTHONPATH": "${workspaceFolder}/src",
        "QUANT_DATA_DIR": "${env:QUANT_DATA_DIR}"
      }
    }
  }
}
```

Set `QUANT_DATA_DIR` to the folder that contains `raw/upstox/` (same as the institutional notebook).

## ChatGPT (requires HTTPS)

ChatGPT does **not** accept `http://IP:8765/mcp`. Use:

- **`deploy-https.md`** — Caddy + domain, Cloudflare Tunnel, or OpenAI Secure MCP Tunnel
- Plugin URL: `https://<your-hostname>/mcp` (not the root `/`)