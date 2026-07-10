#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR"

# === CONFIG (defaults — overridden by .env below) ===
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"
HOST="${HOST:-127.0.0.1}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
PID_FILE="$RUN_DIR/aihub.pid"
PORT_FILE="$RUN_DIR/aihub.port"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
FRONTEND_PORT_FILE="$RUN_DIR/frontend.port"
UVICORN_LOG="$LOG_DIR/aihub.log"
ERROR_LOG="$LOG_DIR/aihub.error.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
APP_IMPORT="${APP_IMPORT:-aihub.main:app}"
WORKERS="${WORKERS:-1}"
ENV_FILE="$APP_DIR/.env"
COCKPIT_DIR="$APP_DIR/cockpit"
COCKPIT_ENV="$COCKPIT_DIR/.env"
NODE_BIN="${NODE_BIN:-node}"
NPM_BIN="${NPM_BIN:-npm}"
# Max czas na pierwszy sukces /system/ping (init_db, bootstrap PG, opcjonalnie długi import SQLite→PG).
# Przy DB_BACKEND=postgres pełny import public.* z dużego SQLite potrafi przekroczyć 10–15 min.
START_BACKEND_WAIT_SEC="${START_BACKEND_WAIT_SEC:-1800}"

# === FLAGS ===
NO_INSTALL=0
NO_FRONTEND=0
FORCE_KILL=0
DO_CLEAN=0
# 0 = next dev (domyślnie); 1 = npm run build && next start
PROD_FRONTEND=0
SKIP_DOCTOR=0
PROFILE_MODE=""

# === COLORS ===
G='\033[0;32m'
Y='\033[0;33m'
R='\033[0;31m'
M='\033[0;35m'
NC='\033[0m'

info()  { echo -e "${G}[INFO]${NC} $*"; }
warn()  { echo -e "${Y}[WARN]${NC} $*"; }
err()   { echo -e "${R}[ERR]${NC}  $*" >&2; }
dbg()   { echo -e "${M}[DBG]${NC}  $*"; }

# === PARSE ARGS ===
while (($#)); do
  case "$1" in
    --no-install) NO_INSTALL=1; shift ;;
    --no-frontend) NO_FRONTEND=1; shift ;;
    --force-kill) FORCE_KILL=1; shift ;;
    --clean) DO_CLEAN=1; shift ;;
    --prod-frontend) PROD_FRONTEND=1; shift ;;
    --local) PROFILE_MODE="local"; shift ;;
    --prod) PROFILE_MODE="prod"; shift ;;
    --skip-doctor) SKIP_DOCTOR=1; shift ;;
    -h|--help)
      echo "AI-Hub Bootstrap v6.0 (Backend + Frontend)"
      echo "Usage: ./start.sh [options]"
      echo "Options:"
      echo "  --no-install       Skip pip/npm install"
      echo "  --no-frontend      Backend only"
      echo "  --force-kill       Kill foreign process on PORT"
      echo "  --clean            Rotate DB backup + reset run files"
      echo "  --prod-frontend    cockpit: npm run build && next start (stabilniejsze na E2E/CI;"
      echo "  --local            Runtime override: SQLite + local paths, keeps real API/LLM/embedding keys from .env"
      echo "  --prod             Runtime uses .env as production profile (Postgres/real providers if configured)"
      echo "  --skip-doctor      Skip mandatory preflight doctor (emergency only)"
      echo "                     next dev bywa niestabilny — jeden GET / nie gwarantuje trwałości)"
      echo ""
      echo "Optional env (not flags):"
      echo "  START_BACKEND_WAIT_SEC=1800  Max seconds for /system/ping (default 1800; use 120 for sqlite-only dev)"
      echo "  START_RUN_WEB_GROUNDING_SMOKE=1  After health checks run scripts/web_grounding_smoke.sh"
      echo "                                   (FAIL aborts start). Override log dir: WEB_GROUNDING_SMOKE_LOG_DIR"
      echo "  START_RUN_SELFHOSTED_SMOKE=1      Po health: scripts/smoke_selfhosted_stt_vision.sh (Ollama + /chat/stt)"
      echo "  START_RUN_REAL_EMBEDDING_SMOKE=1  Po health: scripts/smoke_embedding_real.py (provider + FAISS)"
      echo "  START_RUN_REAL_CHAT_SMOKE=1       Po health: scripts/smoke_chat_real.py against /chat/turn"
      echo "  START_RUN_REAL_MEMORY_SMOKE=1     Po health: scripts/smoke_memory_real.py against Memory V2/context-pack"
      echo "  AIHUB_HEALTH_LIVE_PROVIDER_PROBE=1 /ops/health probes provider TCP endpoints"
      echo "                                   SELFHOSTED_SMOKE_SKIP_STT=1 — tylko Ollama /api/tags"
      exit 0
      ;;
    *) err "Unknown: $1"; exit 2 ;;
  esac
done

mkdir -p "$LOG_DIR" "$RUN_DIR" "$APP_DIR/data/backup"

