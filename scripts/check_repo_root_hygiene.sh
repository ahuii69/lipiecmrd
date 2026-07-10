#!/usr/bin/env bash
# Fail if repo root contains report/log/artifact-style files that belong under reports/archive/.
# Usage: scripts/check_repo_root_hygiene.sh
# Override (emergency only): ROOT_HYGIENE_SKIP=1
set -euo pipefail

if [[ "${ROOT_HYGIENE_SKIP:-0}" == "1" ]]; then
  echo "[root-hygiene] SKIP (ROOT_HYGIENE_SKIP=1)" >&2
  exit 0
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

violations=()
while IFS= read -r -d '' f; do
  base="$(basename "$f")"
  lc="$(printf '%s' "$base" | tr '[:upper:]' '[:lower:]')"

  if [[ "$lc" == "requirements.txt" ]]; then
    continue
  fi
  if [[ "$lc" == *.txt ]]; then
    violations+=("$base (→ reports/archive/; wyjątek w root: tylko requirements.txt)")
  elif [[ "$lc" == *.log ]]; then
    violations+=("$base (→ reports/archive/ lub logs/)")
  elif [[ "$lc" == *_summary* ]]; then
    violations+=("$base (*_summary* → reports/archive/)")
  elif [[ "$lc" == *_report* ]]; then
    violations+=("$base (*_report* → reports/archive/)")
  elif [[ "$lc" == *_result* ]]; then
    violations+=("$base (*_result* → reports/archive/)")
  fi
done < <(find "$ROOT" -maxdepth 1 -type f -print0 2>/dev/null)

if ((${#violations[@]} > 0)); then
  echo "[root-hygiene] FAIL: niedozwolone pliki w katalogu głównym repozytorium:" >&2
  for v in "${violations[@]}"; do
    echo "  - $v" >&2
  done
  echo "[root-hygiene] Przenieś artefakty do reports/archive/ (lub logs/ dla runtime)." >&2
  echo "[root-hygiene] Sprawdź: scripts/check_repo_root_hygiene.sh" >&2
  exit 1
fi

echo "[root-hygiene] OK (root bez *.txt poza requirements.txt, bez *.log, bez *_summary/_report/_result)"
exit 0
