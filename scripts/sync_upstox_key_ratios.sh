#!/usr/bin/env bash
# Sync Upstox key-ratios for all NSE EQ (one resource per symbol — fast vs full fundamentals).
#
#   ./scripts/sync_upstox_key_ratios.sh
#   ./scripts/sync_upstox_key_ratios.sh --symbol RELIANCE
#   ./scripts/sync_upstox_key_ratios.sh --dry-run

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV:-$ROOT/.venv/bin/python}"
export PYTHONPATH="${ROOT}/src"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env"
  set +a
fi

exec "$VENV" "${ROOT}/scripts/sync_upstox_key_ratios.py" --all-eq "$@"