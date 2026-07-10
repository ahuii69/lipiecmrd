#!/usr/bin/env bash
# Smoke self-hosted: Ollama + (opcjonalnie) POST /chat/stt do działającego hubu.
# Repo root = katalog nadrzędny względem tego skryptu.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY="$ROOT/.venv/bin/python"
else
  PY="${PYTHON:-python3}"
fi

export CHAT_VISION_OLLAMA_URL="${CHAT_VISION_OLLAMA_URL:-http://127.0.0.1:11434}"
export AIHUB_SMOKE_BASE_URL="${AIHUB_SMOKE_BASE_URL:-http://127.0.0.1:8080}"

if [[ -f "$ROOT/.env" ]]; then
  # shellcheck disable=SC2046
  eval "$("$PY" "$ROOT/scripts/dotenv_tool.py" exports "$ROOT/.env" 2>/dev/null)" || true
fi

EXTRA=()
if [[ "${SELFHOSTED_SMOKE_SKIP_STT:-0}" == "1" ]]; then
  EXTRA+=(--skip-stt)
fi

echo "[smoke_selfhosted_stt_vision.sh] CHAT_VISION_OLLAMA_URL=$CHAT_VISION_OLLAMA_URL AIHUB_SMOKE_BASE_URL=$AIHUB_SMOKE_BASE_URL" >&2
"$PY" -m aihub.scripts.selfhosted_stt_vision_smoke "${EXTRA[@]}"
RC=$?

if [[ "$RC" -eq 0 ]]; then
  echo "[smoke_selfhosted_stt_vision.sh] RESULT: PASS"
else
  echo "[smoke_selfhosted_stt_vision.sh] RESULT: FAIL (exit $RC)" >&2
fi
exit "$RC"
