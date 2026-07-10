# Pamięć — AI-Hub

## Po co jest pamięć

Łączy **krótki kontekst sesji** (historia w `POST /chat/turn`) z **trwałą wiedzą użytkownika**: fakty, epizody, węzły grafu, opcjonalnie embeddingi oraz **Memory V2** (strukturalne wpisy, procedury, konflikty).

## Składowe

1. **STM** — ostatnie wiadomości w grafie (per `user_id`).
2. **LTM / graf** — węzły L1/L2, relacje, spójność.
3. **Wektor / TF-IDF** — opcjonalnie (zależnie od konfiguracji i dostawcy embeddingów).
4. **Memory V2** — osobna warstwa API i zapisów (`memory_v2_*`), rekomendowana dla nowych integracji.

## Jak trafia wiedza do modelu

- Runtime czatu woła unified retrieval i wstrzykuje **skrót** do promptu (nie pełny zrzut bazy).
- Przy długiej historii sesji stosowany jest **smart trim** (`smart_clip_chat_history`): rollup starszej części + ostatnie surowe wiadomości — patrz [ARCHITECTURE.md](ARCHITECTURE.md).

## Czego pamięć NIE robi

- **Nie przechowuje treści vaultu** (haseł) jako faktów do retrievalu — granica w `vault/firewall.py`.
- Nie zastępuje audytu bezpieczeństwa: wrażliwe dane nie powinny trafiać do promptu poza vault.

## Konfiguracja (skrót)

- `STM_MAX_MESSAGES`, ścieżki DB: `aihub.config` / zmienne `DATA_DIR`, `DB_PATH`.
- Wyłączenie legacy HTTP V1: `AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP`.

## Ograniczenia

- Domyślnie **SQLite** — jedna instancja zapisu; klastrowanie wymaga migracji.
- Jakość retrievalu zależy od treningu danych i limitów tokenów — długie sesje są przycinane.
