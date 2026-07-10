#!/usr/bin/env bash
# sanity.sh — Post-deploy sanity check for AI-Hub
# Usage: ./sanity.sh [--port PORT] [--host HOST] [--api-key KEY]
# Exit 0 = all checks passed; Exit 1 = at least one failed
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
PORT_FILE="${PORT_FILE:-$RUN_DIR/aihub.port}"
LOG_DIR="${LOG_DIR:-$APP_DIR/data/logs}"
UVICORN_LOG="${UVICORN_LOG:-$LOG_DIR/uvicorn.log}"
API_KEY_HEADER_NAME="${API_KEY_HEADER_NAME:-X-API-Key}"
API_KEY_VALUE="${API_KEY_VALUE:-${API_KEY:-}}"
SANITY_USER="${SANITY_USER:-sanity_check}"
COGNITIVE_DEBUG_ENABLED="${AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT:-0}"

# Override from args
while (($#)); do
  case "${1:-}" in
    --port)  shift; PORT_OVERRIDE="$1" ;;
    --host)  shift; HOST="$1" ;;
    --api-key) shift; API_KEY_VALUE="$1" ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
  shift
done

PORT="${PORT_OVERRIDE:-$(cat "$PORT_FILE" 2>/dev/null || echo "8080")}"
BASE="http://${HOST}:${PORT}"

HDR=()
if [[ -n "${API_KEY_VALUE:-}" ]]; then
  HDR=(-H "${API_KEY_HEADER_NAME}: ${API_KEY_VALUE}")
fi

PASS=0
FAIL=0

check() {
  local name="$1"
  shift
  echo -n "[$name] "
  if "$@"; then
    echo "PASS"
    PASS=$((PASS + 1))
  else
    echo "FAIL"
    FAIL=$((FAIL + 1))
  fi
}

# --- CHECK 1: /cognitive/health ---
health_ok() {
  local resp
  resp=$(curl -fsS "${HDR[@]}" "$BASE/cognitive/health" 2>&1) || return 1

  # Verify JSON has db_schema.ok == true
  echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d['db_schema']['ok'] is True, 'db_schema not ok: ' + str(d['db_schema'])
print('  schema OK, status=' + d['status'])
"
}
check "cognitive/health" health_ok

# --- CHECK 2: POST /cognitive/decide (debug-only, optional) ---
if [[ "${COGNITIVE_DEBUG_ENABLED}" == "1" ]]; then
  decide_ok() {
    local resp
    resp=$(curl -fsS -X POST "${HDR[@]}" \
      -H "Content-Type: application/json" \
      -d '{"message":"sanity check ping","context":{}}' \
      "$BASE/cognitive/decide?user_id=${SANITY_USER}" 2>&1) || return 1

    echo "$resp" | python3 -c "
import sys, json
d = json.load(sys.stdin)
assert d.get('canonical_runtime') is False, 'expected non-canonical debug endpoint'
assert d.get('debug_only') is True, 'expected debug_only=true'
assert d.get('bypass') is True, 'expected bypass=true'
assert 'action_type' in d, 'missing action_type'
print('  debug action=' + d['action_type'] + ' confidence=' + str(d.get('confidence','')))
"
  }
  check "cognitive/decide(debug)" decide_ok
else
  echo "[cognitive/decide(debug)] SKIP (AIHUB_ENABLE_COGNITIVE_DEBUG_ENDPOINT!=1)"
fi

# --- CHECK 3: GC via internal call ---
gc_ok() {
  local resp
  resp=$(python3 -c "
import os, sys
sys.path.insert(0, '${APP_DIR}')
os.environ.setdefault('DB_PATH', '${APP_DIR}/data/aihub.sqlite3')
os.environ.setdefault('DATA_DIR', '${APP_DIR}/data')
from aihub.memory_gc import collect_garbage
stats = collect_garbage('${SANITY_USER}')
if 'error' in stats:
    print('ERROR: ' + stats['error'], file=sys.stderr)
    sys.exit(1)
print('  deleted=%d archived=%d' % (stats.get('deleted',0), stats.get('archived',0)))
" 2>&1) || return 1
  echo "$resp"
}
check "memory_gc" gc_ok

# --- SUMMARY ---
echo ""
echo "=== SANITY RESULT: ${PASS} passed, ${FAIL} failed ==="

if [[ "$FAIL" -gt 0 ]]; then
  echo ""
  echo "--- Last 200 lines of log ---"
  tail -n 200 "$UVICORN_LOG" 2>/dev/null || echo "(no log file)"
  exit 1
fi

exit 0
