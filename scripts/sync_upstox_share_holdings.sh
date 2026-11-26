#!/usr/bin/env bash
# Sync Upstox share-holdings for all NSE EQ (one resource per symbol).
#
#   ./scripts/sync_upstox_share_holdings.sh
#   ./scripts/sync_upstox_share_holdings.sh --symbol RELIANCE
#   ./scripts/sync_upstox_share_holdings.sh --dry-run

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

exec "$VENV" "${ROOT}/scripts/sync_upstox_share_holdings.py" --all-eq "$@"