# === CLEAN ===
if [[ $DO_CLEAN -eq 1 ]]; then
  info "Cleaning..."
  if [[ -f "$PID_FILE" ]]; then
    pid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
    fi
  fi
  rm -f "$PID_FILE" "$PORT_FILE" "$FRONTEND_PID_FILE" "$FRONTEND_PORT_FILE"
  if [[ -f "$APP_DIR/data/aihub.sqlite3" ]]; then
    ts=$(date +%Y%m%d_%H%M%S)
    cp "$APP_DIR/data/aihub.sqlite3" "$APP_DIR/data/backup/aihub.sqlite3.${ts}"
  fi
  info "Clean done"
fi

# === VENV ===
ensure_venv() {
  if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    info "Creating venv..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  fi
  source "$VENV_DIR/bin/activate" 2>/dev/null || true
}

# === PYTHON DEPS ===
install_python_deps() {
  if [[ $NO_INSTALL -eq 1 ]]; then
    info "Skipping pip install"
    return 0
  fi
  ensure_venv
  info "Installing Python dependencies..."
  python -m pip install -U pip setuptools wheel >> "$UVICORN_LOG" 2>&1 || true
  if [[ -f "$APP_DIR/requirements.txt" ]]; then
    pip install -r "$APP_DIR/requirements.txt" >> "$UVICORN_LOG" 2>&1 || { err "pip install failed"; return 1; }
  fi
  pip install -U "uvicorn[standard]" >> "$UVICORN_LOG" 2>&1 || true
  info "Python deps installed"
}

# === NODE CHECK ===
check_node() {
  if command -v "$NODE_BIN" >/dev/null 2>&1 && command -v "$NPM_BIN" >/dev/null 2>&1; then
    local node_ver
    node_ver=$("$NODE_BIN" --version | cut -d'v' -f2 | cut -d'.' -f1)
    if [[ ${node_ver:-0} -ge 18 ]]; then
      return 0
    fi
  fi
  return 1
}

# === NPM DEPS ===
install_npm_deps() {
  if [[ $NO_INSTALL -eq 1 ]]; then
    info "Skipping npm install"
    return 0
  fi
  if check_node; then
    if [[ -d "$COCKPIT_DIR" ]]; then
      info "Installing Node dependencies..."
      cd "$COCKPIT_DIR"
      if [[ -f package-lock.json ]]; then
        "$NPM_BIN" ci >> "$FRONTEND_LOG" 2>&1 || "$NPM_BIN" install >> "$FRONTEND_LOG" 2>&1 || true
      else
        "$NPM_BIN" install >> "$FRONTEND_LOG" 2>&1 || true
      fi
      cd "$APP_DIR"
      info "Node deps installed"
    fi
  else
    warn "Node unavailable — skipping npm install"
  fi
}

# === GEN SECRET ===
gen_secret() {
  python -c "import secrets; print(secrets.token_hex(${1:-32}))" 2>/dev/null || openssl rand -hex "${1:-32}" 2>/dev/null || echo "secret-$(date +%s)"
}

