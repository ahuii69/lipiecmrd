#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AI-Hub — VERIFY RUNTIME
# Import gate + pytest + curl health (local + HTTPS)
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
PORT_FILE="$RUN_DIR/aihub.port"
HOST="${HOST:-127.0.0.1}"

G='\033[0;32m'; R='\033[0;31m'; NC='\033[0m'
pass() { echo -e "${G}[PASS]${NC} $*"; }
fail() { echo -e "${R}[FAIL]${NC} $*"; ERRORS=$((ERRORS+1)); }

ERRORS=0

# ── 1. Import gate ──
echo "── Import gate ──"
if [[ -x "$VENV_DIR/bin/python" ]]; then
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

if python -c "from aihub.main import app; print(f'routes: {len(app.routes)}')" 2>/dev/null; then
  pass "import aihub.main:app"
else
  fail "import aihub.main:app"
fi

# ── 2. Pytest ──
echo ""
echo "── pytest ──"
if python -m pytest -q tests/ 2>&1; then
  pass "pytest tests/"
else
  fail "pytest tests/"
fi

# ── 3. Local health ──
echo ""
echo "── Local health ──"
if [[ -f "$PORT_FILE" ]]; then
  PORT="$(cat "$PORT_FILE")"
  URL="http://${HOST}:${PORT}"
  if curl -fsS "$URL/system/ping" -o /dev/null 2>/dev/null; then
    pass "GET $URL/system/ping"
  else
    fail "GET $URL/system/ping (backend nie odpowiada)"
  fi
  PY=python
  [[ -x "$VENV_DIR/bin/python" ]] && PY="$VENV_DIR/bin/python"
  HUB_KEY=""
  if [[ -f "$APP_DIR/.env" ]]; then
    HUB_KEY=$("$PY" "$APP_DIR/scripts/dotenv_tool.py" hub-x-key "$APP_DIR" "$APP_DIR/.env" 2>/dev/null || true)
  fi
  if [[ -n "$HUB_KEY" ]]; then
    if curl -fsS -H "x-api-key: $HUB_KEY" "$URL/cognitive/health" -o /dev/null 2>/dev/null; then
      pass "GET $URL/cognitive/health (z x-api-key)"
    else
      fail "GET $URL/cognitive/health (z x-api-key)"
    fi
  else
    if curl -fsS "$URL/cognitive/health" -o /dev/null 2>/dev/null; then
      pass "GET $URL/cognitive/health (bez klucza)"
    else
      fail "GET $URL/cognitive/health (bez klucza)"
    fi
  fi
else
  echo "  (pominięto — brak $PORT_FILE, backend nie działa?)"
fi

# ── 4. HTTPS (Caddy) ──
echo ""
echo "── HTTPS (Caddy) ──"
DOMAIN="${DOMAIN:-}"
if [[ -f "$APP_DIR/.env" ]]; then
  DOMAIN="$(grep '^DOMAIN=' "$APP_DIR/.env" 2>/dev/null | head -1 | cut -d= -f2- || true)"
fi
if [[ -n "$DOMAIN" ]]; then
  if curl -fsS "https://${DOMAIN}/system/ping" -o /dev/null 2>/dev/null; then
    pass "GET https://${DOMAIN}/system/ping"
  else
    fail "GET https://${DOMAIN}/system/ping (Caddy/DNS/cert?)"
  fi
else
  echo "  (pominięto — brak DOMAIN w .env)"
fi

# ── Podsumowanie ──
echo ""
if [[ "$ERRORS" -eq 0 ]]; then
  pass "Wszystkie testy przeszły ✓"
  exit 0
else
  fail "$ERRORS test(ów) nie przeszło"
  exit 1
fi
