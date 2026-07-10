# AI-Hub v1.1.0 — Release Notes

**Data:** 2026-03-06

## Co dodano

### Wireup Sprint

- **Knowledge Graph** — zasilany z `add_fact()` i `add_episode()` (wcześniej martwy moduł)
- **Attention Controller** — filtruje duże batche (>20 msg) w `agent_tick` (wcześniej martwy)
- **Vector Engine dense boost** — FAISS search w `retrieve_context` (wcześniej martwy)
- **Psyche modulation** — scoring (`_importance_from_text`, `_confidence_from_text`) modulowany przez stan psyche
- **Learning throttle** — `process_turn` ogranicza fakty per turę (energy/focus)

### Infrastruktura

- **Caddy HTTPS** — reverse proxy na `ahui69.org`, auto Let's Encrypt, HTTP/2, HSTS
- **systemd service** — `aihub.service` z auto-restart
- **Caddyfile** — zstd+gzip encode, security headers, reverse_proxy

### Poprzedni sprint (v1.0.0)

- start.sh / stop.sh / sanity.sh
- Log rotation, ENV config
- memory_facts VIEW, memory_meta cold-start
- 36 testów regresji P2-P8

## Co naprawiono

- **Pytest warnings** — 4 external (huggingface/SWIG) → filtrowane, 0 warnings
- **memory_meta cold-start** — zero "no such table" na świeżej bazie
- **Broad-except** w memory_gc.py — zawężone do `sqlite3.Error, OSError`

## Brak breaking changes

- Endpointy: BEZ ZMIAN
- `retrieve_context`: rozszerzony o `dense_hits` (addytywne, backwards-compatible)

## Jak testować

```bash
.venv/bin/python -m pytest -q          # 92 passed, 0 warnings
./sanity.sh                             # health + decide + GC
curl -s https://ahui69.org/system/ping  # {"ok":true}
curl -s https://ahui69.org/cognitive/health
```
