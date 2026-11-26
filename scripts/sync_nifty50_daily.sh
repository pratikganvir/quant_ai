#!/usr/bin/env bash
# Sync daily Upstox cache for Nifty 50 EQ issuers (2011 → today by default).
#
#   cd ~/quant_ai && export QUANT_DATA_DIR=/home/pratik/data/quant_ai
#   ./scripts/sync_nifty50_daily.sh --plan-only
#   ./scripts/sync_nifty50_daily.sh
#   ./scripts/sync_nifty50_daily.sh --incremental

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIST="${NIFTY50_UNIVERSE_LIST:-$ROOT/universe/nifty50_eq_symbols.txt}"
VENV="${VENV:-$ROOT/.venv/bin/python}"
START="${NIFTY50_START:-2011-01-01}"

if [[ ! -f "$LIST" ]]; then
  echo "Missing: $LIST" >&2
  exit 1
fi

mapfile -t _SYMS < <(grep -v '^\s*#' "$LIST" | grep -v '^\s*$' | tr '[:lower:]' '[:upper:]')
SYMS_CSV=$(IFS=,; echo "${_SYMS[*]}")
export PYTHONPATH="${ROOT}/src"

EXTRA=()
[[ "${1:-}" == "--plan-only" ]] && { EXTRA+=(--plan-only); shift; }
[[ "${1:-}" == "--incremental" ]] && { EXTRA+=(--incremental); shift; }

echo "Nifty 50 EQ: ${#_SYMS[@]} symbols, daily from $START"

exec "$VENV" "${ROOT}/scripts/sync_upstox_nse_daily.py" \
  --start "$START" \
  --symbols "$SYMS_CSV" \
  --no-materialize \
  "${EXTRA[@]}" \
  "$@"