# === ENV BACKEND ===
ensure_backend_env() {
  if [[ ! -f "$ENV_FILE" ]]; then
    info "Creating .env..."
    cat >> "$ENV_FILE" <<'ENVEND'
ENV=development
HOST=127.0.0.1
PORT=8080
WORKERS=1
DB_BACKEND=sqlite
DATA_DIR=data
DB_PATH=data/aihub.sqlite3
FS_ROOT=data/fs
SNAPSHOT_DIR=data/snapshots
LOG_DIR=logs
LOG_LEVEL=INFO
AGENT_AUTOSTART=1
HTTP_MAX_REDIRECTS=5
# Hub HTTP: start.sh uzupełni API_KEY losowo, jeśli puste (poniżej).
# LLM (np. DeepInfra): uzupełnij raz — tylko w tym pliku na dysku, nie w repo.
LLM_API_KEY=
ENVEND
  fi

  local api_key
  api_key=$(grep "^API_KEY=" "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)
  if [[ -z "$api_key" ]] || [[ "$api_key" == "PLACEHOLDER"* ]]; then
    key=$(gen_secret 32)
    if grep -q "^API_KEY=" "$ENV_FILE"; then
      sed -i "s|^API_KEY=.*|API_KEY=${key}|" "$ENV_FILE"
    else
      echo "API_KEY=${key}" >> "$ENV_FILE"
    fi
  fi

  # Istniejące .env bez linii LLM — jedna pusta linia do uzupełnienia lokalnie.
  if ! grep -q '^LLM_API_KEY=' "$ENV_FILE" 2>/dev/null; then
    {
      echo ""
      echo "# Uzupełnij klucz dostawcy modelu (sk-… / token DeepInfra itd.)"
      echo "LLM_API_KEY="
    } >> "$ENV_FILE"
  fi
}

# === ENV FRONTEND ===
ensure_frontend_env() {
  [[ -d "$COCKPIT_DIR" ]] || return 0
  mkdir -p "$COCKPIT_DIR"
}

# === PYTHON FOR ENV (venv preferred) ===
python_for_env() {
  if [[ -x "$VENV_DIR/bin/python" ]]; then
    echo "$VENV_DIR/bin/python"
  else
    echo "$PYTHON_BIN"
  fi
}

# === LOAD ENV (wszystkie zmienne z morda/.env → export; parser jak dotenv) ===
load_backend_env() {
  [[ -f "$ENV_FILE" ]] || return 0
  local py
  py="$(python_for_env)"
  eval "$("$py" "$APP_DIR/scripts/dotenv_tool.py" exports "$ENV_FILE")"
}

# Katalogi z aihub.config (DATA_DIR, FS_ROOT, SNAPSHOT_DIR, katalog pliku DB) — jeden kanon ze ścieżek w .env
ensure_data_dirs() {
  [[ -f "$ENV_FILE" ]] || return 0
  local py
  py="$(python_for_env)"
  if ! ( cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$py" -c "
from aihub.config import DATA_DIR, FS_ROOT, SNAPSHOT_DIR, DB_PATH
for p in (DATA_DIR, FS_ROOT, SNAPSHOT_DIR, DB_PATH.parent):
    p.mkdir(parents=True, exist_ok=True)
" ); then
    warn "ensure_data_dirs: nie udało się utworzyć katalogów (sprawdź .env / PYTHONPATH)"
  fi
}



# === PROFILE OVERRIDES ===
apply_profile_overrides() {
  case "${PROFILE_MODE}" in
    local)
      info "Profile: local (SQLite/runtime-safe; real API/LLM/embedding keys still read from .env)"
      export ENV="${ENV:-development}"
      export DB_BACKEND="sqlite"
      export DB_PATH="${DB_PATH:-data/aihub.sqlite3}"
      export DATA_DIR="${DATA_DIR:-data}"
      export FS_ROOT="${FS_ROOT:-data/fs}"
      export SNAPSHOT_DIR="${SNAPSHOT_DIR:-data/snapshots}"
      export AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK="0"
      export AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK="0"
      export AIHUB_DISABLE_REMOTE_EMBEDDINGS="${AIHUB_DISABLE_REMOTE_EMBEDDINGS:-0}"
      export START_BACKEND_WAIT_SEC="${START_BACKEND_WAIT_SEC:-180}"
      ;;
    prod)
      info "Profile: prod (.env controls DB/providers; doctor enforces hard failures)"
      export ENV="${ENV:-production}"
      export AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK="${AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK:-0}"
      export AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK="${AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK:-0}"
      ;;
    "")
      ;;
    *)
      err "Unknown profile mode: ${PROFILE_MODE}"
      exit 2
      ;;
  esac
}

