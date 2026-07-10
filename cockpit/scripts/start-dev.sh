#!/usr/bin/env bash
##############################################################################
# AI-Hub Cockpit (Frontend) — Development Server Start
##############################################################################

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PORT="${PORT:-3000}"

if [ -f "$ROOT_DIR/.env.local" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT_DIR/.env.local"
    set +a
fi

# Check if node_modules exists
if [ ! -d "$ROOT_DIR/node_modules" ]; then
    echo "⚠ node_modules not found. Installing dependencies..."
    cd "$ROOT_DIR"
    npm ci
fi

echo "✓ Starting AI-Hub Cockpit (dev mode)"
echo "  Frontend: http://localhost:${PORT}"
echo "  Backend:  ${AIHUB_BASE_URL:-http://127.0.0.1:8080}"
echo ""
echo "Press Ctrl+C to stop."
echo ""

cd "$ROOT_DIR"
PORT=$PORT npm run dev
