# Architektura AI-Hub (Morda)

## Czym jest system

**AI-Hub** to backend w **FastAPI** (`aihub/main.py`) z warstwą pamięci, narzędziami (web, research, pliki, agent) oraz frontendem operatorskim **Cockpit** (Next.js) i uproszczoną powłoką użytkownika. Jeden proces API obsługuje czat kanoniczny (`POST /chat/turn`), pamięć (V1 graf + V2), psyche, agenta i panele cockpit.

## Główne warstwy

| Warstwa | Odpowiedzialność | Kluczowe moduły |
|--------|-------------------|-----------------|
| **HTTP / API** | Routing, auth hubu, OpenAPI | `aihub/main.py`, `*_api.py`, `canonical_http_surface.py` |
| **Chat runtime** | Orchestracja LLM ↔ narzędzia, kontekst, trace | `chat_runtime.py`, `chat_context_compose.py`, `chat_deterministic.py` |
| **Pamięć** | STM/LTM, graf, wektor (opcjonalnie), Memory V2 | `memory_core.py`, `memory_engine.py`, `memory_v2_*` |
| **Vault** | Sekrety użytkownika (szyfrowane), poza STM/embeddings | `vault/`, `user_vault.py` |
| **Narzędzia** | Rejestr capability, router wykonania | `tools/registry.py`, `tools/router.py`, `web_tools.py`, `research_engine.py` |
| **Agent / planner** | Cykle zadań, handoff z czatu | `agent_engine.py`, `executive_controller.py`, `planner_engine.py` |
| **Frontend** | BFF do hubu, czat, panele | `cockpit/app`, proxy `/api/aihub/*` |

## Przepływ czatu (uproszczony)

1. Klient wysyła `POST /chat/turn` z `user_id`, `session_id`, `message`, `history` (opcjonalnie stream).
2. **Deterministyczne skróty** (vault, meta-pytania o historię sesji) mogą zakończyć turę bez LLM.
3. W przeciwnym razie budowany jest kontekst: system prompt + **smart trim** historii (rollup + ostatnie N wiadomości) + pamięć + psyche + ewentualnie web prefetch.
4. Provider LLM + pętla narzędzi (limit iteracji w konfiguracji).
5. Zapis transkryptu sesji, write-back do pamięci V2 / doświadczeń (zgodnie z ścieżką).

Szczegóły sprintów i podsystemów poznawczych: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Ograniczenia architektoniczne

- Skalowanie poziome API: poza zakresem obecnego repo (SQLite jako domyślny magazyn).
- Pełna izolacja tenantów wymaga review `user_id` i polityk na poziomie wdrożenia.
- Legacy HTTP pod `aihub/api/` nie jest montowane w `main` — patrz [aihub/api/_LEGACY.md](aihub/api/_LEGACY.md).
