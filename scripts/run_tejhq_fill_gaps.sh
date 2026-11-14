#!/usr/bin/env bash
# Start gap-fill in background with a guaranteed log file.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DATA_DIR="${QUANT_DATA_DIR:-/home/pratik/data/quant_ai}"
LOG="${DATA_DIR}/metadata/tejhq_fill_gaps.log"
mkdir -p "${DATA_DIR}/metadata"
touch "${LOG}"

cd "${ROOT}"
export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}"
export QUANT_DATA_DIR="${DATA_DIR}"
export QUANT_PROJECT_ROOT="${ROOT}"

if [[ ! -x "${ROOT}/.venv/bin/python" ]]; then
  echo "Missing ${ROOT}/.venv/bin/python" >&2
  exit 1
fi

ARGS=("$@")
if [[ ${#ARGS[@]} -eq 0 ]]; then
  ARGS=(--fill-gaps)
fi

{
  echo "===== $(date -Is) run_tejhq_fill_gaps ${ARGS[*]} ====="
  exec "${ROOT}/.venv/bin/python" "${ROOT}/scripts/sync_tejhq_nse_daily.py" "${ARGS[@]}"
} >>"${LOG}" 2>&1 &

echo "PID=$!"
echo "LOG=${LOG}"
echo "Tail: tail -f ${LOG}"