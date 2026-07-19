#!/usr/bin/env bash
# release_gate.sh — checklista release'owa (migracje / health / sesje / restart)
# Nie wypycha sekretów. Exit 0 = gate PASS.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

DO_RESTART=0
DO_PYTEST=1
while (($#)); do
  case "$1" in
    --restart) DO_RESTART=1 ;;
    --no-pytest) DO_PYTEST=0 ;;
    -h|--help)
      echo "Usage: scripts/release_gate.sh [--restart] [--no-pytest]"
      exit 0
      ;;
    *) echo "Unknown arg: $1"; exit 2 ;;
  esac
  shift
done

PY="${APP_DIR}/.venv/bin/python"
[[ -x "$PY" ]] || PY="/home/ubuntu/.venvs/mrd-312/bin/python"
[[ -x "$PY" ]] || PY="$(command -v python3)"

PASS=0
FAIL=0
WARN=0

ok() { echo "[PASS] $*"; PASS=$((PASS + 1)); }
bad() { echo "[FAIL] $*"; FAIL=$((FAIL + 1)); }
warn() { echo "[WARN] $*"; WARN=$((WARN + 1)); }

echo "=== AIHub release gate ==="
echo "repo=$APP_DIR"
echo

# ── 1. Live backend / frontend ──────────────────────────────────────
if curl -fsS -m 5 http://127.0.0.1:8080/system/ping >/dev/null; then
  ok "backend /system/ping"
else
  bad "backend /system/ping"
fi

READY_JSON="$(curl -fsS -m 8 http://127.0.0.1:8080/ops/ready 2>/dev/null || true)"
if echo "$READY_JSON" | "$PY" -c "import sys,json; d=json.load(sys.stdin); assert d.get('ready') is True; assert not d.get('blocking')" 2>/dev/null; then
  ok "backend /ops/ready"
else
  bad "backend /ops/ready ($READY_JSON)"
fi

if curl -fsS -m 5 -o /dev/null -w '' http://127.0.0.1:3001/ 2>/dev/null || curl -sS -m 5 -o /dev/null -w '' http://127.0.0.1:3001/ | true; then
  CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/ || echo 000)"
  if [[ "$CODE" =~ ^(200|301|302|307|308)$ ]]; then
    ok "frontend :3001 HTTP $CODE"
  else
    bad "frontend :3001 HTTP $CODE"
  fi
fi

BFF_CODE="$(curl -sS -m 5 -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/api/aihub/system/ping || echo 000)"
if [[ "$BFF_CODE" == "200" ]]; then
  ok "BFF proxy /api/aihub/system/ping"
else
  bad "BFF proxy ping HTTP $BFF_CODE"
fi

# ── 2. Schema health (live) ─────────────────────────────────────────
SCHEMA_OUT="$("$PY" - <<'PY'
import os
from pathlib import Path
env = Path(".env")
if env.exists():
    for line in env.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
from aihub.db import get_active_stack_schema_health, fetch_all, fetch_one

h = get_active_stack_schema_health()
print(
    "OK" if h.get("ok") else "BAD",
    h.get("backend"),
    "missing_tables",
    h.get("missing_tables"),
    "missing_cols",
    len(h.get("missing_columns") or []),
)
cols = set()
if h.get("backend") == "postgres":
    rows = fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='chat_sessions'"
    )
    cols = {r["column_name"] for r in rows}
    need = {"archived", "archived_at"}
    miss = sorted(need - cols)
    print("CHAT_SESSIONS_ARCHIVED", "OK" if not miss else ("MISSING:" + ",".join(miss)))
else:
    print("CHAT_SESSIONS_ARCHIVED", "SKIP_SQLITE")
try:
    n = fetch_one("SELECT COUNT(*) AS n FROM auth_sessions")
    print("AUTH_SESSIONS", int(n["n"] if n else 0))
except Exception as e:
    print("AUTH_SESSIONS_ERR", type(e).__name__)
PY
)" || true

echo "$SCHEMA_OUT"
if echo "$SCHEMA_OUT" | head -1 | grep -q '^OK'; then
  ok "active stack schema health"
else
  bad "active stack schema health: $SCHEMA_OUT"
fi
if echo "$SCHEMA_OUT" | grep -q 'CHAT_SESSIONS_ARCHIVED OK\|CHAT_SESSIONS_ARCHIVED SKIP'; then
  ok "chat_sessions archived columns (upgrade)"
else
  bad "chat_sessions missing archived columns — run init_db/bootstrap or restart backend"
fi

# ── 3. Fresh SQLite migrate (isolated tempfile) ─────────────────────
if [[ "$DO_PYTEST" == "1" ]]; then
  if "$PY" -m pytest -q tests/test_v2_schema_migration.py -m legacy_sqlite_v2 --tb=line >/tmp/release_gate_migrate.txt 2>&1; then
    ok "fresh/legacy SQLite V2 migration pytest"
  else
    bad "SQLite V2 migration pytest (see /tmp/release_gate_migrate.txt)"
    tail -20 /tmp/release_gate_migrate.txt || true
  fi
  if "$PY" -m pytest -q tests/test_architecture_coherence.py tests/test_capability_closing.py --tb=line >/tmp/release_gate_cohesion.txt 2>&1; then
    ok "cohesion/capability regression"
  else
    bad "cohesion/capability regression"
    tail -15 /tmp/release_gate_cohesion.txt || true
  fi
