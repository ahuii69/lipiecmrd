#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Zapisuje pip freeze do export/requirements-freeze.txt (SBOM-light dla Pythona).
# Uruchamiaj na tym samym venv co produkcja / CI.
#
#   ./scripts/sbom_python_freeze.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
OUT_PATH="${OUT_PATH:-$APP_DIR/export/requirements-freeze.txt}"
mkdir -p "$(dirname "$OUT_PATH")"

PYTHON_CMD="python3"
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PYTHON_CMD="$APP_DIR/.venv/bin/python"
fi

"$PYTHON_CMD" -m pip freeze >"$OUT_PATH"
echo "Wrote $OUT_PATH ($("$PYTHON_CMD" -m pip freeze | wc -l) lines)"
