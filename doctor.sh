#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON_BIN:-python3}"
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PY="$APP_DIR/.venv/bin/python"
fi
exec "$PY" "$APP_DIR/scripts/doctor.py" --repo "$APP_DIR" --env "$APP_DIR/.env" --check-db --check-imports --check-routes --sync-cockpit-env "$@"
