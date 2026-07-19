# Release gate — migracje, deploy, rollback, restart, sesje

**Cel:** typowe rzeczy release'owe, których unit testy zwykle nie łapią.  
**Host produkcyjny (ten VPS):** `aihub-backend.service` + `aihub-frontend.service`, repo `/home/ubuntu/mrd`, DB **Postgres**.

---

## Szybka komenda

```bash
# sprawdzenie bez restartu
./scripts/release_gate.sh

# pełny gate + restart backend/frontend (zalecane przed „wydane”)
./scripts/release_gate.sh --restart
```

---

## 1. Migracje świeżej bazy

| Backend | Ścieżka | Dowód |
|---------|---------|--------|
| **SQLite** | `init_db()` → CREATE IF NOT EXISTS + `apply_active_stack_migrations_to_connection` | `aihub/db/runtime.py`; test `tests/test_v2_schema_migration.py` |
| **Postgres** | `init_db()` → `run_postgres_bootstrap(postgres_bootstrap.sql)` | idempotentne `CREATE IF NOT EXISTS` + `ADD COLUMN IF NOT EXISTS` |

Czyste środowisko SQLite (dev/test):

```bash
.venv/bin/pytest -q tests/test_v2_schema_migration.py -m legacy_sqlite_v2
```

Czyste Postgres: nowy DSN → start backendu → `init_db` przy starcie aplikacji.

---

## 2. Upgrade starej bazy

| Stack | Mechanizm | Ryzyko |
|-------|-----------|--------|
| SQLite | ALTER ADD COLUMN w `apply_active_stack_migrations_*` przy każdym `init_db` | niskie (idempotent) |
| Postgres | **ten sam** `postgres_bootstrap.sql` przy każdym `init_db` (ALTER IF NOT EXISTS) | działa **tylko jeśli proces faktycznie odpalił `init_db`** po deployu kodu |

**Znane znalezisko (2026-07-19):** żywa Postgres miała `chat_sessions` bez `archived` / `archived_at`, mimo że DDL jest w bootstrapie — backend nie przeładował `init_db` od czasu dodania ALTER.  
**Fix:** restart `aihub-backend` (lub ręczne `run_postgres_bootstrap`). Gate sprawdza te kolumny.

---

## 3. Deploy na czyste środowisko

Minimalna kolejność:

1. `scripts/rollback_tag.sh` — tag `pre-deploy-<ts>`
2. `git pull` / checkout release
3. Backend: venv + deps; **frontend: `cd cockpit && npm run build`** (wymagane — `next start` bez `.next/BUILD_ID` pada w pętli restartów)
4. `systemctl restart aihub-backend.service` → czeka na `/system/ping` (PG `init_db` może trwać kilka sekund)
5. `systemctl restart aihub-frontend.service` → czeka na BFF ping
6. `./scripts/release_gate.sh` (bez `--restart`) albo pełny z `--restart`

**Pułapka release (2026-07-19):** restart frontu **przed** zakończonym `npm run build` kasuje / nie widzi `BUILD_ID` → `Could not find a production build` + crash-loop. Zawsze: **stop → build → start** (albo build przy działającym starym procesie, potem restart).

Alternatywa bootstrap: `./start.sh --clean` (SQLite-centric; na tym VPS kanoniczne są jednostki systemd).

---

## 4. Rollback

```bash
scripts/rollback_tag.sh          # przed deployem
git reset --hard pre-deploy-<ts>
systemctl restart aihub-backend.service
systemctl restart aihub-frontend.service
./scripts/release_gate.sh
```

Uwaga: rollback **kodu** nie cofa automatycznie danych Postgres. Snapshoty / backup DB osobno (`start.sh --clean` robi backup SQLite; dla PG — snapshot wolumenu / `pg_dump` poza tym gate'em).

---

## 5. Restart procesów

```bash
systemctl restart aihub-backend.service
# czekaj na ping
curl -fsS http://127.0.0.1:8080/system/ping

systemctl restart aihub-frontend.service
curl -fsS http://127.0.0.1:3001/api/aihub/system/ping
```

Backend przy starcie: `init_db()` → upgrade schematu.  
Frontend: nowy build Next (BFF allowlist / policy).

---

## 6. Sesje po deployu

| Warstwa | Przetrwa restart? | Mechanizm |
|---------|-------------------|-----------|
| Login (`aihub_session` cookie) | **TAK** (do TTL) | wiersze w `auth_sessions`; TTL default **43200 s** (`AIHUB_SESSION_TTL_SECONDS`) |
| Lista / treść czatu | **TAK** | historia z API (`chat_session_messages`); store lokalny trzyma metadane, nie fanfik transkryptu |
| Rehydrate UI | **TAK** | `store-rehydrator` + `historyNonce` / fetch history |
| Archive UI (localStorage) | częściowo | lokalne `archivedSessionIds` — sync API archive wymaga kolumn `archived*` (patrz §2) |

Po restarcie użytkownik **nie musi** logować się od zera, o ile cookie i wiersz sesji żyją.  
Po **rebuildzie** frontu: twardy refresh przeglądarki; cookie zostaje.

**Uwaga ops:** przy `ENV=production` i `AIHUB_SESSION_COOKIE_SECURE=false` cookie działa na HTTP localhost za proxy — świadomy kompromis; za publicznym HTTPS bez TLS-terminacji ustaw `true`.

---

## 7. Definition of Done (release)

- [ ] `release_gate.sh` → exit 0  
- [ ] `/ops/ready` → `ready=true`  
- [ ] schema health OK + `chat_sessions.archived*` obecne (PG)  
- [ ] BFF ping 200 po restarcie frontu  
- [ ] Smoke: zalogowany user → stara sesja w sidebarze + historia ładuje się  
- [ ] Smoke: image / `/api/aihub/chat/file/{id}` (po allowliście)  
- [ ] Tag rollback utworzony przed deployem  

---

## Powiązane

- `docs/LAUNCH.md`, `docs/RUNBOOK.md`  
- `20.07_env_bff_full_audit.md` (env + BFF)  
- `scripts/rollback_tag.sh`, `sanity.sh`
