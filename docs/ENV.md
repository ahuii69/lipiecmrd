# AI-Hub — Zmienne środowiskowe (.env)

Plik `.env` jest automatycznie generowany przez `start.sh` (jeśli nie istnieje).
Brakujące zmienne są dopisywane automatycznie (bez ruszania istniejących).
Klucze `API_KEY` i `AIHUB_TOKEN_SECRET` są generowane losowo przy pierwszym starcie.

## Zmienne

### Core runtime

| Zmienna     | Default              | Opis                        |
| ----------- | -------------------- | --------------------------- |
| `HOST`      | `127.0.0.1`          | Interfejs nasłuchu backendu |
| `PORT_BASE` | `8080`               | Pierwszy port do skanowania |
| `PORT_MAX`  | `8090`               | Ostatni port do skanowania  |
| `WORKERS`   | `1`                  | Liczba workerów uvicorn     |
| `DATA_DIR`  | `data`               | Katalog danych              |
| `DB_PATH`   | `data/aihub.sqlite3` | Ścieżka do bazy SQLite      |
| `LOG_DIR`   | `logs`               | Katalog logów               |
| `LOG_LEVEL` | `INFO`               | Poziom logowania            |

### Security

| Zmienna              | Default            | Opis                                                     |
| -------------------- | ------------------ | -------------------------------------------------------- |
| `API_KEY`            | (auto-gen 64 hex)  | Klucz API do chronionych endpointów (header `X-API-Key`) |
| `AIHUB_TOKEN_SECRET` | (auto-gen 128 hex) | Secret do podpisywania sesji agenta                      |

### Public deployment

| Zmienna      | Default              | Opis                           |
| ------------ | -------------------- | ------------------------------ |
| `DOMAIN`     | `ahui69.org`         | Domena publiczna (Caddy HTTPS) |
| `PUBLIC_URL` | `https://ahui69.org` | Pełny publiczny URL            |

### Safety toggles

| Zmienna                   | Default | Opis                               |
| ------------------------- | ------- | ---------------------------------- |
| `AIHUB_REWRITER_READONLY` | `1`     | `1` = self-rewriter read-only      |
| `SELF_HEAL_WRITE`         | `0`     | `1` = zezwól self-heal na zapis    |
| `SELF_HEAL_SNAPSHOT`      | `0`     | `1` = auto-snapshot przy self-heal |

### Agent worker

| Zmienna               | Default   | Opis                           |
| --------------------- | --------- | ------------------------------ |
| `AGENT_AUTOSTART`     | `1`       | `1` = auto-start agent loop    |
| `AGENT_INTERVAL_S`    | `3.5`     | Interwał tick agenta (sekundy) |
| `AGENT_USER_ID`       | `default` | Domyślny user ID agenta        |
| `AGENT_MAX_RETRIES`   | `3`       | Max retry przy błędzie tick    |
| `AGENT_RETRY_DELAY_S` | `1.0`     | Delay między retries           |

### SSE

| Zmienna             | Default | Opis                   |
| ------------------- | ------- | ---------------------- |
| `SSE_KEEPALIVE_S`   | `15`    | Interwał keepalive SSE |
| `SSE_MAX_EVENT_LOG` | `50000` | Max zdarzeń w logu SSE |

### Web fetch

| Zmienna          | Default   | Opis                       |
| ---------------- | --------- | -------------------------- |
| `HTTP_TIMEOUT_S` | `12`      | Timeout HTTP dla web fetch |
| `HTTP_MAX_BYTES` | `2097152` | Max body size (2 MB)       |

### Memory / Vector (config.py)

| Zmienna                  | Default   | Opis                    |
| ------------------------ | --------- | ----------------------- |
| `STM_MAX_MESSAGES`       | `200`     | Max wiadomości w STM    |
| `LTM_MAX_FACTS_PER_USER` | `20000`   | Max faktów LTM per user |
| `EPISODES_MAX_PER_USER`  | `20000`   | Max epizodów per user   |
| `VEC_MAX_VOCAB`          | `60000`   | Max vocab TF-IDF        |
| `VEC_MAX_DF`             | `0.90`    | Max document frequency  |
| `VEC_MIN_DF`             | `2`       | Min document frequency  |
| `VEC_MAX_TOKENS_PER_DOC` | `6000`    | Max tokenów per doc     |
| `FS_ROOT`                | `data/fs` | Sandbox plików agenta   |

### Vector engine (vector_engine.py)

| Zmienna             | Default                   | Opis                        |
| ------------------- | ------------------------- | --------------------------- |
| `VECTOR_MODEL_NAME` | `all-MiniLM-L6-v2`        | Model sentence-transformers |
| `VECTOR_DIM`        | `384`                     | Wymiar wektorów             |
| `VECTOR_INDEX_PATH` | `./data/vector.index`     | Ścieżka indeksu FAISS       |
| `VECTOR_META_PATH`  | `./data/vector_meta.json` | Ścieżka metadanych wektorów |

## Generowanie kluczy

start.sh generuje klucze automatycznie przy pierwszym starcie:

```bash
# Ręcznie:
python3 -c "import secrets; print(secrets.token_hex(32))"   # API_KEY (64 hex)
python3 -c "import secrets; print(secrets.token_hex(64))"   # AIHUB_TOKEN_SECRET (128 hex)
```

## Nadpisywanie

Domyślnie start.sh **nie nadpisuje** istniejących wartości w `.env`.
Aby wymusić regenerację kluczy i nadpisanie wszystkich zmiennych:

```bash
./start.sh --force-env
```
