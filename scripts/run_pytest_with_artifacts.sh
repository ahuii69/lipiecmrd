#!/usr/bin/env bash
set -euo pipefail

# Run pytest and always persist failure evidence (log + junit xml).
# Usage:
#   scripts/run_pytest_with_artifacts.sh
#   scripts/run_pytest_with_artifacts.sh tests/test_repair_sprint_v2.py -q

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="${REPORT_DIR:-${ROOT_DIR}/reports/archive/pytest}"
PYTHON_BIN="${PYTHON_BIN:-${ROOT_DIR}/.venv/bin/python}"

mkdir -p "${REPORT_DIR}"
STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${REPORT_DIR}/pytest_${STAMP}.log"
JUNIT_FILE="${REPORT_DIR}/pytest_${STAMP}.xml"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python executable not found: ${PYTHON_BIN}" >&2
  exit 2
fi

CMD=("${PYTHON_BIN}" -m pytest -ra --tb=short --junitxml "${JUNIT_FILE}")
if (($#)); then
  CMD+=("$@")
fi

echo "[pytest] root=${ROOT_DIR}"
echo "[pytest] log=${LOG_FILE}"
echo "[pytest] junit=${JUNIT_FILE}"
echo "[pytest] cmd=${CMD[*]}"

set +e
(
  cd "${ROOT_DIR}"
  "${CMD[@]}"
) 2>&1 | tee "${LOG_FILE}"
status=${PIPESTATUS[0]}
set -e

echo "[pytest] exit_code=${status}"
echo "[pytest] preserved_log=${LOG_FILE}"
echo "[pytest] preserved_junit=${JUNIT_FILE}"

exit "${status}"
