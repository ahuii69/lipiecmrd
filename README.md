# AI-Hub Morda — final runtime package

Self-hosted AI runtime: FastAPI backend, Memory V1/V2, MemoryContextPack, Psyche V2, web/research tools, vector memory with FAISS, and responsive GPT/Grok-style Cockpit frontend.

## Co jest w paczce

- Backend: `aihub.main:app` / FastAPI.
- Frontend: `cockpit/` / Next.js.
- Pamięć: STM/LTM, Memory V2, hybrid retrieval, context-pack, index jobs, reindex.
- Psychika: Psyche V2 runtime, event writeback, policy/adaptation.
- Web: guarded fetch/research/ingest, SSRF guard, Psyche V2 event writeback.
- Ops: `/ops/health`, `/ops/ready`, `/ops/capabilities`, doctor, real smoke scripts.
- Env: **root `.env` i `cockpit/.env` NIE są częścią żadnej dystrybuowanej paczki/ZIP i nie powinny nigdy w takiej trafić.** To pliki lokalne dla tej konkretnej instalacji (VPS), tworzone z `.env.example` / `cockpit/.env.example` i wypełniane realnymi sekretami wyłącznie na docelowej maszynie. Uprawnienia plików: `chmod 600` (właściciel procesu, brak dostępu dla innych). Sekretów nie drukować w logach/raportach/audytach — nigdy nie commitować `.env` do repozytorium ani nie dołączać do release ZIP.

## Start — local profile

Local przełącza storage na SQLite, ale **nie wyłącza realnych LLM/embeddingów z `.env`**.

```bash
./doctor.sh --profile local
START_RUN_REAL_EMBEDDING_SMOKE=1 \
START_RUN_REAL_CHAT_SMOKE=1 \
START_RUN_REAL_MEMORY_SMOKE=1 \
./start.sh --local --prod-frontend
```

## Start — prod profile

Prod używa `.env` jako prawdy: Postgres, LLM, Voyage/embedding, frontend BFF.

```bash
./doctor.sh --profile prod
START_RUN_REAL_EMBEDDING_SMOKE=1 \
START_RUN_REAL_CHAT_SMOKE=1 \
START_RUN_REAL_MEMORY_SMOKE=1 \
./start.sh --prod --prod-frontend
```

## Najważniejsze komendy

```bash
./doctor.sh --profile local          # preflight env/deps/import/routes/db/cockpit env
./scripts/release_gate.sh            # pełny lokalny gate release
python3 scripts/functional_endpoint_smoke.py --repo .
./scripts/reindex_memory_v2.sh --all --enqueue --process --limit 1000
./stop.sh
```

## Endpointy kontrolne

```text
GET  /system/ping
GET  /ops/health
GET  /ops/ready
GET  /ops/capabilities
POST /chat/turn
POST /memory/v2/context-pack
GET  /memory/v2/index-jobs
GET  /psyche/v2/runtime/{user_id}
GET  /web/health
```

## Wymagania

- Python 3.11+
- Node.js 18+
- `pip install -r requirements.txt` musi zainstalować m.in. `faiss-cpu`, `sentence-transformers`, `psycopg2-binary`.
- Dla profilu prod musi odpowiadać `POSTGRES_DSN`.
- Dla pełnego runtime musi odpowiadać LLM provider i embedding provider z `.env`.

## Dokumentacja

- `API.md` — powierzchnia HTTP.
- `ARCHITECTURE.md` — architektura systemu.
- `MEMORY.md` — pamięć i retrieval.
- `WEB.md` — web/research.
- `VAULT.md` — vault/security.
- `docs/` — runbooki, deployment, analizy i checklisty.
- `FINAL_DELIVERY_REPORT.md` — końcowy raport paczki.

## Zasada release

Paczka ma startować przez doctor/start, a nie przez zgadywanie. Jeśli Postgres/LLM/Voyage/FAISS/STT/Vision nie działają na docelowej maszynie, doctor lub smoke ma wywalić konkretny błąd przed udawaniem sukcesu.
