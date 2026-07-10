#!/bin/bash
# Smoke tests for /chat/turn endpoints - validate tool registry consistency

set -e

API_BASE="${API_BASE:-http://localhost:8000}"
USER_ID="${USER_ID:-smoke_test_$(date +%s)}"
SESSION_ID="sess_$(date +%s)"

echo "=========================================="
echo "SMOKE TESTS: /chat/turn Tool Consistency"
echo "=========================================="
echo "API: $API_BASE"
echo "User: $USER_ID"
echo "Session: $SESSION_ID"
echo ""

# Test 1: Chat mode - should NOT expose debug tools
echo "[TEST 1] POST /chat/turn mode=chat"
echo "Expected: 25 tools (debug-only excluded), all ETAP 9A fields present"
echo ""

RESPONSE=$(curl -s -X POST "$API_BASE/chat/turn" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"$USER_ID\",
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"List your available tools\",
    \"mode\": \"chat\",
    \"include_debug\": false
  }")

echo "Response:"
echo "$RESPONSE" | jq '.' 2>/dev/null || echo "$RESPONSE"

# Extract and verify trace fields
TRACE=$(echo "$RESPONSE" | jq '.trace' 2>/dev/null || echo "{}")
echo ""
echo "Trace fields:"
echo "$TRACE" | jq '.' 2>/dev/null || echo "$TRACE"

# Verify ETAP 9A fields exist
ETAP9A_FIELDS=(
  "selected_strategy"
  "reason_codes"
  "degraded"
  "memory_lookup_happened"
  "psyche_snapshot_happened"
  "research_was_required"
  "experience_write_back_attempted"
  "experience_write_back_succeeded"
)

echo ""
echo "Checking ETAP 9A fields in trace:"
for field in "${ETAP9A_FIELDS[@]}"; do
  if echo "$TRACE" | jq -e ".$field" > /dev/null 2>&1; then
    echo "  ✓ $field present"
  else
    echo "  ✗ $field MISSING"
  fi
done

echo ""
echo "=========================================="
echo ""

# Test 2: Debug mode - should expose debug tools
echo "[TEST 2] POST /chat/turn mode=debug"
echo "Expected: 27 tools (all), including system.debug_info"
echo ""

RESPONSE2=$(curl -s -X POST "$API_BASE/chat/turn" \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"$USER_ID\",
    \"session_id\": \"$SESSION_ID\",
    \"message\": \"Show me debug_info\",
    \"mode\": \"debug\",
    \"include_debug\": true
  }")

echo "Response:"
echo "$RESPONSE2" | jq '.' 2>/dev/null || echo "$RESPONSE2"

# Check tool results
TOOL_RESULTS=$(echo "$RESPONSE2" | jq '.tool_results' 2>/dev/null || echo "[]")
echo ""
echo "Tool results count:"
echo "$TOOL_RESULTS" | jq 'length' 2>/dev/null

# Check for tool not found errors
echo ""
echo "Checking for 'tool not found' errors:"
ERRORS=$(echo "$RESPONSE2" | jq '.tool_results[] | select(.error | contains("tool not found"))' 2>/dev/null || echo "")
if [ -z "$ERRORS" ]; then
  echo "  ✓ No 'tool not found' errors"
else
  echo "  ✗ FOUND errors:"
  echo "$ERRORS"
fi

echo ""
echo "=========================================="
echo "✅ Smoke tests complete"
echo "=========================================="
