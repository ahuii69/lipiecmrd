#!/usr/bin/env bash
set -Eeuo pipefail

# Repo root (nie zakładaj /root/morda — działa z dowolnej lokalizacji klonu).
SMOKE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export SMOKE_REPO_ROOT="$SMOKE_ROOT"

ENV_FILE="${SMOKE_ROOT}/.env"
VENV_DIR="${SMOKE_ROOT}/.venv"
BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
export BASE_URL
USER_ID="${USER_ID:-smoke_user}"
SESSION_ID="${SESSION_ID:-smoke_session}"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-30}"
PYTHON_BIN="${PYTHON_BIN:-python}"
# full | chat-only  — chat-only: tylko dowód LLM (/chat/turn), bez memory/agent tick
SMOKE_MODE="${SMOKE_MODE:-full}"

log() {
  printf '[smoke] %s\n' "$*"
}

fail() {
  printf '[smoke][FAIL] %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path="$1"
  [[ -f "$path" ]] || fail "Brak pliku: $path"
}

require_dir() {
  local path="$1"
  [[ -d "$path" ]] || fail "Brak katalogu: $path"
}

cd "$SMOKE_ROOT" || fail "Nie mogę wejść do $SMOKE_ROOT"
require_file "$ENV_FILE"
require_dir "$VENV_DIR"

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

log "Start smoke runtime (mode=$SMOKE_MODE) dla $BASE_URL"

export SMOKE_MODE
SMOKE_MODE="$SMOKE_MODE" USER_ID="$USER_ID" SESSION_ID="$SESSION_ID" \
  TIMEOUT_SECONDS="$TIMEOUT_SECONDS" "$PYTHON_BIN" - <<'PY'
import json
import os
import socket
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(os.environ.get("SMOKE_REPO_ROOT", ".")).resolve()
ENV_FILE = ROOT / ".env"
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8080").rstrip("/")
USER_ID = os.getenv("USER_ID", "smoke_user")
SESSION_ID = os.getenv("SESSION_ID", "smoke_session")
TIMEOUT_SECONDS = int(os.getenv("TIMEOUT_SECONDS", "30"))
SMOKE_MODE = os.getenv("SMOKE_MODE", "full").strip().lower()
CHAT_ONLY = SMOKE_MODE in ("chat-only", "chat_only", "quick")

load_dotenv(ENV_FILE)

from aihub.agent_http_surface import agent_tick_http_enabled
from aihub.auth_patch import coalesce_hub_key

api_key = coalesce_hub_key().strip()
if not api_key:
    raise SystemExit(
        "[smoke][FAIL] Brak hub key w .env "
        "(AIHUB_API_KEY / HUB_API_KEY / API_KEY / AIHUB_PROXY_TOKEN)"
    )

headers_json = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json",
}
headers_auth = {
    "Authorization": f"Bearer {api_key}",
}

skips: list[str] = []


def log(msg: str) -> None:
    print(f"[smoke] {msg}")


def fail(msg: str) -> None:
    raise SystemExit(f"[smoke][FAIL] {msg}")


def expect(condition: bool, msg: str) -> None:
    if not condition:
        fail(msg)


def pretty_snippet(text: str, limit: int = 700) -> str:
    text = text.replace("\n", "\\n")
    return text[:limit]


def legacy_memory_v1_http_disabled() -> bool:
    """Zgodnie z aihub.main._legacy_memory_v1_http_disabled."""
    v = os.environ.get("AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP", "").strip().lower()
    return v in ("1", "true", "yes", "on")


def parse_base(base_url: str) -> tuple[str, int]:
    if not base_url.startswith("http://"):
        fail(f"Na ten smoke wspieram tylko http://, dostałem: {base_url}")
    without_scheme = base_url[len("http://") :]
    host_port = without_scheme.split("/", 1)[0]
    if ":" in host_port:
        host, port_s = host_port.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError as exc:
            fail(f"Nieprawidłowy port w BASE_URL={base_url}: {exc}")
    else:
        host, port = host_port, 80
    return host, port