# === EMBEDDING/VECTOR WARMUP ===
warm_embedding_runtime() {
  local py
  py="$(python_for_env)"
  info "Warming real embedding/vector runtime (FAISS + semantic embeddings)..."
  local profile_arg=()
  if [[ -n "${PROFILE_MODE}" ]]; then
    profile_arg=(--profile "$PROFILE_MODE")
  fi
  if ! (cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$py" "$APP_DIR/scripts/warm_embedding_models.py" --repo "$APP_DIR" --env "$ENV_FILE" "${profile_arg[@]}"); then
    err "Embedding/vector warmup failed. FAISS + real semantic embeddings are required; no fake vector fallback."
    exit 1
  fi
}

# === PREFLIGHT DOCTOR ===
run_preflight_doctor() {
  if [[ $SKIP_DOCTOR -eq 1 ]]; then
    warn "Skipping doctor/preflight (--skip-doctor)"
    return 0
  fi
  local py
  py="$(python_for_env)"
  info "Doctor/preflight: env + deps + DB + imports + routes + cockpit env..."
  local base_url="http://${HOST:-127.0.0.1}:${PORT:-8080}"
  local profile_arg=()
  if [[ -n "${PROFILE_MODE}" ]]; then
    profile_arg=(--profile "$PROFILE_MODE")
  fi
  if ! (cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$py" "$APP_DIR/scripts/doctor.py" \
      --repo "$APP_DIR" \
      --env "$ENV_FILE" \
      "${profile_arg[@]}" \
      --check-db \
      --check-imports \
      --check-routes \
      --sync-cockpit-env \
      --backend-base-url "$base_url"); then
    err "Doctor/preflight failed. Napraw .env/deps/DB zanim startujesz backend."
    exit 1
  fi
  info "Doctor/preflight: OK"
}

# === PORT HELPERS ===
# Poprawny exit code: END{exit 1} po NR==1{exit 0} nadpisywał sukces — zawsze zwracało 1,
# więc port wyglądał na „wolny” w pick_port, a bind-wait nigdy nie widział listenera.
is_listening() {
  ss -ltnH "( sport = :$1 )" 2>/dev/null | awk 'END { exit(NR > 0 ? 0 : 1) }'
}

get_pid_on_port() {
  local port="$1"
  ss -ltnp "( sport = :$port )" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | head -1
}

is_our_process() {
  local pid="$1"
  [[ -z "$pid" ]] && return 1
  kill -0 "$pid" 2>/dev/null || return 1
  local cwd
  cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
  if [[ "$cwd" == "$APP_DIR"* ]]; then
    return 0
  fi
  local cmdline
  cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  if [[ "$cmdline" == *"$APP_DIR"* ]] || [[ "$cmdline" == *"aihub.main"* ]]; then
    return 0
  fi
  return 1
}

pick_port() {
  local base="$1" max="$2"
  for ((p=base; p<=max; p++)); do
    if ! is_listening "$p"; then
      echo "$p"
      return 0
    fi
  done
  return 1
}

# === STOP STALE PROCESSES ===
stop_stale() {
  if [[ -f "$PID_FILE" ]]; then
    local oldpid
    oldpid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [[ -n "$oldpid" ]] && kill -0 "$oldpid" 2>/dev/null; then
      info "Stopping stale backend (pid=$oldpid)..."
      kill "$oldpid" 2>/dev/null || true
      for i in {1..20}; do
        kill -0 "$oldpid" 2>/dev/null || break
        sleep 0.25
      done
      kill -9 "$oldpid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE" "$PORT_FILE"
  fi

  if [[ -f "$FRONTEND_PID_FILE" ]]; then
    local oldfpid
    oldfpid=$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)
    if [[ -n "$oldfpid" ]] && kill -0 "$oldfpid" 2>/dev/null; then
      info "Stopping stale frontend (pid=$oldfpid)..."
      kill "$oldfpid" 2>/dev/null || true
      for i in {1..10}; do
        kill -0 "$oldfpid" 2>/dev/null || break
        sleep 0.25
      done
      kill -9 "$oldfpid" 2>/dev/null || true
    fi
    rm -f "$FRONTEND_PID_FILE" "$FRONTEND_PORT_FILE"
  fi
}

# === START BACKEND ===
start_backend() {
  ensure_venv

  local port="${PORT:-8080}"
  local port_max=$((port + 10))

  # Check if our backend already runs on correct port with correct PID
  if [[ -f "$PID_FILE" ]]; then
    local oldpid
    oldpid=$(cat "$PID_FILE" 2>/dev/null || true)
    if [[ -n "$oldpid" ]] && kill -0 "$oldpid" 2>/dev/null; then
      local real_pid
      real_pid=$(get_pid_on_port "$port")
      if [[ "$real_pid" == "$oldpid" ]] && is_our_process "$oldpid"; then
        info "Backend already running (pid=$oldpid port=$port) — verified"
        echo "$port" > "$PORT_FILE"
        return 0
      else
        warn "Stale PID file (pid=$oldpid not on port $port) — cleaning"
        kill "$oldpid" 2>/dev/null || true
        sleep 0.5
      fi
    fi
    rm -f "$PID_FILE" "$PORT_FILE"
  fi

  # Check if port is occupied by foreign process
  if is_listening "$port"; then
    local foreign_pid
    foreign_pid=$(get_pid_on_port "$port")
    if [[ -n "$foreign_pid" ]] && ! is_our_process "$foreign_pid"; then
      if [[ $FORCE_KILL -eq 1 ]]; then
        warn "Port $port occupied by foreign pid=$foreign_pid — killing (--force-kill)"
        kill -9 "$foreign_pid" 2>/dev/null || true
        sleep 0.5
      else
        err "Port $port occupied by foreign process (pid=$foreign_pid)"
        err "Use --force-kill to take over, or stop the other process"
        exit 1
      fi
    elif [[ -n "$foreign_pid" ]] && is_our_process "$foreign_pid"; then
      warn "Found orphaned backend (pid=$foreign_pid) on port $port — adopting"
      echo "$foreign_pid" > "$PID_FILE"
      echo "$port" > "$PORT_FILE"
      return 0
    fi
  fi

  # Find free port starting from .env PORT
  local actual_port
  actual_port=$(pick_port "$port" "$port_max" 2>/dev/null || true)
  if [[ -z "$actual_port" ]]; then
    err "No free port in range $port-$port_max"
    exit 1
  fi
  if [[ "$actual_port" != "$port" ]]; then
    warn "Configured PORT=$port busy, using $actual_port"
  fi

  # Rotate backend logs on fresh start
  if [[ -f "$UVICORN_LOG" ]] && [[ -s "$UVICORN_LOG" ]]; then
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    mv "$UVICORN_LOG" "$LOG_DIR/aihub.log.${ts}"
  fi
  if [[ -f "$ERROR_LOG" ]] && [[ -s "$ERROR_LOG" ]]; then
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    mv "$ERROR_LOG" "$LOG_DIR/aihub.error.log.${ts}"
  fi
  : > "$UVICORN_LOG"
  : > "$ERROR_LOG"

  echo "$actual_port" > "$PORT_FILE"
  export PORT="$actual_port"
  export HOST="${HOST:-127.0.0.1}"
  info "Starting backend @ ${HOST}:${actual_port}"

  nohup env HOST="$HOST" PORT="$actual_port" "$VENV_DIR/bin/python" -m uvicorn "$APP_IMPORT" \
    --host "$HOST" --port "$actual_port" \
    --workers "$WORKERS" \
    --log-level info \
    >> "$UVICORN_LOG" 2>> "$ERROR_LOG" &

  local launch_pid=$!
  echo "$launch_pid" > "$PID_FILE"

  # Pierwszy klucz hubu (aliasy z config/hub_key_env_names.json)
  local api_key=""
  local py
  py="$(python_for_env)"
  api_key=$("$py" "$APP_DIR/scripts/dotenv_tool.py" hub-x-key "$APP_DIR" "$ENV_FILE" || true)

  # Wait for TCP listener + HTTP /system/ping (PG bootstrap + sqlite_pg_import może trwać minuty)
  local bind_sleep=0.5
  local bind_max=$(( START_BACKEND_WAIT_SEC * 2 ))
  info "Waiting for backend (port $actual_port, health /system/ping, up to ${START_BACKEND_WAIT_SEC}s)..."
  local verified=0
  local i
  for ((i = 1; i <= bind_max; i++)); do
    if ! kill -0 "$launch_pid" 2>/dev/null; then
      err "Backend process died during startup (pid=$launch_pid)"
      rm -f "$PID_FILE" "$PORT_FILE"
      tail -40 "$ERROR_LOG" 2>/dev/null || true
      exit 1
    fi

    if is_listening "$actual_port"; then
      local real_pid
      real_pid=$(get_pid_on_port "$actual_port")
      if [[ -n "$real_pid" ]]; then
        if [[ "$real_pid" != "$launch_pid" ]]; then
          dbg "Launch pid=$launch_pid, listener pid=$real_pid — updating PID file"
        fi
        echo "$real_pid" > "$PID_FILE"

        local health_url="http://${HOST}:${actual_port}/system/ping"
        local http_code="000"
        if [[ -n "$api_key" ]]; then
          http_code=$(curl -sS -o /dev/null -w '%{http_code}' \
            --connect-timeout 3 \
            --max-time 15 \
            -H "x-api-key: ${api_key}" \
            "$health_url" 2>/dev/null) || http_code="000"
        else
          http_code=$(curl -sS -o /dev/null -w '%{http_code}' \
            --connect-timeout 3 \
            --max-time 15 \
            "$health_url" 2>/dev/null) || http_code="000"
        fi

        if [[ "$http_code" == "200" ]] || [[ "$http_code" == "401" ]]; then
          verified=1
          dbg "Health check passed: /system/ping HTTP $http_code (attempt $i/$bind_max)"
          break
        fi
        if ((i % 20 == 0)); then
          dbg "Still waiting: listener pid=$real_pid HTTP=$http_code (attempt $i/$bind_max)"
        fi
      fi
    fi
    if ((i % 120 == 0)); then
      warn "Startup w toku ($i/$bind_max, max ${START_BACKEND_WAIT_SEC}s) — bez listenera = trwa lifespan/import SQLite→PG; log: $UVICORN_LOG"
    fi
    sleep "$bind_sleep"
  done

  if [[ $verified -eq 0 ]]; then
    err "Backend failed to become healthy on port $actual_port within ${START_BACKEND_WAIT_SEC}s (see $ERROR_LOG; zwiększ START_BACKEND_WAIT_SEC lub poczekaj na koniec importu PG)"
    kill "$launch_pid" 2>/dev/null || true
    rm -f "$PID_FILE" "$PORT_FILE"
    tail -40 "$ERROR_LOG" 2>/dev/null || true
    exit 1
  fi

  info "Backend started and verified (pid=$(cat "$PID_FILE") port=$actual_port)"
}

# === SYNC COCKPIT/.ENV Z morda/.env (zawsze po znanym porcie backendu) ===
sync_cockpit_env() {
  [[ -d "$COCKPIT_DIR" ]] || return 0
  local backend_port
  backend_port=$(cat "$PORT_FILE" 2>/dev/null || echo "${PORT:-8080}")
  local py
  py="$(python_for_env)"
  "$py" "$APP_DIR/scripts/dotenv_tool.py" write-cockpit "$APP_DIR" "$ENV_FILE" "$COCKPIT_ENV" "http://${HOST}:${backend_port}"
  info "Cockpit .env ← $ENV_FILE (AIHUB_BASE_URL=http://${HOST}:${backend_port})"
}

# === START FRONTEND ===
start_frontend() {
  if [[ $NO_FRONTEND -eq 1 ]] || [[ ! -d "$COCKPIT_DIR" ]]; then
    return 0
  fi
  if ! check_node; then
    warn "Node unavailable — skipping frontend"
    return 0
  fi

  local fport="${FRONTEND_PORT:-3000}"
  local fport_max=$((fport + 10))

  # Check existing frontend
  if [[ -f "$FRONTEND_PID_FILE" ]]; then
    local oldfpid
    oldfpid=$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)
    if [[ -n "$oldfpid" ]] && kill -0 "$oldfpid" 2>/dev/null; then
      info "Frontend already running (pid=$oldfpid)"
      return 0
    fi
    rm -f "$FRONTEND_PID_FILE" "$FRONTEND_PORT_FILE"
  fi

  local actual_fport
  actual_fport=$(pick_port "$fport" "$fport_max" 2>/dev/null || true)
  if [[ -z "$actual_fport" ]]; then
    warn "No free port for frontend in range $fport-$fport_max"
    return 1
  fi

  # Rotate frontend log
  if [[ -f "$FRONTEND_LOG" ]] && [[ -s "$FRONTEND_LOG" ]]; then
    local ts; ts=$(date +%Y%m%d_%H%M%S)
    mv "$FRONTEND_LOG" "$LOG_DIR/frontend.log.${ts}"
  fi
  : > "$FRONTEND_LOG"

  # cockpit/.env ustawiane w sync_cockpit_env() (wywołanie w main przed start_frontend).

  echo "$actual_fport" > "$FRONTEND_PORT_FILE"

  cd "$COCKPIT_DIR"
  if [[ $PROD_FRONTEND -eq 1 ]]; then
    info "Frontend mode: production (next start) — building cockpit..."
    if ! "$NPM_BIN" run build >> "$FRONTEND_LOG" 2>&1; then
      err "cockpit npm run build failed — see $FRONTEND_LOG"
      cd "$APP_DIR"
      rm -f "$FRONTEND_PID_FILE" "$FRONTEND_PORT_FILE"
      return 1
    fi
    info "Starting frontend @ localhost:${actual_fport} (next start)"
    nohup env PORT="$actual_fport" "$NPM_BIN" run start >> "$FRONTEND_LOG" 2>&1 &
  else
    info "Starting frontend @ localhost:${actual_fport} (next dev; use --prod-frontend for next start)"
    nohup env PORT="$actual_fport" "$NPM_BIN" run dev >> "$FRONTEND_LOG" 2>&1 &
  fi
  local npm_pid=$!
  cd "$APP_DIR"

  # Wait for port binding
  info "Waiting for frontend to bind port $actual_fport..."
  local fverified=0
  for i in {1..40}; do
    if is_listening "$actual_fport"; then
      local real_fpid
      real_fpid=$(get_pid_on_port "$actual_fport")
      if [[ -n "$real_fpid" ]]; then
        echo "$real_fpid" > "$FRONTEND_PID_FILE"
        fverified=1
        break
      fi
    fi
    # npm may have exited but node child continues
    if ! kill -0 "$npm_pid" 2>/dev/null; then
      sleep 1
      if is_listening "$actual_fport"; then
        local real_fpid
        real_fpid=$(get_pid_on_port "$actual_fport")
        if [[ -n "$real_fpid" ]]; then
          echo "$real_fpid" > "$FRONTEND_PID_FILE"
          fverified=1
          break
        fi
      fi
      break
    fi
    sleep 0.5
  done

  if [[ $fverified -eq 1 ]]; then
    info "Frontend started and verified (pid=$(cat "$FRONTEND_PID_FILE") port=$actual_fport)"
  else
    err "Frontend failed to bind port $actual_fport within 20s"
    kill "$npm_pid" 2>/dev/null || true
    rm -f "$FRONTEND_PID_FILE" "$FRONTEND_PORT_FILE"
    tail -20 "$FRONTEND_LOG" 2>/dev/null || true
    return 1
  fi
}

