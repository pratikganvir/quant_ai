#!/usr/bin/env bash
# Sync Upstox fundamentals for all NSE EQ symbols (idempotent gzip cache per resource).
#
# Requires UPSTOX_ACCESS_TOKEN or UPSTOX_API_KEY in .env / environment.
# Set QUANT_DATA_DIR to the tree that contains raw/upstox/ (see README).
#
# Usage:
#   ./scripts/sync_upstox_fundamentals_all.sh              # full universe, skip cached
#   ./scripts/sync_upstox_fundamentals_all.sh --dry-run    # count API work
#   ./scripts/sync_upstox_fundamentals_all.sh --refresh-cache
#   ./scripts/sync_upstox_fundamentals_all.sh --resources profile,key-ratios
#   ./scripts/sync_upstox_fundamentals_all.sh --max-symbols 200

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

exec "$VENV" "${ROOT}/scripts/sync_upstox_fundamentals.py" --all-eq "$@"