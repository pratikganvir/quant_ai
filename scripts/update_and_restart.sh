#!/usr/bin/env bash
# Fast-forward the current git branch, refresh Python deps, restart Jupyter.
#
# Usage (from the repo, as the deploy user or with sudo):
#   ./scripts/update_and_restart.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DEPLOY_ENV="${PROJECT_ROOT}/.deploy.env"

if [[ -f "${DEPLOY_ENV}" ]]; then
  # shellcheck disable=SC1090
  . "${DEPLOY_ENV}"
fi

VENV_DIR="${VENV_DIR:-${PROJECT_ROOT}/.venv}"
SERVICE_NAME="${SERVICE_NAME:-quant-jupyter}"
TARGET_USER="${TARGET_USER:-${SUDO_USER:-${USER}}}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Virtualenv missing at ${VENV_DIR}. Run scripts/setup_ubuntu.sh first." >&2
  exit 1
fi

if [[ ! -d "${PROJECT_ROOT}/.git" ]]; then
  echo "Not a git checkout: ${PROJECT_ROOT}" >&2
  exit 1
fi

run_repo() {
  if [[ "${EUID}" -eq 0 ]]; then
    sudo -u "${TARGET_USER}" -H bash -c 'cd "$1" && shift && exec "$@"' bash "${PROJECT_ROOT}" "$@"
  else
    "$@"
  fi
}

cd "${PROJECT_ROOT}"

if [[ -n "$(run_repo git status --porcelain --untracked-files=no)" ]]; then
  echo "Working tree has local modifications. Commit, stash, or discard them before updating." >&2
  run_repo git status --short
  exit 1
fi

BRANCH="$(run_repo git rev-parse --abbrev-ref HEAD)"
if [[ "${BRANCH}" == "HEAD" ]]; then
  echo "Detached HEAD. Checkout a branch before updating." >&2
  exit 1
fi

echo "==> Fetching origin"
run_repo git fetch --prune origin

if run_repo git rev-parse --abbrev-ref --symbolic-full-name '@{u}' >/dev/null 2>&1; then
  echo "==> Fast-forwarding ${BRANCH} from upstream"
  run_repo git pull --ff-only
else
  echo "==> No upstream for ${BRANCH}; skipping pull"
fi

echo "==> Refreshing Python dependencies"
REQ_FILTERED="$(mktemp)"
trap 'rm -f "${REQ_FILTERED}"' EXIT
grep -v -E '^(appnope([=<>~!]|$)|#|$)' "${PROJECT_ROOT}/requirements.txt" > "${REQ_FILTERED}"
chmod 644 "${REQ_FILTERED}"
run_repo "${VENV_DIR}/bin/pip" install -r "${REQ_FILTERED}"
run_repo "${VENV_DIR}/bin/pip" install -e "${PROJECT_ROOT}"

echo "==> Restarting ${SERVICE_NAME}"
if [[ "${EUID}" -eq 0 ]]; then
  systemctl restart "${SERVICE_NAME}"
else
  sudo systemctl restart "${SERVICE_NAME}"
fi

systemctl --no-pager --full status "${SERVICE_NAME}" || true

echo
echo "Updated ${BRANCH} at $(run_repo git rev-parse --short HEAD)."
echo "Jupyter: http://<vm-ip>:${JUPYTER_PORT:-8888}"
