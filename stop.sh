#!/usr/bin/env bash
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${RUN_DIR:-$APP_DIR/data/run}"
LOG_DIR="${LOG_DIR:-$APP_DIR/logs}"
PID_FILE="$RUN_DIR/aihub.pid"
PORT_FILE="$RUN_DIR/aihub.port"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
FRONTEND_PORT_FILE="$RUN_DIR/frontend.port"

G='\033[0;32m'
Y='\033[0;33m'
R='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${G}[INFO]${NC} $*"; }
warn()  { echo -e "${Y}[WARN]${NC} $*"; }
err()   { echo -e "${R}[ERR]${NC}  $*" >&2; }

mkdir -p "$RUN_DIR" "$LOG_DIR"

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

graceful_kill() {
  local pid="$1" label="$2"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  info "Stopping $label (pid=$pid)..."
  kill "$pid" 2>/dev/null || true
  for i in {1..40}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      info "$label stopped (graceful)"
      return 0
    fi
    sleep 0.25
  done
  warn "$label did not stop gracefully — sending SIGKILL"
  kill -9 "$pid" 2>/dev/null || true
  sleep 0.5
  if kill -0 "$pid" 2>/dev/null; then
    err "$label pid=$pid still alive after SIGKILL"
    return 1
  fi
  info "$label stopped (forced)"
}

stop_backend() {
  local pid port

  # Read PID from file
  pid=$(cat "$PID_FILE" 2>/dev/null || true)
  port=$(cat "$PORT_FILE" 2>/dev/null || true)

  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    if is_our_process "$pid"; then
      graceful_kill "$pid" "backend"
    else
      warn "PID $pid in file is NOT our process — skipping kill"
    fi
  elif [[ -n "$pid" ]]; then
    info "Backend pid=$pid already dead"
  fi

  # Cross-check: if something still listens on our port, verify ownership
  if [[ -n "$port" ]]; then
    local real_pid
    real_pid=$(get_pid_on_port "$port")
    if [[ -n "$real_pid" ]] && is_our_process "$real_pid"; then
      warn "Found lingering backend on port $port (pid=$real_pid) — stopping"
      graceful_kill "$real_pid" "backend-lingering"
    elif [[ -n "$real_pid" ]]; then
      warn "Port $port still in use by foreign pid=$real_pid — NOT killing"
    fi
  fi

  rm -f "$PID_FILE" "$PORT_FILE"
}

stop_frontend() {
  local fpid fport

  # Primary: use PID file
  fpid=$(cat "$FRONTEND_PID_FILE" 2>/dev/null || true)
  fport=$(cat "$FRONTEND_PORT_FILE" 2>/dev/null || true)

  if [[ -n "$fpid" ]] && kill -0 "$fpid" 2>/dev/null; then
    if is_our_process "$fpid"; then
      graceful_kill "$fpid" "frontend"
    else
      warn "Frontend PID $fpid is NOT our process — skipping kill"
    fi
  elif [[ -n "$fpid" ]]; then
    info "Frontend pid=$fpid already dead"
  fi

  # Cross-check port
  if [[ -n "$fport" ]]; then
    local real_fpid
    real_fpid=$(get_pid_on_port "$fport")
    if [[ -n "$real_fpid" ]] && is_our_process "$real_fpid"; then
      warn "Found lingering frontend on port $fport (pid=$real_fpid) — stopping"
      graceful_kill "$real_fpid" "frontend-lingering"
    elif [[ -n "$real_fpid" ]]; then
      warn "Port $fport still in use by foreign pid=$real_fpid — NOT killing"
    fi
  fi

  rm -f "$FRONTEND_PID_FILE" "$FRONTEND_PORT_FILE"
}

stop_systemd() {
  if systemctl is-active --quiet aihub 2>/dev/null; then
    info "Stopping aihub.service..."
    systemctl stop aihub 2>/dev/null || true
  fi
}

{
  info "======================================"
  info "AI-Hub STOP v6.0"
  info "======================================"
  stop_systemd || true
  stop_backend || true
  stop_frontend || true
  info "======================================"
  info "AI-Hub stopped"
  info "======================================"
}
