#!/usr/bin/env bash
# Sync monthly bars for regime + SMA12 test universe (small list → fewer API calls).
#
# Usage:
#   cd ~/quant_ai
#   export QUANT_DATA_DIR=/home/pratik/data/quant_ai
#   ./scripts/sync_regime_sma12_universe.sh              # fetch monthly
#   ./scripts/sync_regime_sma12_universe.sh --plan-only  # gaps only, no HTTP
#   ./scripts/sync_regime_sma12_universe.sh --incremental
#
# Requires UPSTOX_ACCESS_TOKEN in .env

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIST="${REGIME_SMA12_UNIVERSE_LIST:-$ROOT/universe/regime_sma12_monthly_symbols.txt}"
VENV="${VENV:-$ROOT/.venv/bin/python}"
START="${REGIME_SMA12_START:-2005-01-01}"

if [[ ! -f "$LIST" ]]; then
  echo "Missing symbol list: $LIST" >&2
  exit 1
fi

mapfile -t _SYMS < <(grep -v '^\s*#' "$LIST" | grep -v '^\s*$' | tr '[:lower:]' '[:upper:]')
if [[ ${#_SYMS[@]} -eq 0 ]]; then
  echo "No symbols in $LIST" >&2
  exit 1
fi

SYMS_CSV=$(
  IFS=,
  echo "${_SYMS[*]}"
)

export PYTHONPATH="${ROOT}/src"
EXTRA=()
if [[ "${1:-}" == "--plan-only" ]]; then
  EXTRA+=(--plan-only)
  shift || true
fi
if [[ "${1:-}" == "--incremental" ]]; then
  EXTRA+=(--incremental)
  shift || true
fi

echo "Universe: ${#_SYMS[@]} symbols (monthly from $START)"
echo "List: $LIST"

exec "$VENV" "${ROOT}/scripts/sync_upstox_nse_daily.py" \
  --interval month \
  --start "$START" \
  --symbols "$SYMS_CSV" \
  --no-materialize \
  "${EXTRA[@]}" \
  "$@"