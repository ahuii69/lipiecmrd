#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AI-Hub — CANONICAL PRODUCT SYSTEM GATE
#
# Verifies the default product stack end-to-end (backend + transport truth +
# chat / agent / web / observability contracts). Intended for CI or pre-release.
#
# Does NOT replace: full pytest tests/ (run scripts/baseline_gate.sh for that).
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

G='\033[0;32m'; R='\033[0;31m'; Y='\033[1;33m'; NC='\033[0m'
pass() { echo -e "${G}[PASS]${NC} $*"; }
fail() { echo -e "${R}[FAIL]${NC} $*"; exit 1; }
info() { echo -e "${Y}[INFO]${NC} $*"; }

info "1/4 Python import aihub.main"
python -c "import aihub.main" || fail "import aihub.main failed"
pass "import aihub.main"

info "2/4 Pytest system_gate marker (canonical product E2E)"
VENV_DIR="${VENV_DIR:-$ROOT/.venv}"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
else
  fail "venv not found at $VENV_DIR"
fi

python -m pytest -q -m system_gate tests/test_product_system_gate.py || fail "system_gate pytest failed"
pass "pytest -m system_gate"

info "3/4 Cockpit proxy allowlist (Vitest — same JSON as backend)"
if [[ -f "$ROOT/cockpit/package.json" ]]; then
  (cd "$ROOT/cockpit" && npm run test --silent) || fail "cockpit npm run test failed"
  pass "cockpit npm run test"
else
  info "cockpit/package.json missing — skip cockpit tests"
fi

info "4/4 Related transport / surface regressions (fast)"
python -m pytest -q \
  tests/test_cockpit_proxy_allowlist.py \
  tests/test_agent_http_surface.py::test_agent_run_is_canonical_with_headers \
  tests/test_agent_http_surface.py::test_cognitive_health_observability_header \
  tests/test_chat_api.py::test_chat_turn_endpoint_and_capabilities \
  tests/test_chat_api.py::test_legacy_turn_endpoint_is_explicitly_deprecated \
  || fail "related regression pytest failed"
pass "related regression pytest"

echo ""
pass "Product system gate complete."
