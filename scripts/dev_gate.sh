#!/usr/bin/env bash
# Lokalny gate przed release: allowlist ↔ manifest, import app, krótkie testy kanonu.
# Pełna regresja:  pytest -q tests
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"

echo "[dev_gate] check_allowlist_canonical_sync"
python3 scripts/check_allowlist_canonical_sync.py

echo "[dev_gate] import aihub.main"
python3 -c "import aihub.main as m; assert m.app is not None"

echo "[dev_gate] check_pg_ready (--soft: gate nie pada gdy Postgres wyłączony lokalnie)"
python3 scripts/check_pg_ready.py --soft

echo "[dev_gate] pytest canonical + allowlist"
pytest -q tests/test_canonical_http_surface.py tests/test_cockpit_proxy_allowlist.py tests/test_psyche_rules_endpoint_filter.py

echo "[dev_gate] OK — pełny pipeline: make release"
