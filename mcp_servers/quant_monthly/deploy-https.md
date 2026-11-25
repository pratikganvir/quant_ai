# HTTPS for ChatGPT MCP

ChatGPT **rejects plain HTTP** for custom MCP servers. Use one of these.

## Prerequisite: MCP on localhost

Always bind the app to **127.0.0.1**; only the proxy is public.

```bash
cd ~/quant
source .venv/bin/activate
export PYTHONPATH=src
export QUANT_DATA_DIR=/home/pratik/data/quant_ai

### Always-on (systemd)

```bash
cd ~/quant
# optional: sudo QUANT_USER=pratik QUANT_DATA_DIR=/home/pratik/data/quant_ai \
#   QUANT_BIND=127.0.0.1 ./scripts/install_quant_monthly_mcp_service.sh
sudo ./scripts/install_quant_monthly_mcp_service.sh
curl -s http://127.0.0.1:8765/health
```

Set `QUANT_BIND=0.0.0.0` only if you expose the port directly (use Tunnel or Caddy + HTTPS for ChatGPT).

Manual foreground:

```bash
python -m mcp_servers.quant_monthly --http 8765 --host 127.0.0.1
```
```

---

## Option 1 — Domain + Caddy (recommended)

**You need a hostname** (Let's Encrypt does not usefully cover raw IPs like `78.47.81.36` for ChatGPT).

1. Create DNS **A record**: `mcp.yourdomain.com` → `78.47.81.36`
2. Open firewall **80** and **443** (close public **8765** if it was open):

   ```bash
   sudo ufw allow 80,443/tcp
   sudo ufw delete allow 8765/tcp   # optional: hide raw port
   ```

3. Install [Caddy](https://caddyserver.com/docs/install) on the VM.
4. Use `Caddyfile.example` in this folder (replace `YOURDOMAIN`).
5. `sudo systemctl enable --now caddy && sudo systemctl reload caddy`
6. Test:

   ```bash
   curl -s https://mcp.yourdomain.com/health
   ```

7. **ChatGPT** → Add custom MCP server → Server URL:

   ```text
   https://mcp.yourdomain.com/mcp
   ```

   Auth: **No authentication**.

---

## Option 2 — OpenAI Secure MCP Tunnel (no domain, no HTTPS on your VM)

Keep MCP on `127.0.0.1:8765` only. ChatGPT connects via tunnel.

Guide: [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

In ChatGPT: **Connection** → **Tunnel** → pick tunnel targeting `http://127.0.0.1:8765/mcp`.

---

## Option 3 — Cloudflare Tunnel (free HTTPS URL without opening ports)

1. [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) on the VM.
2. Route a hostname (e.g. `mcp.yourdomain.com`) to `http://127.0.0.1:8765`.
3. ChatGPT URL: `https://mcp.yourdomain.com/mcp`

---

## What not to use in ChatGPT

| URL | Works? |
|-----|--------|
| `http://78.47.81.36:8765/mcp` | No (HTTP) |
| `https://78.47.81.36:8765/mcp` | No (no valid cert on IP) |
| `https://mcp.yourdomain.com/mcp` | Yes (with Caddy/CF/nginx) |

---

## REST (browser / curl) after HTTPS

- `https://mcp.yourdomain.com/api/stocks`
- `https://mcp.yourdomain.com/api/monthly/RELIANCE?limit=24`