# === HEALTH CHECK BACKEND ===
wait_backend_health() {
  local port
  port=$(cat "$PORT_FILE" 2>/dev/null || echo "8080")
  local api_key
  local py
  py="$(python_for_env)"
  api_key=$("$py" "$APP_DIR/scripts/dotenv_tool.py" hub-x-key "$APP_DIR" "$ENV_FILE" || true)

  info "Backend health @ ${HOST}:${port}/system/ping (do ${START_BACKEND_WAIT_SEC}s) ..."
  local hi hi_max
  hi_max=$(( START_BACKEND_WAIT_SEC * 2 ))
  for ((hi = 1; hi <= hi_max; hi++)); do
    local ok=0
    if [[ -n "$api_key" ]]; then
      curl -fsS -H "x-api-key: $api_key" "http://${HOST}:${port}/system/ping" -o /dev/null 2>/dev/null && ok=1
    else
      curl -fsS "http://${HOST}:${port}/system/ping" -o /dev/null 2>/dev/null && ok=1
    fi
    if [[ "$ok" -eq 1 ]]; then
      # Final PID cross-check
      local real_pid file_pid
      real_pid=$(get_pid_on_port "$port")
      file_pid=$(cat "$PID_FILE" 2>/dev/null || true)
      if [[ -n "$real_pid" ]] && [[ "$real_pid" != "$file_pid" ]]; then
        dbg "PID drift detected: file=$file_pid actual=$real_pid — updating"
        echo "$real_pid" > "$PID_FILE"
      fi
      info "Backend healthy (pid=$(cat "$PID_FILE") port=$port)"
      return 0
    fi
    sleep 0.5
  done

  err "Backend health TIMEOUT (${START_BACKEND_WAIT_SEC}s)"
  tail -20 "$ERROR_LOG" 2>/dev/null || true
  return 1
}

