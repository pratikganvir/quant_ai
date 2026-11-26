# Deprecated

Daily (and monthly) MCP + REST are served by **`mcp_servers.quant_monthly`** on one port.

```bash
PYTHONPATH=src python -m mcp_servers.quant_monthly --http 8765
# GET /api/daily/RELIANCE?limit=60
```

Use `sudo ./scripts/install_quant_monthly_mcp_service.sh` on the VM. Stop `quant-daily-mcp` if you installed the old split service:

```bash
sudo systemctl disable --now quant-daily-mcp
sudo systemctl restart quant-monthly-mcp
```

See [`../quant_monthly/README.md`](../quant_monthly/README.md).