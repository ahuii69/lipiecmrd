# Checklista wdrożenia VPS (handoff po sprzedaży)

Lista dla **integratora lub IT klienta**. Szczegóły operacyjne: [RUNBOOK.md](RUNBOOK.md), [LAUNCH.md](LAUNCH.md), [docs/ENV.md](ENV.md).

## 1. Maszyna

- [ ] Linux (np. Ubuntu LTS), Python **3.11+**, Node **18+** (Cockpit: zalecane 20+).
- [ ] Użytkownik deploy z prawem do `git pull`, `./start.sh`, zapisu w katalogu aplikacji.
- [ ] Firewall: publicznie tylko **80/443**; backend i Next **localhost** (jak w standardowym układzie z reverse proxy).

## 2. Repozytorium i proces

- [ ] Klon na znanej ścieżce (np. zmienna `DEPLOY_PATH` w pipeline — zgodnie z waszym CI).
- [ ] `./start.sh` po deployu; porty w `data/run/aihub.port` i `data/run/frontend.port`.
- [ ] Opcja stabilnego frontu: `./start.sh --prod-frontend` (build + `next start`).

## 3. Sekrety i env

- [ ] **Klucz hubu** ustawiony (aliasy: `AIHUB_API_KEY` / `HUB_API_KEY` / `API_KEY` — patrz `config/hub_key_env_names.json`).
- [ ] **LLM:** `LLM_API_KEY` lub `DEEPINFRA_API_KEY` (zgodnie z `aihub.config`).
- [ ] **Vault produkcyjny:** jawny, stabilny `AIHUB_USER_VAULT_KEY` (nie polegać na seedzie dev); **utrata klucza = brak odszyfrowania vaultu** — patrz [VAULT.md](../VAULT.md).
- [ ] W produkcji: `ENV=production` tylko gdy **wszystkie** wymagane sekrety są w środowisku (plik `.env` nie jest ładowany — patrz `aihub/config.py`).

## 4. Cockpit → backend

- [ ] W `cockpit/.env` / `.env.local`: `AIHUB_BASE_URL` wskazuje na backend (ten sam host/port co BFF oczekuje).
- [ ] Klucz w BFF zgodny z backendem.

## 5. Reverse proxy i TLS

- [ ] Caddy / nginx / inny proxy: TLS na 443, proxy do `127.0.0.1:<backend_port>` i ścieżek frontu zgodnie z waszą konfiguracją.
- [ ] Po deployu: `curl -fsS https://<domena>/system/ping` (lub lokalny ping jak w RUNBOOK).

## 6. Backup i dane

- [ ] Cron lub systemd timer: [scripts/backup_sqlite_vps.sh](../scripts/backup_sqlite_vps.sh) (lub snapshot dysku obejmujący `data/`).
- [ ] Katalog `data/backup/` monitorowany (miejsce na dysku).
- [ ] Przy rotacji klucza vaultu: plan **re-encrypt** (obecnie wymaga procedury poza tym dokumentem).

## 7. Weryfikacja

- [ ] `./scripts/health_check_all.sh` — szybki smoke HTTP (front opcjonalnie: `SKIP_COCKPIT_CHECK=1`).
- [ ] `./scripts/smoke_runtime.sh` (wymaga działającego backendu i kluczy).
- [ ] Opcjonalnie: `scripts/baseline_gate.sh` na środowisku build/CI.

## 7b. Materiały pod dział zakupów (opcjonalnie)

- [ ] `docs/PROCUREMENT_BRIEF.md` — dołączyć do oferty.
- [ ] `./scripts/dump_openapi.sh` → `export/openapi.json`
- [ ] `./scripts/sbom_python_freeze.sh` → `export/requirements-freeze.txt`

## 8. PostgreSQL

- [ ] Jeśli wymóg klienta to **wspólna baza dla wielu replik API** — zaplanować projekt migracji; zobacz [DATABASE_PRODUCTION.md](DATABASE_PRODUCTION.md) (nie jest to przełącznik jednym env).
