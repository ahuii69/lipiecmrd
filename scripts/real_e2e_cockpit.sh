#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR/cockpit"
export PLAYWRIGHT_REAL_HUB=1
exec npm run test:e2e:real