# === HEALTH CHECK FRONTEND ===
# Jeden GET na / bywa fałszywie „zdrowy”: next dev potrafi odpowiedzieć i zaraz paść.
# Wymagamy: /user (strona czatu), potem krótka pauza i powtórka + listener + żywy PID z portu.
wait_frontend_health() {
  if [[ $NO_FRONTEND -eq 1 ]] || [[ ! -d "$COCKPIT_DIR" ]]; then
    return 0
  fi
  local fport
  fport=$(cat "$FRONTEND_PORT_FILE" 2>/dev/null || true)
  [[ -z "$fport" ]] && return 0

  local user_url="http://127.0.0.1:${fport}/user"
  info "Frontend health: ${user_url} (+ 4s stability re-check) ..."

  local code=""
  local i
  for i in {1..60}; do
    code=$(curl -sS -L -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 15 \
      "$user_url" 2>/dev/null) || code="000"
    if [[ "$code" == "200" ]]; then
      break
    fi
    sleep 0.5
  done
  if [[ "$code" != "200" ]]; then
    err "Frontend: /user nie zwróciło HTTP 200 (ostatni kod=${code:-?}, limit ~30s)"
    tail -25 "$FRONTEND_LOG" 2>/dev/null || true
    return 1
  fi

  info "Frontend: pierwszy GET /user → 200; czekam 4s na stabilność procesu/portu..."
  sleep 4

  if ! is_listening "$fport"; then
    err "Frontend: po 4s brak listenera na porcie $fport"
    tail -25 "$FRONTEND_LOG" 2>/dev/null || true
    return 1
  fi

  local real_fpid
  real_fpid=$(get_pid_on_port "$fport")
  if [[ -z "$real_fpid" ]]; then
    err "Frontend: ss nie zwraca pid dla :$fport po stabilności"
    return 1
  fi
  echo "$real_fpid" > "$FRONTEND_PID_FILE"
  if ! kill -0 "$real_fpid" 2>/dev/null; then
    err "Frontend: pid=$real_fpid z portu nie żyje (kill -0 failed)"
    return 1
  fi

  code=$(curl -sS -L -o /dev/null -w '%{http_code}' --connect-timeout 2 --max-time 15 \
    "$user_url" 2>/dev/null) || code="000"
  if [[ "$code" != "200" ]]; then
    err "Frontend: drugi GET /user nieudany (HTTP ${code:-?}) — proces prawdopodobnie padł"
    tail -25 "$FRONTEND_LOG" 2>/dev/null || true
    return 1
  fi

  info "Frontend healthy (pid=$real_fpid port=$fport; /user×2 + listener + żywy PID)"
  return 0
}