def port_check(host: str, port: int) -> None:
    log(f"Port check {host}:{port}")
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect((host, port))
    except Exception as exc:
        fail(f"Port {host}:{port} nie odpowiada: {type(exc).__name__}: {exc}")
    finally:
        s.close()
    log("Port OK")


def request(
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    json_payload=None,
    expected_status: int = 200,
):
    url = f"{BASE_URL}{path}"
    resp = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=json_payload,
        timeout=TIMEOUT_SECONDS,
    )
    log(f"{method} {path} -> {resp.status_code}")
    if resp.status_code != expected_status:
        fail(
            f"{method} {path} zwrócił {resp.status_code}, oczekiwano {expected_status}. "
            f"Body={pretty_snippet(resp.text, 1400)}"
        )
    return resp


def request_any(method: str, path: str, *, headers: dict[str, str], json_payload=None):
    """Bez fail na status — do warunkowych endpointów."""
    url = f"{BASE_URL}{path}"
    resp = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=json_payload,
        timeout=TIMEOUT_SECONDS,
    )
    log(f"{method} {path} -> {resp.status_code}")
    return resp


def get_json(resp, label: str):
    try:
        return resp.json()
    except Exception as exc:
        fail(f"{label}: odpowiedź nie jest poprawnym JSON: {exc}. Body={pretty_snippet(resp.text)}")


host, port = parse_base(BASE_URL)
port_check(host, port)

# --- OBOWIĄZKOWE (dowód żywego API + LLM) ---
log("[mandatory] /system/ping")
resp = request("GET", "/system/ping", headers=headers_auth, expected_status=200)
data = get_json(resp, "system/ping")
expect(data.get("ok") is True, f"/system/ping ma zły payload: {data}")
log(f"/system/ping OK app={data.get('app')}")

if not CHAT_ONLY:
    log("[mandatory] /cockpit/health")
    resp = request("GET", "/cockpit/health", headers=headers_auth, expected_status=200)
    data = get_json(resp, "cockpit/health")
    expect(data.get("ok") is True, f"/cockpit/health ma zły payload: {data}")
    log("/cockpit/health OK")

    log("[mandatory] /cockpit/schema-health")
    resp = request("GET", "/cockpit/schema-health", headers=headers_auth, expected_status=200)
    data = get_json(resp, "cockpit/schema-health")
    expect(data.get("ok") is True, f"/cockpit/schema-health ma zły payload: {data}")
    log("/cockpit/schema-health OK")

    log("[mandatory] /openapi.json")
    resp = request("GET", "/openapi.json", headers=headers_auth, expected_status=200)
    data = get_json(resp, "openapi.json")
    expect(data.get("info", {}).get("title") == "AIHub", f"openapi title != AIHub: {data.get('info')}")
    paths = data.get("paths", {})
    expect("/chat/turn" in paths, "OpenAPI nie zawiera /chat/turn")
    expect(
        "/memory/search" in paths or "/memory/v2/search" in paths,
        "OpenAPI nie zawiera /memory/search ani /memory/v2/search",
    )
    log(f"/openapi.json OK paths={len(paths)}")

    log("[mandatory] /chat/capabilities")
    resp = request("GET", "/chat/capabilities", headers=headers_auth, expected_status=200)
    data = get_json(resp, "chat/capabilities")
    expect(data.get("ok") is True, f"/chat/capabilities ma zły payload: {data}")
    expect(int(data.get("count", 0)) > 0, f"/chat/capabilities count <= 0: {data}")
    log(f"/chat/capabilities OK count={data.get('count')}")

log("[mandatory] POST /chat/turn (prawdziwy LLM — brak mocka Playwright)")
chat_payload = {
    "user_id": USER_ID,
    "session_id": SESSION_ID,
    "message": "Napisz jedno krótkie zdanie testowe po polsku.",
    "mode": "chat",
    "include_debug": False,
    "history": [],
}
resp = request("POST", "/chat/turn", headers=headers_json, json_payload=chat_payload, expected_status=200)
data = get_json(resp, "chat/turn")
expect(data.get("ok") is True, f"/chat/turn ok != true: {data}")
rt = (data.get("response_text") or "").strip()
expect(len(rt) >= 3, f"/chat/turn response_text za krótki lub pusty: {data!r}")
trace = data.get("trace") or {}
expect(trace.get("provider") is not None, f"/chat/turn trace.provider pusty: {trace}")
expect(trace.get("selected_strategy") is not None, f"/chat/turn trace.selected_strategy pusty: {trace}")
log(f"/chat/turn OK provider={data.get('provider')} strategy={trace.get('selected_strategy')}")

