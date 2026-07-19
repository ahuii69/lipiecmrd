#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# AI-Hub — ROLLBACK TAG
# Tworzy git tag pre-deploy-<ts> jako punkt przywracania.
# ─────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

G='\033[0;32m'; Y='\033[0;33m'; NC='\033[0m'
info() { echo -e "${G}[INFO]${NC} $*"; }
warn() { echo -e "${Y}[WARN]${NC} $*"; }

if ! command -v git &>/dev/null || [[ ! -d "$APP_DIR/.git" ]]; then
  warn "Brak git repo — nie mogę utworzyć taga."
  exit 1
fi

TS="$(date +%Y%m%d_%H%M%S)"
TAG="pre-deploy-${TS}"

git tag "$TAG"
info "Tag utworzony: $TAG"
info ""
info "Aby przywrócić do tego punktu:"
info "  git reset --hard $TAG"
info ""
info "Aby usunąć tag (jeśli nie potrzebny):"
info "  git tag -d $TAG"
