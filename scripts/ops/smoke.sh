#!/usr/bin/env bash
# AI-Hub Operator Smoke Pack — Final verification
# Usage: ./scripts/ops/smoke.sh [--host HOST] [--port PORT]

set -euo pipefail

# Runtime directories (repo root = two levels above this script)
_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_REPO_ROOT="$(cd "$_SCRIPT_DIR/../.." && pwd)"
APP_DIR="${APP_DIR:-$_REPO_ROOT}"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
PORT_FILE="$RUN_DIR/aihub.port"
FRONTEND_PORT_FILE="$RUN_DIR/frontend.port"

HOST="${HOST:-127.0.0.1}"

# Auto-detect ports from runtime files (SOURCE OF TRUTH)
DETECTED_BACKEND_PORT=$(cat "$PORT_FILE" 2>/dev/null || echo "")
DETECTED_FRONTEND_PORT=$(cat "$FRONTEND_PORT_FILE" 2>/dev/null || echo "")

PORT="${PORT:-${DETECTED_BACKEND_PORT:-8080}}"
FRONTEND_PORT="${FRONTEND_PORT:-${DETECTED_FRONTEND_PORT:-3000}}"

# Override from args
while (($#)); do
  case "$1" in
    --host) shift; HOST="$1" ;;
    --port) shift; PORT="$1" ;;
    --frontend-port) shift; FRONTEND_PORT="$1" ;;
    *) echo "Unknown: $1"; exit 2 ;;
  esac
  shift
done

BACKEND_BASE="http://${HOST}:${PORT}"
FRONTEND_BASE="http://${HOST}:${FRONTEND_PORT}"

PASS=0
FAIL=0

test_backend_health() {
  echo -n "[BACKEND_HEALTH] $BACKEND_BASE/system/ping ... "
  if curl -s --max-time 5 "$BACKEND_BASE/system/ping" >/dev/null; then
    echo "PASS"
    ((PASS++))
  else
    echo "FAIL"
    ((FAIL++))
  fi
}

test_backend_docs() {
  echo -n "[BACKEND_DOCS] $BACKEND_BASE/docs ... "
  if curl -s --max-time 5 "$BACKEND_BASE/docs" >/dev/null; then
    echo "PASS"
    ((PASS++))
  else
    echo "FAIL"
    ((FAIL++))
  fi
}

test_frontend_root() {
  echo -n "[FRONTEND_ROOT] $FRONTEND_BASE/ ... "
  if curl -s --max-time 5 "$FRONTEND_BASE/" >/dev/null; then
    echo "PASS"
    ((PASS++))
  else
    echo "FAIL"
    ((FAIL++))
  fi
}

test_frontend_proxy() {
  echo -n "[FRONTEND_PROXY] $FRONTEND_BASE/api/aihub/system/ping ... "
  if curl -s --max-time 5 "$FRONTEND_BASE/api/aihub/system/ping" >/dev/null; then
    echo "PASS"
    ((PASS++))
  else
    echo "FAIL"
    ((FAIL++))
  fi
}

echo "🔍 PORT DETECTION:"
echo "  Backend  → $PORT  (detected: ${DETECTED_BACKEND_PORT:-NONE}, file: $PORT_FILE)"
echo "  Frontend → $FRONTEND_PORT (detected: ${DETECTED_FRONTEND_PORT:-NONE}, file: $FRONTEND_PORT_FILE)"
echo

echo "🚀 SMOKE TESTS:"
test_backend_health
test_backend_docs
test_frontend_root
test_frontend_proxy

echo ""
echo "============================================"
if [[ $FAIL -eq 0 ]]; then
  echo "✅ SMOKE RESULT: $PASS passed, $FAIL failed — ALL OK"
  echo "============================================"
  exit 0
else
  echo "❌ SMOKE RESULT: $PASS passed, $FAIL failed — CHECK FAILURES"
  echo "============================================"
  echo ""
  echo "TROUBLESHOOTING:"
  if ! curl -s --max-time 2 "$BACKEND_BASE/system/ping" >/dev/null; then
    echo "- Backend down: Start with './start.sh --no-frontend'"
  fi
  if ! curl -s --max-time 2 "$FRONTEND_BASE/" >/dev/null; then
    echo "- Frontend down: Start with './start.sh' (full stack)"
  fi
  echo "- Check logs: 'tail -f logs/aihub.error.log'"
  echo "- Check processes: 'ps aux | grep -E \"uvicorn|npm run dev\"'"
  exit 1
fi