if CHAT_ONLY:
    print(
        "[smoke][PASS] tryb chat-only: obowiązkowy dowód LLM (/chat/turn) OK; "
        "pełny stack: SMOKE_MODE=full ./scripts/smoke_runtime.sh"
    )
    sys.exit(0)

# --- OBOWIĄZKOWE: odczyt pamięci (v1 lub v2 — zależnie od env) ---
if legacy_memory_v1_http_disabled():
    log(
        "[mandatory] POST /memory/v2/search "
        "(legacy v1 wyłączony: AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP)"
    )
    v2_payload = {"user_id": USER_ID, "query": "test runtime smoke", "limit": 5}
    resp = request(
        "POST",
        "/memory/v2/search",
        headers=headers_json,
        json_payload=v2_payload,
        expected_status=200,
    )
    data = get_json(resp, "memory/v2/search")
    expect("items" in data, f"/memory/v2/search brak items: {data}")
    expect(isinstance(data.get("items"), list), f"/memory/v2/search items nie jest listą: {data}")
    expect(isinstance(data.get("total_count", 0), int), f"/memory/v2/search total_count: {data}")
    log(
        f"/memory/v2/search OK items={len(data.get('items') or [])} "
        f"total_count={data.get('total_count')}"
    )
else:
    log("[mandatory] POST /memory/search (legacy v1 HTTP)")
    memory_payload = {
        "user_id": USER_ID,
        "query": "test runtime smoke",
        "limit": 5,
    }
    resp = request(
        "POST",
        "/memory/search",
        headers=headers_json,
        json_payload=memory_payload,
        expected_status=200,
    )
    data = get_json(resp, "memory/search")
    expect(data.get("user_id") == USER_ID, f"/memory/search user_id mismatch: {data}")
    expect("stm" in data, f"/memory/search brak stm: {data}")
    expect("psyche" in data, f"/memory/search brak psyche: {data}")
    log(
        f"/memory/search OK stm={len(data.get('stm') or [])} "
        f"episodic={len(data.get('episodic') or [])} semantic={len(data.get('semantic') or [])}"
    )

# --- WARUNKOWE: tick HTTP (legalny 404 gdy AIHUB_ENABLE_AGENT_TICK_HTTP=0) ---
tick_path = "/agent/tick/default?include_debug=false"
if agent_tick_http_enabled():
    log("[optional-env] POST /agent/tick/default (AIHUB_ENABLE_AGENT_TICK_HTTP włączone)")
    resp = request("POST", tick_path, headers=headers_auth, expected_status=200)
    data = get_json(resp, "agent/tick/default")
    expect(data.get("ok") is True, f"/agent/tick/default ok != true: {data}")
    expect(data.get("mode") == "tick", f"/agent/tick/default mode != tick: {data}")
    expect(bool(data.get("strategy")), f"/agent/tick/default strategy puste: {data}")
    log(
        f"/agent/tick/default OK strategy={data.get('strategy')} "
        f"planning_used={data.get('planning_used')} reasoning_used={data.get('reasoning_used')}"
    )
else:
    resp = request_any("POST", tick_path, headers=headers_auth)
    if resp.status_code == 404:
        skips.append(
            "agent_tick_http: SKIP (AIHUB_ENABLE_AGENT_TICK_HTTP=0 — 404 zgodnie z kodem)"
        )
        log(skips[-1])
    else:
        fail(
            f"Oczekiwano 404 przy wyłączonym tick HTTP, "
            f"dostałem {resp.status_code}. Body={pretty_snippet(resp.text)}"
        )

summary_bits = ["mandatory=OK"]
if skips:
    summary_bits.append("; ".join(skips))
else:
    summary_bits.append("optional tick=OK")
print(f"[smoke][PASS] Runtime smoke ({' | '.join(summary_bits)})")
PY
