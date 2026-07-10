#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [ -f "$ROOT_DIR/.env.local" ]; then
	set -a
	# shellcheck disable=SC1091
	source "$ROOT_DIR/.env.local"
	set +a
fi

FRONTEND_PORT="${FRONTEND_PORT:-3000}"
FRONTEND_URL="${FRONTEND_URL:-http://127.0.0.1:${FRONTEND_PORT}}"
BACKEND_URL="${AIHUB_BASE_URL:-http://127.0.0.1:8080}"

check_status() {
	local url="$1"
	local extra_header="${2:-}"
	if [ -n "$extra_header" ]; then
		curl -sS -o /dev/null -w "%{http_code}" -H "$extra_header" "$url"
	else
		curl -sS -o /dev/null -w "%{http_code}" "$url"
	fi
}

echo "1) Frontend root: ${FRONTEND_URL}/"
frontend_code="$(check_status "${FRONTEND_URL}/")"
if [ "$frontend_code" != "200" ]; then
	echo "❌ Frontend root failed: HTTP ${frontend_code}"
	exit 1
fi
echo "✓ Frontend root alive (HTTP ${frontend_code})"

echo "2) Frontend proxy: ${FRONTEND_URL}/api/aihub/system/ping"
proxy_header=""
if [ -n "${AIHUB_API_KEY:-}" ]; then
	proxy_header="x-aihub-api-key-override: ${AIHUB_API_KEY}"
fi
proxy_code="$(check_status "${FRONTEND_URL}/api/aihub/system/ping" "$proxy_header")"
if [ "$proxy_code" != "200" ] && [ "$proxy_code" != "401" ]; then
	echo "❌ Frontend proxy failed: HTTP ${proxy_code}"
	exit 1
fi
echo "✓ Frontend proxy alive (HTTP ${proxy_code})"

echo "3) Backend direct: ${BACKEND_URL}/system/ping"
backend_header=""
if [ -n "${AIHUB_API_KEY:-}" ]; then
	backend_header="x-api-key: ${AIHUB_API_KEY}"
fi
backend_code="$(check_status "${BACKEND_URL}/system/ping" "$backend_header")"
if [ "$backend_code" != "200" ] && [ "$backend_code" != "401" ]; then
	echo "❌ Backend direct failed: HTTP ${backend_code}"
	exit 1
fi
echo "✓ Backend direct alive (HTTP ${backend_code})"

echo "✅ Health check passed"
