# AI-Hub — Runbook operacyjny

## 1. Uruchomienie

```bash
./start.sh                              # pełny start (venv + deps + .env + health wait)
./start.sh --no-install                 # bez pip install
./start.sh --clean                      # stop + backup DB + clear run + start
./start.sh --clean --caddy --systemd    # pełny bootstrap produkcyjny
```

systemd: `systemctl start aihub`

> **Port:** start.sh skanuje porty 8080-8090 i bierze pierwszy wolny.
> Aktualny port: `cat data/run/aihub.port`
> W produkcji: Caddy proxy `127.0.0.1:PORT` → `https://ahui69.org`.

## 2. Zatrzymanie

```bash
./stop.sh                   # zatrzymuje pid + systemd service
```

systemd: `systemctl stop aihub`

## 3. Health check

```bash
# Lokalnie (bezstanowy ping)
curl -s http://127.0.0.1:$(cat data/run/aihub.port)/system/ping

# Pełny health (DB, schema)
curl -s http://127.0.0.1:$(cat data/run/aihub.port)/cognitive/health | python3 -m json.tool

# HTTPS (przez Caddy)
curl -s https://ahui69.org/system/ping
curl -s https://ahui69.org/cognitive/health | python3 -m json.tool
```

Oczekiwany wynik `/cognitive/health`: `"status": "ok"`, `"db_schema": {"ok": true}`.
Jeśli `db_schema.ok == false` → restart serwisu → `init_db()` naprawi schema.

## 4. Weryfikacja pełna

```bash
scripts/verify_runtime.sh   # import gate + pytest + local curl + HTTPS curl
```

## 5. Testy

```bash
.venv/bin/python -m pytest -q tests/
```

## 6. Logi

| Plik                   | Zawartość     | Uwagi  |
| ---------------------- | ------------- | ------ |
| `logs/aihub.log`       | Uvicorn + app | Append |
| `logs/aihub.error.log` | Stderr        | Append |

Podgląd live: `tail -f logs/aihub.log`

## 7. DB i GC

```bash
sqlite3 data/aihub.sqlite3 ".tables"
```

Ręczne GC:

```bash
.venv/bin/python -c "
from aihub.memory_gc import collect_garbage
print(collect_garbage('USER_ID'))
"
```

## 8. Backup / Rollback

**DB backup** (automatyczny przy `--clean`):

```bash
ls data/backup/
```

**Git rollback**:

```bash
scripts/rollback_tag.sh              # tworzy tag pre-deploy-<ts>
git reset --hard pre-deploy-<ts>     # przywróć
```

## 9. Troubleshooting

| Objaw             | Rozwiązanie                                                                        |
| ----------------- | ---------------------------------------------------------------------------------- |
| Port zajęty       | `./start.sh --force-kill-port-conflicts`                                           |
| Health timeout    | `tail -200 logs/aihub.error.log`                                                   |
| Import error      | `.venv/bin/python -c "from aihub.main import app"`                                 |
| Caddy 502         | `curl http://127.0.0.1:$(cat data/run/aihub.port)/system/ping` — czy backend żyje? |
| Caddy cert fail   | `caddy validate --config /etc/caddy/Caddyfile` + sprawdź DNS                       |
| systemd fail      | `systemctl status aihub`, `journalctl -u aihub -n 50`                              |
| DB schema broken  | Restart serwisu (init_db auto-fix)                                                 |
| Snapshoty za duże | `./start.sh --purge-snapshots --i-know-what-im-doing`                              |

## 10. Caddy

```bash
systemctl status caddy
systemctl reload caddy
caddy validate --config /etc/caddy/Caddyfile
```

Szczegóły: [docs/DEPLOY_CADDY.md](DEPLOY_CADDY.md)

```bash
tail -f logs/aihub.log
grep "ERROR" logs/aihub.error.log
```

## 8. HTTPS / Caddy

Produkcja idzie przez **Caddy** + HTTPS na `https://ahui69.org`.

| Parametr             | Wartość                                      |
| -------------------- | -------------------------------------------- |
| Caddy version        | v2.11.1                                      |
| Caddyfile            | `/etc/caddy/Caddyfile`                       |
| TLS                  | auto Let's Encrypt                           |
| HTTP/2               | ✅                                           |
| HSTS                 | max-age=63072000, includeSubDomains, preload |
| Reverse proxy target | `127.0.0.1:8080`                             |

