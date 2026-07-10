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

export PORT="${PORT:-3000}"

cd "$ROOT_DIR"

if [ ! -d "$ROOT_DIR/node_modules" ]; then
    echo "node_modules missing -> npm ci"
    npm ci
fi

if [ ! -d ".next" ]; then
    echo "No production build found -> npm run build"
    npm run build
fi

echo "Starting AI-Hub Cockpit (prod) on port ${PORT}"
echo "Proxy target: ${AIHUB_BASE_URL:-http://127.0.0.1:8080}"
npm run start
