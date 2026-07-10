# FINAL_DELIVERY_REPORT — AI-Hub Morda

> **DOKUMENT HISTORYCZNY.** Opisuje stan konkretnego wcześniejszego ZIP-a release. Nie jest aktualnym stanem repo — aktualny stan i naprawy: `06.07audyt.md` / `06.07naprawa.md`. Sekcja "Realne env pliki w paczce" poniżej jest **skorygowana** względem oryginalnego zapisu — `.env`/`cockpit/.env` nie powinny być częścią żadnego dystrybuowanego ZIP-a (patrz `README.md`).

## Status paczki

Finalny ZIP jest oparty na ostatnim hard-functional/runtime buildzie, z końcowym polish pass.

## Realne funkcje dopięte

- FastAPI backend `aihub.main:app`.
- Professional Cockpit frontend `/` i `/user`.
- Memory V2 hybrid retrieval, context-pack, durable index jobs, reindex scripts.
- MemoryContextPack wpięty do prompt/trace.
- Psyche V2 runtime i web/research event writeback.
- Web fetch/research/ingest przez kanoniczne `web_tools` z SSRF guardem.
- Doctor/preflight + release audit + functional endpoint smoke.
- Strict FAISS/semantic embedding runtime: bez cichego NumPy/hash fallbacku w realnym profilu.
- ~~Realne env pliki w paczce: `.env` i `cockpit/.env` bez drukowania sekretów.~~ **KOREKTA (06.07 naprawa):** `.env`/`cockpit/.env` nie powinny być dystrybuowane w ZIP-ie. Tworzone lokalnie na docelowej maszynie z `.env.example`/`cockpit/.env.example`, `chmod 600`, nigdy w repo/release.

## Finalny gate wykonany w sandboxie

- `python3 scripts/release_audit.py --repo .` — OK.
- `python3 -m compileall -q aihub scripts tests` — OK.
- `python3 scripts/functional_endpoint_smoke.py --repo . --db-path /tmp/final_polish_functional.sqlite3` — OK.
- Focused backend tests: 29 passed.
- Frontend: `npm ci`, `npm run typecheck`, `npm run test` — 58 passed, `NEXT_TELEMETRY_DISABLED=1 npm run build` — OK.

## Czego sandbox nie potwierdza

- Live Postgres z Twojego `POSTGRES_DSN`.
- Live LLM provider.
- Live Voyage/embedding provider.
- Live Ollama/STT/Vision usługi.

Te elementy są celowo sprawdzane przez `./doctor.sh --profile prod` i real smoke flags przy `start.sh`.

## Brak śmieci release

Przed ZIP-em usunięto cache/build/runtime artifacts: `__pycache__`, `.pytest_cache`, `node_modules`, `.next`, lokalne sqlite/wal/shm, logi runtime, coverage/test-results/playwright-report.

## Komendy końcowe na VPS

```bash
./doctor.sh --profile prod
START_RUN_REAL_EMBEDDING_SMOKE=1 \
START_RUN_REAL_CHAT_SMOKE=1 \
START_RUN_REAL_MEMORY_SMOKE=1 \
./start.sh --prod --prod-frontend
```

## Ważna uwaga sandbox

W aktualnym sandboxie końcowy `doctor --profile prod/local` nie został oznaczony jako PASS, bo środowisko bazowe nie ma globalnie zainstalowanych ciężkich zależności z `requirements.txt` (`faiss-cpu`, `sentence-transformers`, `psycopg2-binary`). Paczka zawiera te zależności w `requirements.txt`, a `start.sh` instaluje je przed doctorem. Na docelowej maszynie obowiązuje kolejność: `./start.sh` albo ręcznie `.venv/bin/pip install -r requirements.txt` → `./doctor.sh --profile prod/local`.