# === FINAL VERIFICATION ===
verify_pid_consistency() {
  info "Verifying PID consistency..."

  local bpid bport
  bpid=$(cat "$PID_FILE" 2>/dev/null || true)
  bport=$(cat "$PORT_FILE" 2>/dev/null || true)
  if [[ -n "$bpid" ]] && [[ -n "$bport" ]]; then
    local real_bpid
    real_bpid=$(get_pid_on_port "$bport")
    if [[ -z "$real_bpid" ]]; then
      err "VERIFY FAIL: PID=$bpid but nothing listening on port $bport"
      return 1
    elif [[ "$real_bpid" != "$bpid" ]]; then
      warn "VERIFY: PID file=$bpid vs actual=$real_bpid — fixing"
      echo "$real_bpid" > "$PID_FILE"
    else
      info "Backend PID OK: pid=$bpid port=$bport"
    fi
  fi

  local fpid fport
  fpid=$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)
  fport=$(cat "$FRONTEND_PORT_FILE" 2>/dev/null || true)
  if [[ -n "$fpid" ]] && [[ -n "$fport" ]]; then
    local real_fpid
    real_fpid=$(get_pid_on_port "$fport")
    if [[ -z "$real_fpid" ]]; then
      err "VERIFY FAIL: Frontend PID=$fpid but nothing on port $fport"
      return 1
    elif [[ "$real_fpid" != "$fpid" ]]; then
      warn "VERIFY: Frontend PID file=$fpid vs actual=$real_fpid — fixing"
      echo "$real_fpid" > "$FRONTEND_PID_FILE"
      fpid="$real_fpid"
    else
      info "Frontend PID OK: pid=$fpid port=$fport"
    fi
    if ! kill -0 "$fpid" 2>/dev/null; then
      err "VERIFY FAIL: Frontend pid=$fpid nie żyje"
      return 1
    fi
    if ! curl -fsS -L -o /dev/null --connect-timeout 2 --max-time 15 \
      "http://127.0.0.1:${fport}/user" 2>/dev/null; then
      err "VERIFY FAIL: GET /user nie HTTP 2xx po starcie"
      return 1
    fi
    info "Frontend verify: /user OK (końcowa kontrola)"
  fi
}