```bash
systemctl status caddy
systemctl reload caddy          # po zmianach Caddyfile
caddy validate --config /etc/caddy/Caddyfile
curl -s https://ahui69.org/system/ping    # test PROD
```

Caddyfile: `/etc/caddy/Caddyfile`
aihub service: `/etc/systemd/system/aihub.service`

### LOCAL vs PROD — curl cheatsheet

````bash
# --- LOCAL ONLY (bez Caddy) ---
curl -s http://127.0.0.1:8080/system/ping | python3 -m json.tool
curl -s http://127.0.0.1:8080/cognitive/health | python3 -m json.tool
curl -s -X POST http://127.0.0.1:8080/memory/add \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","user_msg":"test","assistant_msg":"ok","intent":"test","meta":{}}'
curl -s -X POST http://127.0.0.1:8080/psyche/update \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","text":"super","role":"user"}'

# --- PROD (przez Caddy HTTPS) ---
curl -s https://ahui69.org/system/ping | python3 -m json.tool
curl -s https://ahui69.org/cognitive/health | python3 -m json.tool
``

```bash
systemctl status caddy
systemctl reload caddy          # po zmianach Caddyfile
caddy validate --config /etc/caddy/Caddyfile
curl -s https://ahui69.org/system/ping    # test PROD
````

Caddyfile: `/etc/caddy/Caddyfile`
aihub service: `/etc/systemd/system/aihub.service`

### LOCAL vs PROD — curl cheatsheet

````bash
# --- LOCAL ONLY (bez Caddy) ---
curl -s http://127.0.0.1:8080/system/ping | python3 -m json.tool
curl -s http://127.0.0.1:8080/cognitive/health | python3 -m json.tool
curl -s -X POST http://127.0.0.1:8080/memory/add \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","user_msg":"test","assistant_msg":"ok","intent":"test","meta":{}}'
curl -s -X POST http://127.0.0.1:8080/psyche/update \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","text":"super","role":"user"}'

# --- PROD (przez Caddy HTTPS) ---
curl -s https://ahui69.org/system/ping | python3 -m json.tool
curl -s https://ahui69.org/cognitive/health | python3 -m json.tool
``

```bash
systemctl status caddy
systemctl reload caddy          # po zmianach Caddyfile
caddy validate --config /etc/caddy/Caddyfile
curl -s https://ahui69.org/system/ping    # test PROD
````

Caddyfile: `/etc/caddy/Caddyfile`
aihub service: `/etc/systemd/system/aihub.service`

### LOCAL vs PROD — curl cheatsheet

```bash
# --- LOCAL ONLY (bez Caddy) ---
curl -s http://127.0.0.1:8080/system/ping | python3 -m json.tool
curl -s http://127.0.0.1:8080/cognitive/health | python3 -m json.tool
curl -s -X POST http://127.0.0.1:8080/memory/add \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","user_msg":"test","assistant_msg":"ok","intent":"test","meta":{}}'
curl -s -X POST http://127.0.0.1:8080/psyche/update \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","text":"super","role":"user"}'

# --- PROD (przez Caddy HTTPS) ---
curl -s https://ahui69.org/system/ping | python3 -m json.tool
curl -s https://ahui69.org/cognitive/health | python3 -m json.tool
```

Problemy:

- **502** → aihub nie działa → `systemctl start aihub`
- **TLS error** → DNS nie wskazuje na serwer / port 80/443 blokowany

## 9. Rollback

```bash
git reset --hard v0.1.0-wireup
./stop.sh && ./start.sh
systemctl restart caddy
```

## 10. Typowe awarie

| Objaw                         | Rozwiązanie                                              |
| ----------------------------- | -------------------------------------------------------- |
| Health zwraca `MISSING_TABLE` | Restart serwisu (init_db naprawi)                        |
| Port zajęty                   | `./start.sh --force-kill-port-conflicts`                 |
| GC loguje `gc.error`          | Sprawdź lsof na bazie, restart, `PRAGMA integrity_check` |
| Decide zwraca 500             | `grep cognitive_decide logs/aihub.error.log`             |
| Caddy 502                     | `systemctl start aihub` + `systemctl restart caddy`      |
