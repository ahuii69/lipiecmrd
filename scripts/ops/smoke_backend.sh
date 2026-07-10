#!/usr/bin/env bash
# smoke_backend.sh — Kanoniczny smoke test backendu AI-Hub.
# Weryfikuje kontrakt semantyczny /chat/capabilities i /chat/turn.
# Exit 0 = PASS, Exit 1 = FAIL.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT="$(cat "$APP_DIR/data/run/aihub.port" 2>/dev/null || echo "8080")"
BASE="http://127.0.0.1:${PORT}"

# API_KEY ze źródła prawdy: plik .env
API_KEY=""
if [[ -f "$APP_DIR/.env" ]]; then
    API_KEY="$(grep '^API_KEY=' "$APP_DIR/.env" | cut -d= -f2- | tr -d '[:space:]' || true)"
fi

PASS=0
FAIL=0
ok()   { echo "  [OK]  $*"; (( PASS++ )) || true; }
fail() { echo "  [FAIL] $*"; (( FAIL++ )) || true; }

# ── 1. GET /cognitive/health (basic health check) ──────────────────────────
echo "[1] GET /cognitive/health"
if [[ -z "$API_KEY" ]]; then
    fail "API_KEY not found in .env - cannot test authenticated endpoints"
else
    RAW_HEALTH=$(curl -s -w "\n%{http_code}" \
        -H "x-api-key: ${API_KEY}" \
        "${BASE}/cognitive/health" 2>/dev/null)
    HTTP_HEALTH=$(printf '%s' "$RAW_HEALTH" | tail -1)
    BODY_HEALTH=$(printf '%s' "$RAW_HEALTH" | head -n -1)

    if [[ "$HTTP_HEALTH" != "200" ]]; then
        fail "/cognitive/health → HTTP ${HTTP_HEALTH} (expected 200)"
    else
        HEALTH_RESULT=$(printf '%s' "$BODY_HEALTH" | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    db_ok = d.get("db_schema", {}).get("ok")
    if db_ok is True:
        print("OK|db_schema.ok=true")
    else:
        print("FAIL|db_schema.ok=" + repr(db_ok))
except:
    print("FAIL|json parse error")
' 2>/dev/null || echo "FAIL|python3 parse error")
        HEALTH_STATUS="${HEALTH_RESULT%%|*}"
        HEALTH_DETAIL="${HEALTH_RESULT#*|}"
        if [[ "$HEALTH_STATUS" == "OK" ]]; then
            ok "/cognitive/health → 200  ${HEALTH_DETAIL}"
        else
            fail "/cognitive/health → 200 ale kontrakt naruszony: ${HEALTH_DETAIL}"
        fi
    fi
fi

# ── 2. GET /chat/capabilities ─────────────────────────────────────────────────
echo "[2] GET /chat/capabilities"
RAW_CAP=$(curl -s -w "\n%{http_code}" \
    -H "x-api-key: ${API_KEY}" \
    "${BASE}/chat/capabilities" 2>/dev/null)
HTTP_CAP=$(printf '%s' "$RAW_CAP" | tail -1)
BODY_CAP=$(printf '%s' "$RAW_CAP" | head -n -1)

if [[ "$HTTP_CAP" != "200" ]]; then
    fail "/chat/capabilities → HTTP ${HTTP_CAP} (expected 200)"
else
    CAP_RESULT=$(printf '%s' "$BODY_CAP" | python3 -c '
import sys, json
d = json.load(sys.stdin)
ok_flag = d.get("ok")
count   = d.get("count", len(d.get("capabilities", [])))
if ok_flag is not True:
    print("FAIL|ok=" + repr(ok_flag) + " (expected true)")
else:
    print("OK|ok=true  count=" + str(count))
' 2>/dev/null || echo "FAIL|python3 parse error")
    CAP_STATUS="${CAP_RESULT%%|*}"
    CAP_DETAIL="${CAP_RESULT#*|}"
    if [[ "$CAP_STATUS" == "OK" ]]; then
        ok "/chat/capabilities → 200  ${CAP_DETAIL}"
    else
        fail "/chat/capabilities → 200 ale kontrakt naruszony: ${CAP_DETAIL}"
    fi
fi

# ── 3. POST /chat/turn ────────────────────────────────────────────────────────
echo "[3] POST /chat/turn"
PAYLOAD='{"user_id":"mordo-smoke","session_id":"sess-next-assistant-check","message":"Odpowiedz jednym zdaniem: czy runtime działa.","mode":"chat"}'
RAW_TURN=$(curl -s -w "\n%{http_code}" \
    -X POST \
    -H "Content-Type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -d "$PAYLOAD" \
    "${BASE}/chat/turn" 2>/dev/null)
HTTP_TURN=$(printf '%s' "$RAW_TURN" | tail -1)
BODY_TURN=$(printf '%s' "$RAW_TURN" | head -n -1)

if [[ "$HTTP_TURN" != "200" ]]; then
    fail "/chat/turn → HTTP ${HTTP_TURN} (expected 200)"
else
    TURN_RESULT=$(printf '%s' "$BODY_TURN" | python3 -c '
import sys, json
d = json.load(sys.stdin)
errors = []

turn_ok = d.get("ok")
if turn_ok is not True:
    errors.append("ok=" + repr(turn_ok) + " (expected true)")

provider = d.get("provider", "")
if provider != "deepinfra":
    errors.append("provider=" + repr(provider) + " (expected deepinfra)")

trace = d.get("trace", {})
used_fallback = trace.get("used_fallback")
if used_fallback is not False:
    errors.append("trace.used_fallback=" + repr(used_fallback) + " (expected false)")

grounding = trace.get("response_grounding_mode", "")
if grounding != "tool_verified":
    errors.append("trace.response_grounding_mode=" + repr(grounding) + " (expected tool_verified)")

if errors:
    print("FAIL|" + "; ".join(errors))
else:
    print("OK|provider=" + provider + "  used_fallback=" + str(used_fallback) + "  grounding=" + grounding)
' 2>/dev/null || echo "FAIL|python3 parse error")
    TURN_STATUS="${TURN_RESULT%%|*}"
    TURN_DETAIL="${TURN_RESULT#*|}"
    if [[ "$TURN_STATUS" == "OK" ]]; then
        ok "/chat/turn → 200  ${TURN_DETAIL}"
    else
        fail "/chat/turn → 200 ale kontrakt naruszony: ${TURN_DETAIL}"
    fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
if [[ "$FAIL" -eq 0 ]]; then
    echo "SMOKE PASS: ${PASS}/${PASS} OK"
    exit 0
else
    echo "SMOKE FAIL: ${FAIL} failed, ${PASS} passed"
    exit 1
fi