# === MAIN ===
{
  info "=============================================="
  info "AI-Hub Bootstrap v6.0"
  info "=============================================="

  install_python_deps
  if [[ $NO_FRONTEND -eq 0 ]]; then
    install_npm_deps
  fi

  ensure_venv
  ensure_backend_env
  ensure_frontend_env
  load_backend_env
  apply_profile_overrides
  : "${START_BACKEND_WAIT_SEC:=1800}"
  export START_BACKEND_WAIT_SEC
  ensure_data_dirs
  warm_embedding_runtime
  run_preflight_doctor

  info "Stopping stale processes..."
  stop_stale

  info "Starting services..."
  start_backend
  sync_cockpit_env
  fe_start_rc=0
  start_frontend || fe_start_rc=$?
  if [[ $fe_start_rc -ne 0 ]]; then
    warn "Frontend start failed — backend-only mode"
  fi

  info "Health checks..."
  wait_backend_health || exit 1
  if [[ $fe_start_rc -eq 0 ]]; then
    wait_frontend_health || exit 1
  fi

  verify_pid_consistency || exit 1

  if [[ "${START_RUN_WEB_GROUNDING_SMOKE:-0}" == "1" ]]; then
    info "Web grounding smoke (START_RUN_WEB_GROUNDING_SMOKE=1)..."
    if bash "$APP_DIR/scripts/web_grounding_smoke.sh"; then
      info "Web grounding smoke: PASS"
    else
      err "Web grounding smoke: FAIL — see log path printed above"
      exit 1
    fi
  fi


  if [[ "${START_RUN_REAL_EMBEDDING_SMOKE:-0}" == "1" ]]; then
    info "Real embedding/vector smoke (START_RUN_REAL_EMBEDDING_SMOKE=1)..."
    profile_arg=()
    if [[ -n "${PROFILE_MODE}" ]]; then
      profile_arg=(--profile "$PROFILE_MODE")
    fi
    if (cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$(python_for_env)" "$APP_DIR/scripts/smoke_embedding_real.py" --repo "$APP_DIR" --env "$ENV_FILE" "${profile_arg[@]}"); then
      info "Real embedding/vector smoke: PASS"
    else
      err "Real embedding/vector smoke: FAIL — embedding provider/FAISS vector stack nie działa"
      exit 1
    fi
  fi

  if [[ "${START_RUN_REAL_CHAT_SMOKE:-0}" == "1" ]]; then
    info "Real chat smoke (START_RUN_REAL_CHAT_SMOKE=1)..."
    backend_port=$(cat "$PORT_FILE" 2>/dev/null || echo "${PORT:-8080}")
    if (cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$(python_for_env)" "$APP_DIR/scripts/smoke_chat_real.py" --repo "$APP_DIR" --env "$ENV_FILE" --base-url "http://${HOST}:${backend_port}"); then
      info "Real chat smoke: PASS"
    else
      err "Real chat smoke: FAIL — /chat/turn, LLM provider albo memory writeback nie działa"
      exit 1
    fi
  fi

  if [[ "${START_RUN_REAL_MEMORY_SMOKE:-0}" == "1" ]]; then
    info "Real memory smoke (START_RUN_REAL_MEMORY_SMOKE=1)..."
    backend_port=$(cat "$PORT_FILE" 2>/dev/null || echo "${PORT:-8080}")
    if (cd "$APP_DIR" && PYTHONPATH="$APP_DIR" "$(python_for_env)" "$APP_DIR/scripts/smoke_memory_real.py" --repo "$APP_DIR" --env "$ENV_FILE" --base-url "http://${HOST}:${backend_port}"); then
      info "Real memory smoke: PASS"
    else
      err "Real memory smoke: FAIL — Memory V2/context-pack/index jobs nie działa"
      exit 1
    fi
  fi

  if [[ "${START_RUN_SELFHOSTED_SMOKE:-0}" == "1" ]]; then
    info "Self-hosted STT/Vision smoke (START_RUN_SELFHOSTED_SMOKE=1)..."
    if bash "$APP_DIR/scripts/smoke_selfhosted_stt_vision.sh"; then
      info "Self-hosted smoke: PASS"
    else
      err "Self-hosted smoke: FAIL — sprawdź Ollama, CHAT_STT_ENABLED, ffmpeg, API_KEY"
      exit 1
    fi
  fi

  backend_port=$(cat "$PORT_FILE" 2>/dev/null || echo "?")
  frontend_port=$(cat "$FRONTEND_PORT_FILE" 2>/dev/null || echo "disabled")
  backend_pid=$(cat "$PID_FILE" 2>/dev/null || echo "?")
  frontend_pid=$(cat "$FRONTEND_PID_FILE" 2>/dev/null || echo "none")

  info "=============================================="
  info "AI-Hub Running"
  info "=============================================="
  info "Backend:      http://${HOST}:${backend_port}/docs  (pid=$backend_pid)"
  if [[ $NO_FRONTEND -eq 1 ]] || [[ "$frontend_port" == "disabled" ]]; then
    info "Frontend:     (disabled — --no-frontend or not started)"
  elif [[ $PROD_FRONTEND -eq 1 ]]; then
    info "Frontend:     http://localhost:${frontend_port}  (pid=$frontend_pid) [next start]"
  else
    info "Frontend:     http://localhost:${frontend_port}  (pid=$frontend_pid) [next dev]"
  fi
  info "Logs:         $LOG_DIR/"
  info "PID files:    $RUN_DIR/"
  info "=============================================="
}
