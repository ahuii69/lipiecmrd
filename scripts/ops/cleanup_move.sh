#!/usr/bin/env bash
# CLEANUP SPRINT v3.1 — przenieś martwy kod do aihub/_dead/
#
# ⚠️ HISTORYCZNY SKRYPT — NIE URUCHAMIAĆ „na ślepo”: lista plików jest przestarzała
# (np. reflection_engine / goals_engine są aktywne w runtime). Przed mv zweryfikuj
# każdą pozycję (grep importów). Usunięto z listy: agent_memory.py, context_builder.py.
#
# Uruchom z katalogu repo (np. /root/morda). Idempotentny: mv -n = nie nadpisuj.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== CLEANUP SPRINT v3.1 — przenoszenie martwego kodu ==="
DEAD=aihub/_dead
mkdir -p "$DEAD"/{standalone,subdirs,agent_toplevel,artifacts}

REPORT=reports/archive/CLEANUP_EXEC_REPORT.md
mkdir -p reports/archive
echo "# CLEANUP EXEC REPORT — $(date -Iseconds)" > "$REPORT"
echo "" >> "$REPORT"
echo "## Przeniesienia" >> "$REPORT"

moved=0
log_move() { echo "- \`$1\` → \`$2\`" >> "$REPORT"; moved=$((moved + 1)); }

# ─── Sekcja 1: DEAD standalone aihub/*.py ─────────────────────
for f in \
  aihub/agent_planner.py \
  aihub/agent_runtime_patch.py \
  aihub/goals_engine.py \
  aihub/reflection_engine.py \
  aihub/procedures.py \
  aihub/prompt.py \
  aihub/prompts.py \
  aihub/tools.py \
  aihub/planner.py \
  aihub/run.sh \
  aihub/DEPRECATED.md
do
  if [ -f "$f" ]; then
    mv -nv "$f" "$DEAD/standalone/"
    log_move "$f" "$DEAD/standalone/$(basename "$f")"
  fi
done

# ─── Sekcja 2: DEAD subdirs (cały katalog) ───────────────────
# UWAGA: core/ — ZACHOWUJEMY core/__init__.py + core/security.py
#   (auth_patch.py → core/security.py = runtime chain)
#   Przenosimy TYLKO dead pliki z core/: config.py, background.py, logging.py, openapi.py
mkdir -p "$DEAD/subdirs/core"
for cf in config.py background.py logging.py openapi.py; do
  src="aihub/core/$cf"
  if [ -f "$src" ]; then
    mv -nv "$src" "$DEAD/subdirs/core/"
    log_move "$src" "$DEAD/subdirs/core/$cf"
  fi
done

# Reszta subdirów — move całe
for d in \
  aihub/_legacy_api \
  aihub/routers \
  aihub/middleware \
  aihub/psyche \
  aihub/memory \
  aihub/web \
  aihub/fs \
  aihub/sse \
  aihub/util \
  aihub/workers
do
  if [ -d "$d" ]; then
    mv -nv "$d" "$DEAD/subdirs/"
    log_move "$d" "$DEAD/subdirs/$(basename "$d")"
  fi
done

# ─── Sekcja 3: top-level agent/ ──────────────────────────────
if [ -d agent ]; then
  mv -nv agent "$DEAD/agent_toplevel/"
  log_move "agent/" "$DEAD/agent_toplevel/"
fi

# ─── Sekcja 4: artefakty .orig/.rej/.patch ───────────────────
for f in \
  aihub/services/self_rewriter.py.orig \
  aihub/services/self_rewriter.py.rej \
  aihub/services/self_rewriter.py.patch
do
  if [ -f "$f" ]; then
    mv -nv "$f" "$DEAD/artifacts/"
    log_move "$f" "$DEAD/artifacts/$(basename "$f")"
  fi
done

# ─── Sekcja 5: dead files in services/ ───────────────────────
# Zostawiamy: self_rewriter.py (bin/ utility), __init__.py
for f in \
  aihub/services/events.py \
  aihub/services/memory_intel.py \
  aihub/services/predictor.py
do
  if [ -f "$f" ]; then
    mv -nv "$f" "$DEAD/standalone/"
    log_move "$f" "$DEAD/standalone/$(basename "$f")"
  fi
done

# ─── Sekcja 6: __pycache__ cleanup ───────────────────────────
find "$DEAD" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$DEAD" -name '*.pyc' -delete 2>/dev/null || true
# Czyść pycache w reszcie aihub
find aihub -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ─── Raport ───────────────────────────────────────────────────
echo "" >> "$REPORT"
echo "## Podsumowanie" >> "$REPORT"
echo "Przeniesiono: $moved elementów" >> "$REPORT"
echo "" >> "$REPORT"
echo "## Git status" >> "$REPORT"
if command -v git &>/dev/null && [ -d .git ]; then
  echo '```' >> "$REPORT"
  git status --short >> "$REPORT" 2>&1 || true
  echo '```' >> "$REPORT"
else
  echo "_brak git_" >> "$REPORT"
fi

echo ""
echo "=== Przeniesiono $moved elementów. ==="
echo "=== Raport: $REPORT ==="
echo ""
echo "=== GATE: ==="
echo "  python -c \"from aihub.main import app; print('routes:', len(app.routes))\""
echo "  pytest -q tests/"
echo "  ./sanity.sh"
