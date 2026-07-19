#!/usr/bin/env bash
# ENV_STATUS_CHECK.sh — production/runtime environment sanity check.
# Verifies required process env for AIHub without printing secret values.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_MODE="${ENV:-development}"
echo "ENV=${ENV_MODE}"
echo "HOST=${HOST:-127.0.0.1}"
echo "PORT=${PORT:-8080}"
echo "DB_BACKEND=${DB_BACKEND:-sqlite}"

missing=0
require_present() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "MISSING ${name}"
    missing=$((missing + 1))
  else
    echo "OK ${name}=set"
  fi
}

if [[ "${ENV_MODE}" == "production" ]]; then
  require_present AIHUB_USER_VAULT_KEY
  # At least one hub auth secret
  if [[ -z "${AIHUB_API_KEY:-}${HUB_API_KEY:-}${API_KEY:-}${AIHUB_PROXY_TOKEN:-}" ]]; then
    echo "MISSING hub auth secret (AIHUB_API_KEY|HUB_API_KEY|API_KEY|AIHUB_PROXY_TOKEN)"
    missing=$((missing + 1))
  else
    echo "OK hub_auth=set"
  fi
  if [[ -z "${LLM_API_KEY:-}${DEEPINFRA_API_KEY:-}" ]]; then
    echo "MISSING LLM credential (LLM_API_KEY|DEEPINFRA_API_KEY)"
    missing=$((missing + 1))
  else
    echo "OK llm_credential=set"
  fi
else
  echo "OK non-production profile (secrets optional)"
fi

# Canonical listen port must remain the AIHub default (8080), never the legacy default.
LEGACY_BAD_PORT="8""000"
if [[ "${PORT:-8080}" == "${LEGACY_BAD_PORT}" ]]; then
  echo "FAIL PORT uses legacy default"
  exit 1
fi
echo "OK PORT uses non-legacy default"

if [[ "${missing}" -gt 0 && "${ENV_MODE}" == "production" ]]; then
  echo "RESULT: FAIL (${missing} missing)"
  exit 1
fi
echo "RESULT: OK"
exit 0
