#!/usr/bin/env bash
# Tworzy MORDAFULL.ZIP w katalogu głównym projektu: kod + konfiguracja,
# bez data/, logs/, cache'ów Pythona i artefaktów buildów.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PARENT="$(cd "$REPO_ROOT/.." && pwd)"
NAME="$(basename "$REPO_ROOT")"
OUT="$REPO_ROOT/MORDAFULL.ZIP"

cd "$PARENT"

find "$NAME" -type f \
  ! -path "$NAME/data/*" \
  ! -path "$NAME/logs/*" \
  ! -path '*/__pycache__/*' \
  ! -name '*.pyc' \
  ! -path '*/.pytest_cache/*' \
  ! -path '*/.mypy_cache/*' \
  ! -path '*/node_modules/*' \
  ! -path '*/.venv/*' \
  ! -path '*/venv/*' \
  ! -path "$NAME/.git/*" \
  ! -path '*/.ruff_cache/*' \
  ! -name '.DS_Store' \
  ! -path '*/htmlcov/*' \
  ! -path '*/dist/*' \
  ! -path '*/build/*' \
  ! -name '.coverage' \
  2>/dev/null | zip -q "$OUT" -@

echo "OK: $OUT ($(du -h "$OUT" | cut -f1))"
