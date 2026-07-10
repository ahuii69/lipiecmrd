#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOST="${HOST:-127.0.0.1}"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
PORT_FILE="${PORT_FILE:-$RUN_DIR/aihub.port}"
API_KEY_HEADER_NAME="${API_KEY_HEADER_NAME:-X-API-Key}"
API_KEY_VALUE="${API_KEY_VALUE:-${API_KEY:-}}"

port="$(cat "$PORT_FILE" 2>/dev/null || echo "8080")"
base="http://${HOST}:${port}"

hdr=()
if [[ -n "${API_KEY_VALUE:-}" ]]; then
  hdr=(-H "${API_KEY_HEADER_NAME}: ${API_KEY_VALUE}")
fi

echo "Checking: $base/health"
curl -fsS "${hdr[@]}" "$base/health" | head -c 200 || true
echo
echo "Checking: $base/openapi.json (search self_heal op ids)"
curl -fsS "${hdr[@]}" "$base/openapi.json" | rg -n "\"operationId\":\"self_heal_(run|rollback|status)\"" || true
echo "OK ✅"
