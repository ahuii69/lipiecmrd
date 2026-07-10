#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Spójna kopia zapasowa SQLite dla AI-Hub na VPS (bez zatrzymywania serwisu).
# Używa wbudowanego polecenia .backup sqlite3 — bezpieczniejsze niż cp na otwarty plik.
#
# Zmienne środowiskowe:
#   APP_DIR       — katalog repo (domyślnie: katalog nadrzędny względem scripts/)
#   DB_PATH       — pełna ścieżka do pliku .sqlite3 (domyślnie: $APP_DIR/data/aihub.sqlite3)
#   BACKUP_DIR    — katalog kopii (domyślnie: $APP_DIR/data/backup)
#   RETAIN_LAST   — opcjonalnie: zostaw tylko N najnowszych kopii aihub.sqlite3.backup_*
#
# Cron przykład (codziennie 3:15):
#   15 3 * * * APP_DIR=/opt/morda /opt/morda/scripts/backup_sqlite_vps.sh >>/var/log/aihub-backup.log 2>&1
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="${DB_PATH:-$APP_DIR/data/aihub.sqlite3}"
BACKUP_DIR="${BACKUP_DIR:-$APP_DIR/data/backup}"
RETAIN_LAST="${RETAIN_LAST:-}"

mkdir -p "$BACKUP_DIR"

if [[ ! -f "$DB_PATH" ]]; then
  echo "[backup_sqlite_vps] SKIP: brak pliku DB: $DB_PATH" >&2
  exit 0
fi

ts="$(date +%Y%m%d_%H%M%S)"
out="$BACKUP_DIR/aihub.sqlite3.backup_${ts}"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[backup_sqlite_vps] ERR: brak polecenia sqlite3 (apt install sqlite3)" >&2
  exit 1
fi

# .backup tworzy spójny snapshot nawet przy działającym writerze (SQLite).
sqlite3 "$DB_PATH" ".backup '$out'"
echo "[backup_sqlite_vps] OK $out"

if [[ -n "${RETAIN_LAST}" ]] && [[ "${RETAIN_LAST}" =~ ^[0-9]+$ ]] && [[ "${RETAIN_LAST}" -gt 0 ]]; then
  mapfile -t files < <(ls -1t "$BACKUP_DIR"/aihub.sqlite3.backup_* 2>/dev/null || true)
  if ((${#files[@]} > RETAIN_LAST)); then
    for ((i = RETAIN_LAST; i < ${#files[@]}; i++)); do
      rm -f "${files[$i]}"
      echo "[backup_sqlite_vps] removed old ${files[$i]}"
    done
  fi
fi
