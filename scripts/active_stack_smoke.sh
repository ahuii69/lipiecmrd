#!/usr/bin/env bash
# Active stack smoke: wymaga działającego backendu (np. ./start.sh --no-frontend lub pełny stack).
# Użycie:
#   AIHUB_BASE_URL=http://127.0.0.1:8080 API_KEY=sekret ./scripts/active_stack_smoke.sh
# Klucz x-api-key: pierwsza niepusta z config/hub_key_env_names.json (jak backend/Next).
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

BASE="${AIHUB_BASE_URL:-http://127.0.0.1:8080}"
BASE="${BASE%/}"
USER_ID="${SMOKE_USER_ID:-active_stack_smoke}"
KEY="${AIHUB_API_KEY:-}"
[[ -z "${KEY}" ]] && KEY="${HUB_API_KEY:-}"
[[ -z "${KEY}" ]] && KEY="${API_KEY:-}"
[[ -z "${KEY}" ]] && KEY="${AIHUB_PROXY_TOKEN:-}"
CHAT_TIMEOUT="${SMOKE_CHAT_TIMEOUT_S:-90}"

hdr=()
if [[ -n "$KEY" ]]; then
  hdr=(-H "x-api-key: ${KEY}")
fi

json_assert_ok() {
  local file="$1"
  python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('ok') is True, d" "$file" \
    || { echo "[FAIL] Oczekiwano {\"ok\": true} w $file"; cat "$file"; return 1; }
}

req() {
  local method="$1"
  local url="$2"
  local out="$3"
  shift 3
  local code
  code=$(curl -sS -o "$out" -w "%{http_code}" -X "$method" "${hdr[@]}" "$@" "$url") || true
  if [[ "$code" != "200" ]]; then
    echo "[FAIL] $method $url → HTTP $code"
    cat "$out" 2>/dev/null || true
    return 1
  fi
  return 0
}

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "[INFO] AIHUB_BASE_URL=$BASE USER_ID=$USER_ID"

req GET "$BASE/system/ping" "$TMP/ping.json"
json_assert_ok "$TMP/ping.json"
echo "[OK] GET /system/ping"

req GET "$BASE/cockpit/health" "$TMP/ch.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('service')=='cockpit' or d.get('ok') is True, d" "$TMP/ch.json" \
  || { echo "[FAIL] cockpit health"; cat "$TMP/ch.json"; exit 1; }
echo "[OK] GET /cockpit/health"

req GET "$BASE/cockpit/schema-health" "$TMP/sh.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('ok') is True, d" "$TMP/sh.json" \
  || { echo "[FAIL] schema-health"; cat "$TMP/sh.json"; exit 1; }
echo "[OK] GET /cockpit/schema-health"

req POST "$BASE/agent/run" "$TMP/ar.json" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"${USER_ID}\",\"text\":\"smoke dry_run\",\"dry_run\":true,\"max_steps\":1,\"timeout_seconds\":15.0}"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('ok') is True, d" "$TMP/ar.json" \
  || { echo "[FAIL] agent/run"; cat "$TMP/ar.json"; exit 1; }
echo "[OK] POST /agent/run (dry_run)"

req POST "$BASE/agent/tick/${USER_ID}?max_stm=20&max_tasks=2" "$TMP/at.json" \
  -H "Content-Type: application/json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('ok') is True, d" "$TMP/at.json" \
  || { echo "[FAIL] agent/tick"; cat "$TMP/at.json"; exit 1; }
echo "[OK] POST /agent/tick/${USER_ID}"

req POST "$BASE/agent/loop" "$TMP/al.json" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"${USER_ID}\",\"text\":\"smoke loop\",\"dry_run\":true,\"max_iters\":1}"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d.get('ok') is True, d" "$TMP/al.json" \
  || { echo "[FAIL] agent/loop"; cat "$TMP/al.json"; exit 1; }
echo "[OK] POST /agent/loop (dry_run)"

code=$(curl -sS -o "$TMP/ct.json" -w "%{http_code}" -m "$CHAT_TIMEOUT" -X POST "${hdr[@]}" \
  -H "Content-Type: application/json" \
  -d "{\"user_id\":\"${USER_ID}\",\"session_id\":\"smoke\",\"message\":\"Odpowiedz jednym słowem: OK\",\"mode\":\"chat\"}" \
  "$BASE/chat/turn" || echo "000")
if [[ "$code" != "200" ]]; then
  echo "[FAIL] POST /chat/turn → HTTP $code (timeout ${CHAT_TIMEOUT}s; ustaw LLM / SMOKE_CHAT_TIMEOUT_S)"
  cat "$TMP/ct.json" 2>/dev/null || true
  exit 1
fi
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d, dict), d" "$TMP/ct.json" \
  || { echo "[FAIL] chat/turn invalid json"; exit 1; }
echo "[OK] POST /chat/turn"

echo "[PASS] active_stack_smoke — wszystkie kroki OK"
