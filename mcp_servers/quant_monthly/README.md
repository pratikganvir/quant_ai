# Quant OHLCV cache — MCP + REST

Serves **local** Upstox **monthly** (`nse_month/`), **daily** (`nse/`) OHLCV, and **fundamentals** (`fundamentals/{SYMBOL}/`) JSON (no Upstox API on read). One process, one port.

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
| GET | `/health` | Liveness + monthly/daily/fundamentals cache dirs |
| GET | `/api/stocks` | Inventory (`?prefix=REL`, `?interval=daily` default monthly) |
| GET | `/api/monthly/{symbol}` | Monthly bars (`?start=&end=&limit=120`) |
| GET | `/api/daily/{symbol}` | Daily bars (`?start=&end=&limit=500`) |
| GET | `/api/fundamentals/stocks` | Symbols with fundamentals cache (`?prefix=REL`) |
| GET | `/api/fundamentals/{symbol}/{resource}` | Cached fundamentals (`profile`, `key-ratios`, …) |
| GET | `/api/key-ratios/stocks` | Symbols with key-ratios cache (`?prefix=REL`) |
| GET | `/api/key-ratios/{symbol}` | Key ratios only |
| GET | `/api/share-holdings/stocks` | Symbols with share-holdings cache |
| GET | `/api/share-holdings/{symbol}` | Share-holdings only |

## MCP tools

- `list_available_stocks` / `get_monthly_ohlcv` — monthly
- `list_daily_stocks` / `get_daily_ohlcv` — daily
- `list_fundamentals_stocks` / `get_fundamentals` — fundamentals (populate via `scripts/sync_upstox_fundamentals.py`)
- `list_key_ratios_stocks` / `get_key_ratios` — key-ratios only (`scripts/sync_upstox_key_ratios.sh`)
- `list_share_holdings_stocks` / `get_share_holdings` — share-holdings (`scripts/sync_upstox_share_holdings.sh`)

### Sync fundamentals (Upstox API)

```bash
PYTHONPATH=src python scripts/sync_upstox_fundamentals.py --symbol RELIANCE
PYTHONPATH=src python scripts/sync_upstox_fundamentals.py --max-symbols 50
./scripts/sync_upstox_fundamentals_all.sh
./scripts/sync_upstox_key_ratios.sh
./scripts/sync_upstox_share_holdings.sh
PYTHONPATH=src python scripts/sync_upstox_fundamentals.py --resources profile,key-ratios
```

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