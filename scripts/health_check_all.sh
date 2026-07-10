#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Szybka weryfikacja po deployu: ping + health backendu + health Cockpit (HTTP).
# Użycie:
#   ./scripts/health_check_all.sh
#   BASE_URL=http://127.0.0.1:8080 COCKPIT_URL=http://127.0.0.1:3000 ./scripts/health_check_all.sh
#
# Gdy BASE_URL / COCKPIT_URL nie ustawione — próba odczytu portów z data/run/*.port
# (jak po ./start.sh w katalogu repo).
# Exit 0 tylko gdy wszystkie kroki HTTP zwrócą sukces.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

G='\033[0;32m'; R='\033[0;31m'; NC='\033[0m'
ok() { echo -e "${G}[OK]${NC} $*"; }
bad() { echo -e "${R}[FAIL]${NC} $*" >&2; }

resolve_base_url() {
  if [[ -n "${BASE_URL:-}" ]]; then
    echo "${BASE_URL%/}"
    return
  fi
  local p="$APP_DIR/data/run/aihub.port"
  if [[ -f "$p" ]]; then
    echo "http://127.0.0.1:$(tr -d ' \n' <"$p")"
    return
  fi
  echo "http://127.0.0.1:8080"
}

resolve_cockpit_url() {
  if [[ -n "${COCKPIT_URL:-}" ]]; then
    echo "${COCKPIT_URL%/}"
    return
  fi
  local p="$APP_DIR/data/run/frontend.port"
  if [[ -f "$p" ]]; then
    echo "http://127.0.0.1:$(tr -d ' \n' <"$p")"
    return
  fi
  echo "http://127.0.0.1:3000"
}

BASE="$(resolve_base_url)"
COCKPIT="$(resolve_cockpit_url)"

# Hub key: zawsze z repo .env jeśli jest wpis (unika 401 gdy w shellu został stary/zły API_KEY).
if [[ -f "$APP_DIR/.env" ]]; then
  _health_hub_key="$(grep '^API_KEY=' "$APP_DIR/.env" 2>/dev/null | cut -d= -f2- | tr -d '\r' || true)"
  if [[ -n "$_health_hub_key" ]]; then
    API_KEY="$_health_hub_key"
  fi
fi

failures=0

http_get() {
  local name="$1" url="$2"
  if curl -fsS --max-time "${HEALTH_CURL_TIMEOUT:-15}" "$url" >/dev/null; then
    ok "$name → $url"
  else
    bad "$name → $url"
    failures=$((failures + 1))
  fi
}

# Endpointy /cognitive/* i /cockpit/* na hubie wymagają klucza (jak smoke_backend.sh).
http_get_hub() {
  local name="$1" url="$2"
  local args=(-fsS --max-time "${HEALTH_CURL_TIMEOUT:-15}")
  if [[ -n "${API_KEY:-}" ]]; then
    args+=(-H "x-api-key: ${API_KEY}")
  fi
  if curl "${args[@]}" "$url" >/dev/null; then
    ok "$name → $url"
  else
    bad "$name → $url"
    failures=$((failures + 1))
  fi
}

echo "[health_check_all] BASE_URL=$BASE COCKPIT_URL=$COCKPIT"
http_get "system/ping" "$BASE/system/ping"
http_get_hub "cognitive/health" "$BASE/cognitive/health"
http_get_hub "cockpit/health" "$BASE/cockpit/health"
http_get_hub "cockpit/schema-health" "$BASE/cockpit/schema-health"
if [[ "${SKIP_COCKPIT_CHECK:-0}" == "1" ]]; then
  echo "[health_check_all] SKIP_COCKPIT_CHECK=1 — pomijam HTTP frontu"
else
  http_get "cockpit_user_page" "$COCKPIT/user"
fi

if [[ "$failures" -ne 0 ]]; then
  bad "$failures check(s) failed"
  exit 1
fi
ok "all checks passed"
exit 0
