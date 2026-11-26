#!/usr/bin/env bash
# DEPRECATED: daily routes live in quant-monthly-mcp (port 8765).
#
#   sudo ./scripts/install_quant_monthly_mcp_service.sh
#   sudo systemctl restart quant-monthly-mcp
#
# If you previously installed quant-daily-mcp, this script stops and disables it.

set -euo pipefail

SERVICE_NAME="quant-daily-mcp"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run with sudo: sudo $0" >&2
  exit 1
fi

if systemctl list-unit-files | grep -q "^${SERVICE_NAME}.service"; then
  systemctl disable --now "${SERVICE_NAME}" 2>/dev/null || true
  rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
  systemctl daemon-reload
  echo "Removed ${SERVICE_NAME}. Use quant-monthly-mcp on port 8765."
else
  echo "No ${SERVICE_NAME} unit found. Use: sudo ./scripts/install_quant_monthly_mcp_service.sh"
fi