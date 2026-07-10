#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Zapisuje OpenAPI JSON do export/openapi.json (dla załączników do oferty / RFI).
# Wymaga: venv z zainstalowanymi zależnościami (pip install -r requirements.txt).
#
#   ./scripts/dump_openapi.sh
#   OUT_PATH=/tmp/openapi.json ./scripts/dump_openapi.sh
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
OUT_PATH="${OUT_PATH:-$APP_DIR/export/openapi.json}"
mkdir -p "$(dirname "$OUT_PATH")"

PYTHON_CMD="python3"
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PYTHON_CMD="$APP_DIR/.venv/bin/python"
fi

export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON_CMD" - "$OUT_PATH" <<'PY'
import json
import sys
from pathlib import Path

out = Path(sys.argv[1])
try:
    from aihub.main import app
except Exception as e:
    print("dump_openapi: import aihub.main failed:", e, file=sys.stderr)
    sys.exit(2)

spec = app.openapi()
out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
print("Wrote", out)
PY
