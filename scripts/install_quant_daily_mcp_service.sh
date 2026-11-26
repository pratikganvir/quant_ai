#!/usr/bin/env bash
# Install systemd unit quant-daily-mcp (always-on MCP + REST).
#
# Usage (on VM):
#   cd /path/to/quant
#   sudo ./scripts/install_quant_daily_mcp_service.sh
#
# Optional env overrides:
#   QUANT_USER=pratik QUANT_PORT=8766 QUANT_BIND=127.0.0.1 QUANT_DATA_DIR=/home/pratik/data/quant_ai

set -euo pipefail

SERVICE_NAME="quant-daily-mcp"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_USER="${QUANT_USER:-pratik}"
VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
QUANT_PORT="${QUANT_PORT:-8766}"
QUANT_BIND="${QUANT_BIND:-127.0.0.1}"
QUANT_DATA_DIR="${QUANT_DATA_DIR:-/home/pratik/data/quant_ai}"
MCP_ALLOWED_HOSTS="${MCP_ALLOWED_HOSTS:-mcp.marketsenseanalytics.com}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Missing venv at ${VENV_DIR}/bin/python" >&2
  exit 1
fi

if ! id "${TARGET_USER}" &>/dev/null; then
  echo "User ${TARGET_USER} does not exist" >&2
  exit 1
fi

"${VENV_DIR}/bin/pip" install -q -r "${PROJECT_ROOT}/requirements-mcp.txt" 2>/dev/null || true

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Quant NSE daily cache MCP (Streamable HTTP + REST)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
Group=${TARGET_USER}
WorkingDirectory=${PROJECT_ROOT}
Environment=PATH=${VENV_DIR}/bin:/usr/bin
Environment=VIRTUAL_ENV=${VENV_DIR}
Environment=PYTHONPATH=${PROJECT_ROOT}/src
Environment=QUANT_PROJECT_ROOT=${PROJECT_ROOT}
Environment=QUANT_DATA_DIR=${QUANT_DATA_DIR}
Environment=MCP_ALLOWED_HOSTS=${MCP_ALLOWED_HOSTS}
ExecStart=${VENV_DIR}/bin/python -m mcp_servers.quant_daily --http ${QUANT_PORT} --host ${QUANT_BIND}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

echo
echo "Installed ${SERVICE_NAME}.service"
echo "  MCP:     http://${QUANT_BIND}:${QUANT_PORT}/mcp"
echo "  Health:  http://${QUANT_BIND}:${QUANT_PORT}/health"
echo "  Logs:    sudo journalctl -u ${SERVICE_NAME} -f"
echo "  Status:  sudo systemctl status ${SERVICE_NAME}"
echo "  Restart: sudo systemctl restart ${SERVICE_NAME}"
echo
systemctl --no-pager status "${SERVICE_NAME}" || true