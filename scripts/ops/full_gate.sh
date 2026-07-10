#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1
source .venv/bin/activate || exit 1

RUN_ID="$(date +%Y%m%d_%H%M%S)"
OUT="reports/archive/full_gate_${RUN_ID}"
mkdir -p "$OUT"

echo "== ENV ==" | tee "$OUT/00_env.log"
python --version | tee -a "$OUT/00_env.log"
which python | tee -a "$OUT/00_env.log"
node --version | tee -a "$OUT/00_env.log"
npm --version | tee -a "$OUT/00_env.log"

echo "== STOP =="
./stop.sh > "$OUT/01_stop.log" 2>&1 || true

echo "== PY COMPILE =="
python -m compileall -f aihub tests > "$OUT/02_py_compile.log" 2>&1

echo "== IMPORT SMOKE =="
python - <<'PY' > "$OUT/03_import_smoke.log" 2>&1
import importlib

mods = [
    "aihub.main",
    "aihub.chat_api",
    "aihub.chat_runtime",
    "aihub.executive_controller",
    "aihub.cockpit_api",
    "aihub.policy_engine",
    "aihub.reflection_engine",
    "aihub.simulation_engine",
    "aihub.consistency_engine",
    "aihub.prediction_engine",
    "aihub.response_variants_engine",
]

for m in mods:
    importlib.import_module(m)
    print("OK", m)
PY

echo "== PYTEST FULL =="
pytest -q -ra --tb=short > "$OUT/04_pytest_full.log" 2>&1

echo "== FRONT TYPECHECK =="
(
  cd cockpit
  npx tsc --noEmit
) > "$OUT/05_front_typecheck.log" 2>&1

echo "== FRONT LINT =="
(
  cd cockpit
  npx eslint . --max-warnings=0
) > "$OUT/06_front_lint.log" 2>&1

echo "== FRONT BUILD =="
(
  cd cockpit
  npx next build
) > "$OUT/07_front_build.log" 2>&1

echo "== START STACK =="
./start.sh > "$OUT/08_start.log" 2>&1

API_KEY="$(grep -E '^AIHUB_API_KEY=' .env | head -1 | cut -d= -f2-)"
if [ -z "${API_KEY:-}" ]; then
  echo "BRAK AIHUB_API_KEY w .env" > "$OUT/09_backend_health.log"
  exit 1
fi

echo "== BACKEND HEALTH =="
curl -fsS http://127.0.0.1:8080/system/ping > "$OUT/09_backend_health.log"

echo "== CHAT TURN SMOKE =="
curl -fsS -X POST http://127.0.0.1:8080/chat/turn \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${API_KEY}" \
  -d '{
    "user_id": "gate-full",
    "message": "krótki test pełnego runtime decision core"
  }' > "$OUT/10_chat_turn.json"

python - "$OUT/10_chat_turn.json" <<'PY' > "$OUT/10_chat_turn_assert.log" 2>&1
import json
import sys

p = sys.argv[1]
data = json.load(open(p, "r", encoding="utf-8"))
assert data["ok"] is True, data
assert isinstance(data.get("response_text"), str) and data["response_text"].strip(), data
trace = data.get("trace") or {}
for key in [
    "selected_strategy",
    "memory_lookup_happened",
    "psyche_snapshot_happened",
]:
    assert key in trace, f"missing trace key: {key}"
print("CHAT TURN ASSERT OK")
PY

echo "== RUNTIME STATUS SMOKE =="
curl -fsS "http://127.0.0.1:8080/cockpit/agent/gate-full/runtime-status" \
  -H "Authorization: Bearer ${API_KEY}" \
  > "$OUT/11_runtime_status.json"

python - "$OUT/11_runtime_status.json" <<'PY' > "$OUT/11_runtime_status_assert.log" 2>&1
import json
import sys

p = sys.argv[1]
data = json.load(open(p, "r", encoding="utf-8"))
assert data["user_id"] == "gate-full", data
assert "runtime_observability" in data, data
assert "task_trace" in data, data
for key in ["experience_signal", "policy_feedback", "simulation", "computed_reflection"]:
    assert key in data, f"missing parity field: {key}"
print("RUNTIME STATUS ASSERT OK")
PY

echo "== LOG SWEEP =="
grep -RInE "(Traceback|ERROR|CRITICAL|Module not found|Unhandled|Exception:|FAILED)" logs cockpit 2>/dev/null \
  > "$OUT/12_log_sweep.log" || true

echo "== DONE =="
echo "$OUT"
