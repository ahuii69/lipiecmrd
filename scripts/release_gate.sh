#!/usr/bin/env bash
# Finalny gate przed release: audit + dev gate + functional smoke + pytest + build/test Cockpit.
# Nie modyfikuje .env — korzysta z istniejącego środowiska / npm install w cockpit.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT"

echo "========== [release_gate] 1/6 static release audit (collisions, duplicates, markers, imports, routes) =========="
python3 scripts/release_audit.py --repo "$ROOT"

echo "========== [release_gate] 2/6 dev_gate (manifest, import, kanon krótki) =========="
bash scripts/dev_gate.sh

echo "========== [release_gate] 3/6 functional endpoint smoke (lifespan + Memory/Psyche/Ops) =========="
python3 scripts/functional_endpoint_smoke.py --repo "$ROOT" --db-path "$ROOT/data/release_gate_functional.sqlite3"

# Przy CI lub CHECK_PG_STRICT=1: twardy test Postgres (bez --soft), jeśli .env ma DB_BACKEND=postgres
if [[ "${CI:-}" == "true" ]] || [[ "${CHECK_PG_STRICT:-0}" == "1" ]]; then
  echo "========== [release_gate] 1b strict PostgreSQL (check_pg_ready) =========="
  python3 scripts/check_pg_ready.py
fi

echo "========== [release_gate] 4/6 pytest tests (pełny backend) =========="
pytest -q tests

echo "========== [release_gate] 5/6 cockpit: npm run build =========="
cd "$ROOT/cockpit"
if [[ -f package-lock.json ]]; then
  npm ci
else
  npm install
fi
npm run build

echo "========== [release_gate] 6/6 cockpit: npm test =========="
npm run test

echo "========== [release_gate] OK =========="