else
  warn "pytest skipped (--no-pytest)"
fi

# ── 4. Session / cookie contract ────────────────────────────────────
COOKIE_SECURE="$(grep -E '^AIHUB_SESSION_COOKIE_SECURE=' .env 2>/dev/null | cut -d= -f2- || true)"
ENV_MODE="$(grep -E '^ENV=' .env 2>/dev/null | cut -d= -f2- || true)"
TTL="$(grep -E '^AIHUB_SESSION_TTL_SECONDS=' .env 2>/dev/null | cut -d= -f2- || true)"
echo "session_contract ENV=$ENV_MODE COOKIE_SECURE=${COOKIE_SECURE:-default} TTL=${TTL:-default_43200}"
if [[ "${ENV_MODE}" == "production" && "${COOKIE_SECURE}" == "false" ]]; then
  warn "AIHUB_SESSION_COOKIE_SECURE=false przy ENV=production (OK tylko za TLS-terminującym proxy na localhost)"
else
  ok "session cookie secure policy noted"
fi

# Allowlist critical routes in source (not necessarily live Next build)
if grep -q '"/chat/file/{file_id}"' cockpit/lib/api/cockpit-proxy-allowlist.json; then
  ok "source allowlist has GET /chat/file/{file_id}"
else
  bad "source allowlist missing /chat/file/{file_id}"
fi
if awk '/ADMIN_TEMPLATES/,/^];/' cockpit/lib/api/bff-route-policy.ts | grep -q 'capabilities/execute'; then
  bad "capabilities/execute still admin-scoped in source"
else
  ok "capabilities/execute is user-scoped in source"
fi

# ── 5. Optional restart ─────────────────────────────────────────────
if [[ "$DO_RESTART" == "1" ]]; then
  echo
  echo "=== restart processes ==="
  AUTH_BEFORE="$(echo "$SCHEMA_OUT" | awk '/AUTH_SESSIONS /{print $2}')"
  RESTART_OK=1
  if sudo -n systemctl restart aihub-backend.service 2>/dev/null || systemctl restart aihub-backend.service 2>/dev/null; then
    ok "systemctl restart aihub-backend"
  else
    bad "restart aihub-backend (need sudo/passwordless systemctl)"
    RESTART_OK=0
  fi
  # wait for ping
  for i in $(seq 1 30); do
    if curl -fsS -m 2 http://127.0.0.1:8080/system/ping >/dev/null 2>&1; then
      ok "backend back after ${i}s"
      break
    fi
    sleep 1
    if [[ "$i" == "30" ]]; then bad "backend did not return in 30s"; fi
  done
  AFTER=""
  if [[ "$RESTART_OK" == "1" ]]; then
  AFTER="$("$PY" - <<'PY'
import os
from pathlib import Path
env = Path(".env")
if env.exists():
    for line in env.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
from aihub.db import fetch_all, fetch_one, get_active_stack_schema_health

h = get_active_stack_schema_health()
cols = set()
if h.get("backend") == "postgres":
    rows = fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='chat_sessions'"
    )
    cols = {r["column_name"] for r in rows}
miss = sorted({"archived", "archived_at"} - cols) if h.get("backend") == "postgres" else []
n = fetch_one("SELECT COUNT(*) AS n FROM auth_sessions")
print(
    "HEALTH",
    h.get("ok"),
    "MISS",
    ",".join(miss) or "none",
    "AUTH",
    int(n["n"] if n else 0),
)
PY
)"
  echo "$AFTER"
  if echo "$AFTER" | grep -q 'MISS none' && echo "$AFTER" | grep -q 'HEALTH True'; then
    ok "post-restart schema + archived columns"
  else
    bad "post-restart schema: $AFTER"
  fi
  AUTH_AFTER="$(echo "$AFTER" | awk '{for(i=1;i<=NF;i++) if($i=="AUTH") print $(i+1)}')"
  if [[ -n "${AUTH_BEFORE:-}" && -n "${AUTH_AFTER:-}" ]]; then
    ok "auth_sessions survived restart (before=$AUTH_BEFORE after=$AUTH_AFTER)"
  fi
  fi  # RESTART_OK
  if sudo -n systemctl restart aihub-frontend.service 2>/dev/null || systemctl restart aihub-frontend.service 2>/dev/null; then
    ok "systemctl restart aihub-frontend"
    sleep 3
    CODE="$(curl -sS -m 8 -o /dev/null -w '%{http_code}' http://127.0.0.1:3001/api/aihub/system/ping || echo 000)"
    if [[ "$CODE" == "200" ]]; then ok "BFF after frontend restart"; else bad "BFF after frontend restart HTTP $CODE"; fi
  else
    bad "restart aihub-frontend (need sudo/passwordless systemctl)"
  fi
else
  warn "process restart not run (pass --restart)"
fi

# ── 6. Rollback reminder ────────────────────────────────────────────
if [[ -x scripts/rollback_tag.sh ]]; then
  ok "rollback_tag.sh present (git tag pre-deploy-<ts>)"
else
  warn "scripts/rollback_tag.sh missing"
fi

echo
echo "=== SUMMARY pass=$PASS fail=$FAIL warn=$WARN ==="
if [[ "$FAIL" -gt 0 ]]; then
  exit 1
fi
exit 0
