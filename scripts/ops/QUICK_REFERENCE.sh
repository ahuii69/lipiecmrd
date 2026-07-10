#!/bin/bash
# AI-Hub Bootstrap v5.0 — Quick Reference
# Location: /root/ai-hub/
# Status: ✅ Production-Ready (Phase 5)

# ============================================
# QUICK START
# ============================================

# Start everything (backend + frontend)
./start.sh

# Backend only
./start.sh --no-frontend

# Stop everything gracefully
./stop.sh

# Clean restart (backup DB, remove run files)
./start.sh --clean

# ============================================
# COMMON OPTIONS
# ============================================

# Skip installing dependencies
./start.sh --no-install

# Force-kill port conflicts
./start.sh --force-kill

# Custom ports
PORT_BASE=9000 ./start.sh

# Custom Python/Node
PYTHON_BIN=/usr/bin/python3.11 ./start.sh

# ============================================
# TROUBLESHOOTING
# ============================================

# Check if services are running
ps aux | grep uvicorn
ps aux | grep "npm run dev"

# View logs
tail -f logs/aihub.log              # Backend
tail -f logs/frontend.log           # Frontend
tail -f logs/aihub.error.log        # Errors

# Check API key
grep API_KEY .env

# Health check backend
curl -H "x-api-key: $(grep API_KEY .env | cut -d= -f2)" \
  http://127.0.0.1:8080/system/ping

# Health check frontend
curl http://localhost:3000

# Free up ports if stuck
lsof -iTCP:8080,3000
kill -9 <PID>

# ============================================
# FILES
# ============================================

start.sh              # Bootstrap script (this file: 9.5K)
stop.sh               # Shutdown script (1.9K)
.env                  # Configuration (auto-generated)
cockpit/.env          # Frontend config (auto-generated)
logs/                 # Application logs
data/run/             # PID files, port mappings
data/aihub.sqlite3    # Database

# ============================================
# DEPLOYMENT INFO
# ============================================

# Phase: 5 (Current - Deployment Scripts)
# Status: ✅ COMPLETE

# Backend:
#   - Python 3.10 + FastAPI
#   - Uvicorn on port 8080 (auto-select 8080-8090)
#   - Health check: GET /system/ping with x-api-key

# Frontend:
#   - Node.js + Next.js React
#   - npm dev on port 3000 (auto-select 3000-3010)
#   - Health check: HTTP GET on port

# Security:
#   - API_KEY: Auto-generated 64-char hex
#   - SSRF Protection: Blocks localhost redirects
#   - HTTP_MAX_REDIRECTS: Limited to 5
#   - ENV mode: DEV (loaded from .env) vs PROD (env vars only)

# ============================================
# WHAT'S FIXED IN v5.0
# ============================================

# ✅ Frontend integration (cockpit/)
# ✅ Single-command bootstrap
# ✅ Unified environment setup
# ✅ Health checks for both services
# ✅ Graceful shutdown for both services
# ✅ Auto-generated secrets
# ✅ Port conflict resolution
# ✅ Database backups on clean restart
# ✅ Comprehensive logging

# ============================================
# PRODUCTION CONSIDERATIONS
# ============================================

# Use environment variables (not .env) in production:
ENV=production \
API_KEY="prod-key-xxx" \
DEEPINFRA_API_KEY="xxx" \
./start.sh --no-install

# Run behind reverse proxy (Caddy/Nginx):
./start.sh
# Then configure proxy to forward to http://127.0.0.1:8080 (backend)
# and http://127.0.0.1:3000 (frontend)

# Monitor in production:
tail -f logs/aihub.log
tail -f logs/frontend.log
ps aux | grep -E "uvicorn|npm run dev"

# ============================================
# SUPPORT
# ============================================

# Documentation: DEPLOYMENT_GUIDE_V5.md
# Issues: Check logs/ directory
# Config: .env (backend), cockpit/.env (frontend)
# API Docs: http://127.0.0.1:8080/docs (swagger)

