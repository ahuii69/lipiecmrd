# Minimalne cele pomocnicze (repo głównie Python + cockpit).
.PHONY: check check-pg release quality
check:
	bash scripts/dev_gate.sh

# Preflight PostgreSQL (gdy DB_BACKEND=postgres w .env): DSN + psycopg2 + SELECT 1
check-pg:
	python3 scripts/check_pg_ready.py

# Pełny gate: check + pytest tests + cockpit build + cockpit test (zob. scripts/release_gate.sh)
release:
	bash scripts/release_gate.sh

quality: release
