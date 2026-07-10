#!/usr/bin/env bash
# Web grounding smoke: real ChatRuntime + LLM + web tools; artifact log + PASS/FAIL line.
# Repo root = parent of this script's directory.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_DIR="${WEB_GROUNDING_SMOKE_LOG_DIR:-$ROOT/reports/archive/web_grounding_smoke}"
mkdir -p "$LOG_DIR"
LOG_FILE="${WEB_GROUNDING_SMOKE_LOG:-$LOG_DIR/run_${TS}.log}"

echo "[web_grounding_smoke.sh] log: $LOG_FILE" >&2

set +e
"$PY" -m aihub.scripts.web_grounding_smoke --log-file "$LOG_FILE"
RC=$?
set -e

if [[ "$RC" -eq 0 ]]; then
  echo "[web_grounding_smoke.sh] RESULT: PASS (exit 0)"
else
  echo "[web_grounding_smoke.sh] RESULT: FAIL (exit $RC)" >&2
fi

exit "$RC"
