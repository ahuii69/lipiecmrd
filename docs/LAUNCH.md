# AI-Hub — LAUNCH (Quick Reference)

## Jedna komenda

```bash
./start.sh                              # standardowy start
./start.sh --clean                      # czysta restart (stop + backup DB + clear run)
./start.sh --clean --caddy --systemd    # pełny bootstrap produkcyjny
./start.sh --force-env                  # nadpisz klucze i zmienne w .env
```

## Co robi start.sh?

1. Tworzy `.venv` jeśli nie istnieje
2. `pip install -r requirements.txt` (pomiń: `--no-install`)
3. Generuje `.env` z losowymi kluczami jeśli brak / placeholder
4. Dopełnia **wszystkie** brakujące env vars (bez ruszania istniejących)
5. Import gate: `from aihub.main import app`
6. Startuje uvicorn na wolnym porcie 8080-8090
7. Czeka na `/system/ping` (timeout 25s)
8. `--caddy`: konfiguruje Caddy HTTPS reverse proxy
9. `--systemd`: generuje i aktywuje `aihub.service`

## Opcje start.sh

```
--clean                     Zatrzymaj + backup DB + wyczyść data/run
--no-install                Pomiń pip install
--force-kill-port-conflicts Kill proces na zajętym porcie
--force-env                 Nadpisz istniejące klucze/zmienne w .env
--caddy                     Skonfiguruj Caddy HTTPS
--install-caddy             Zainstaluj Caddy z apt
--systemd                   Wygeneruj i aktywuj aihub.service
--purge-snapshots           Usuń snapshoty (wymaga --i-know-what-im-doing)
-h, --help                  Pełna pomoc
```

## Publiczny URL

```
https://ahui69.org
```

Backend słucha na `127.0.0.1:PORT` — Caddy robi HTTPS i reverse proxy.

## Health check

```bash
# Lokalnie
curl -s http://127.0.0.1:$(cat data/run/aihub.port)/system/ping
curl -s http://127.0.0.1:$(cat data/run/aihub.port)/cognitive/health | python3 -m json.tool

# HTTPS (przez Caddy)
curl -s https://ahui69.org/system/ping
```

## Stop

```bash
./stop.sh                    # zatrzymuje pid + systemd service
```

## Weryfikacja

```bash
scripts/verify_runtime.sh   # import + pytest + curl local + HTTPS
```

## Pliki

| Plik                   | Opis                          |
| ---------------------- | ----------------------------- |
| `requirements.txt`     | Pinned deps (produkcja)       |
| `requirements.lock`    | Pełny pip freeze              |
| `.env`                 | Konfiguracja + klucze         |
| `.env.example`         | Szablon .env (bez sekretów)   |
| `data/run/aihub.pid`   | PID procesu uvicorn           |
| `data/run/aihub.port`  | Port na którym słucha backend |
| `logs/aihub.log`       | Uvicorn stdout + app logs     |
| `logs/aihub.error.log` | Stderr                        |

## Rollback

```bash
scripts/rollback_tag.sh              # tworzy git tag pre-deploy-<ts>
git reset --hard pre-deploy-<ts>     # przywróć do taga
```

## Powiązane docs

- [RUNBOOK.md](RUNBOOK.md) — operacje, troubleshooting
- [DEPLOY_CADDY.md](DEPLOY_CADDY.md) — Caddy HTTPS setup
- [ENV.md](ENV.md) — pełna lista zmiennych środowiskowych

```bash
git reset --hard v0.1.0-wireup
./stop.sh && ./start.sh
systemctl restart caddy
```
