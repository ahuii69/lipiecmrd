#!/usr/bin/env bash
# AI-Hub Final Smoke Pack — Complete operator verification
# Usage: ./final_smoke.sh [--backend-only] [--host HOST] [--port PORT]

set -euo pipefail

# Runtime directories (default: repo root when launched from scripts/ops/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
APP_DIR="${APP_DIR:-$REPO_ROOT}"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
PORT_FILE="$RUN_DIR/aihub.port"
FRONTEND_PORT_FILE="$RUN_DIR/frontend.port"

# Initialize configs
HOST="${HOST:-127.0.0.1}"
BACKEND_ONLY=false

# Auto-detect ports from runtime files (SOURCE OF TRUTH)
DETECTED_BACKEND_PORT=$(cat "$PORT_FILE" 2>/dev/null || echo "")
DETECTED_FRONTEND_PORT=$(cat "$FRONTEND_PORT_FILE" 2>/dev/null || echo "")

# Initialize with detected ports or fallback
PORT="${PORT:-${DETECTED_BACKEND_PORT:-8080}}"
FRONTEND_PORT="${FRONTEND_PORT:-${DETECTED_FRONTEND_PORT:-3000}}"

# Parse args (CLI overrides auto-detection)
while (($#)); do
  case "$1" in
    --backend-only) BACKEND_ONLY=true ;;
    --host) shift; HOST="$1" ;;
    --port) shift; PORT="$1" ;;
    --frontend-port) shift; FRONTEND_PORT="$1" ;;
    *) echo "Unknown: $1"; exit 2 ;;
  esac
  shift
done

# Endpoints
BACKEND_BASE="http://${HOST}:${PORT}"
FRONTEND_BASE="http://${HOST}:${FRONTEND_PORT}"

# Port detection debug
echo "🔍 PORT DETECTION:"
echo "  Backend  → $PORT  (detected: ${DETECTED_BACKEND_PORT:-NONE}, file: $PORT_FILE)"
echo "  Frontend → $FRONTEND_PORT (detected: ${DETECTED_FRONTEND_PORT:-NONE}, file: $FRONTEND_PORT_FILE)"
echo

# Counters
PASS=0
FAIL=0
FAILED_TESTS=()

# Test functions
run_test() {
  local name="$1"
  local command="$2"
  echo -n "[$name] "

  if eval "$command" &>/dev/null; then
    echo "PASS"
    ((PASS++))
  else
    echo "FAIL"
    ((FAIL++))
    FAILED_TESTS+=("$name")
  fi
}

# Get API key for auth tests
get_api_key() {
  if [[ -f ".env" ]]; then
    grep "^API_KEY=" .env | cut -d= -f2- | tr -d '[:space:]' || echo ""
  else
    echo ""
  fi
}

echo "============================================"
echo "AI-HUB FINAL SMOKE PACK"
echo "============================================"
echo "Backend:  $BACKEND_BASE"
[[ "$BACKEND_ONLY" != "true" ]] && echo "Frontend: $FRONTEND_BASE"
echo ""

# BACKEND TESTS
echo "🔧 BACKEND TESTS"
run_test "HEALTH" "curl -s --max-time 5 '$BACKEND_BASE/system/ping'"
run_test "DOCS" "curl -s --max-time 5 '$BACKEND_BASE/docs' | grep -q 'FastAPI\|OpenAPI\|swagger'"

# Basic chat test with optional API key
API_KEY=$(get_api_key)
if [[ -n "$API_KEY" ]]; then
  CHAT_CMD="curl -s --max-time 10 -X POST '$BACKEND_BASE/chat/turn' -H 'Content-Type: application/json' -H 'x-api-key: $API_KEY' -d '{\"user_id\":\"smoke\",\"session_id\":\"smoke\",\"message\":\"ping\"}' | grep -q '\"response\"'"
else
  CHAT_CMD="curl -s --max-time 10 -X POST '$BACKEND_BASE/chat/turn' -H 'Content-Type: application/json' -d '{\"user_id\":\"smoke\",\"session_id\":\"smoke\",\"message\":\"ping\"}' | grep -q 'response\|error'"
fi
run_test "CHAT" "$CHAT_CMD"

# FRONTEND TESTS (if not backend-only mode)
if [[ "$BACKEND_ONLY" != "true" ]]; then
  echo ""
  echo "🌐 FRONTEND TESTS"
  run_test "ROOT" "curl -s --max-time 5 '$FRONTEND_BASE/'"
  run_test "PROXY" "curl -s --max-time 5 '$FRONTEND_BASE/api/aihub/system/ping'"
fi

# RESULTS
echo ""
echo "============================================"
if [[ $FAIL -eq 0 ]]; then
  echo "✅ ALL TESTS PASSED ($PASS/$((PASS + FAIL)))"
  echo "🚀 AI-Hub is ready for operation!"
else
  echo "❌ SOME TESTS FAILED ($PASS/$((PASS + FAIL)))"
  echo ""
  echo "Failed tests: ${FAILED_TESTS[*]}"
  echo ""
  echo "🔍 TROUBLESHOOTING:"

  # Smart diagnostics based on failures
  for test in "${FAILED_TESTS[@]}"; do
    case "$test" in
      HEALTH|DOCS|CHAT)
        echo "- Backend issue: Check if backend is running"
        echo "  → Try: 'ps aux | grep uvicorn' or './start.sh --no-frontend'"
        break ;;
    esac
  done

  for test in "${FAILED_TESTS[@]}"; do
    case "$test" in
      ROOT|PROXY)
        echo "- Frontend issue: Check if frontend is running"
        echo "  → Try: 'ps aux | grep \"npm run dev\"' or './start.sh'"
        break ;;
    esac
  done

  echo "- Check logs: 'tail -f logs/aihub.error.log'"
  echo "- Manual test: curl -v $BACKEND_BASE/system/ping"
fi

echo "============================================"
exit $FAIL
