#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AI-Hub — BASELINE GATE (active stack)
# Wymaga: 0 failed, co najmniej EXPECTED_MIN_PASSED testów passed (domyślnie 1).
# Nie sztywnej liczby „passed” — liczba testów rośnie wraz z repo.
#
# Uwagi techniczne:
# - ``set -e`` + ``OUT=$(pytest)`` kończy skrypt przy pierwszym failu testu — wyłączamy
#   ``-e`` na czas pytest i parsujemy podsumowanie (pytest zwraca !=0 przy failed).
# - Pytest drukuje np. „===== 42 passed in 12s =====” — wzorzec musi łapać ``N passed``,
#   nie tylko linię zaczynającą się od cyfry.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
EXPECTED_MIN_PASSED="${EXPECTED_MIN_PASSED:-1}"
# Szybki smoke (np. lokalnie): BASELINE_QUICK=1 bash scripts/baseline_gate.sh
if [[ "${BASELINE_QUICK:-0}" == "1" ]]; then
  PYTEST_ARGS="-q tests/test_chat_context_compose.py tests/test_chat_product_vault_and_history.py tests/test_vault_layer.py tests/test_chat_runtime.py"
else
  PYTEST_ARGS="${PYTEST_ARGS:--q tests/}"
fi

G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${G}[PASS]${NC} $*"; }
fail() { echo -e "${R}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${Y}[INFO]${NC} $*"; }

info "AI-Hub Baseline Gate (pytest $PYTEST_ARGS)"
info "Wymagane: >= ${EXPECTED_MIN_PASSED} passed, 0 failed"
echo ""

# ── Interpreter: venv (preferowane) albo PYTHON_CMD / python3 ──
PYTHON_CMD=""
if [[ -x "$VENV_DIR/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  PYTHON_CMD="python"
  if ! python -c "import cryptography" 2>/dev/null; then
    fail "W venv brak „cryptography”. Uruchom: \"$VENV_DIR/bin/pip install cryptography\""
  fi
elif [[ "${BASELINE_USE_SYSTEM_PYTHON:-0}" == "1" ]]; then
  PYTHON_CMD="${PYTHON_CMD:-python3}"
  if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    fail "Brak $PYTHON_CMD w PATH (BASELINE_USE_SYSTEM_PYTHON=1)"
  fi
  info "venv brak — pytest przez: $PYTHON_CMD (BASELINE_USE_SYSTEM_PYTHON=1)"
  if ! "$PYTHON_CMD" -c "import cryptography" 2>/dev/null; then
    fail "Brak „cryptography” dla $PYTHON_CMD. Uruchom: $PYTHON_CMD -m pip install cryptography"
  fi
else
  fail "venv not found at $VENV_DIR (ustaw BASELINE_USE_SYSTEM_PYTHON=1 albo utwórz .venv)"
fi

# ── Run tests (nie przerywaj na exit code pytest) ──
info "Running: $PYTHON_CMD -m pytest $PYTEST_ARGS"
echo ""

set +e
# shellcheck disable=SC2086
OUTPUT=$("$PYTHON_CMD" -m pytest $PYTEST_ARGS 2>&1)
PYEXIT=$?
set -e

# ── Parse: „N passed” / „M failed” w dowolnej linii podsumowania ──
_flat=$(echo "$OUTPUT" | tr '\n' ' ')
PASSED=0
FAILED=0
if echo "$_flat" | grep -qE '[0-9]+[[:space:]]+passed'; then
  PASSED=$(echo "$_flat" | grep -oE '[0-9]+[[:space:]]+passed' | tail -1 | grep -oE '^[0-9]+')
fi
if echo "$_flat" | grep -qE '[0-9]+[[:space:]]+failed'; then
  FAILED=$(echo "$_flat" | grep -oE '[0-9]+[[:space:]]+failed' | tail -1 | grep -oE '^[0-9]+')
fi

echo ""
info "pytest exit=$PYEXIT — parsed: $PASSED passed, $FAILED failed"

if [[ "$PASSED" -eq 0 ]] && ! echo "$_flat" | grep -q 'passed'; then
  echo ""
  echo "──── ostatnie 80 linii wyjścia pytest ────"
  echo "$OUTPUT" | tail -80
  echo "──────────────────────────────────────────"
  if echo "$OUTPUT" | grep -q "No module named 'cryptography'"; then
    fail "Brak pakietu cryptography dla $PYTHON_CMD — uruchom: $PYTHON_CMD -m pip install cryptography"
  fi
  if echo "$OUTPUT" | grep -q "ERROR collecting"; then
    fail "Błąd zbierania testów (pytest exit=$PYEXIT) — zobacz log powyżej"
  fi
  fail "Nie udało się odczytać liczby passed (zbiór testów / błąd pytest?)"
fi

if [[ "$FAILED" -ne 0 ]]; then
  echo ""
  echo "──── ostatnie 60 linii (fail) ────"
  echo "$OUTPUT" | tail -60
  fail "Tests failed: $FAILED — oczekiwano 0"
fi

if [[ "$PYEXIT" -ne 0 ]] && [[ "$FAILED" -eq 0 ]]; then
  echo ""
  echo "$OUTPUT" | tail -40
  fail "pytest zwrócił $PYEXIT mimo braku „failed” w podsumowaniu — sprawdź log"
fi

if [[ "$PASSED" -lt "$EXPECTED_MIN_PASSED" ]]; then
  fail "Za mało passed: $PASSED (minimum $EXPECTED_MIN_PASSED)"
fi

pass "Baseline OK: $PASSED passed, $FAILED failed ✓"
