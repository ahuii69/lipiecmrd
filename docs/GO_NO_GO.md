# AI-Hub — GO / NO-GO Checklist

> **DOKUMENT ARCHIWALNY (06.07 naprawa).** Snapshot konkretnego tagu (`v1.1.0-wireup`,
> 2026-03-06), nie aktualny stan repo. Liczby są nieaktualne: obecny `tests/` ma 105+ plików i
> 530+ funkcji testowych (nie 92), a `aihub/canonical_http_surface.py` opisuje ~80 tras (nie 32).
> Traktować jako historyczny zapis jednego wydania, nie jako aktualną checklistę — aktualny stan:
> `06.07audyt.md` / `06.07naprawa.md`.

**Data:** 2026-03-06
**Wersja:** v1.1.0-wireup

## Checklist

| #   | Gate                         | Status | Dowód                                             |
| --- | ---------------------------- | ------ | ------------------------------------------------- |
| 1   | pytest 92 passed, 0 warnings | ✅ GO  | `pytest -q` → `92 passed in 70s`                  |
| 2   | Import gate (32 routes)      | ✅ GO  | `from aihub.main import app; len(app.routes)`     |
| 3   | Cold start (fresh DB)        | ✅ GO  | tmp DB → `init_db()` → all tables present         |
| 4   | Local ping                   | ✅ GO  | `curl http://127.0.0.1:8080/system/ping`          |
| 5   | HTTPS ping                   | ✅ GO  | `curl https://ahui69.org/system/ping`             |
| 6   | /cognitive/health            | ✅ GO  | `{"status":"ok","db_schema":{"ok":true}}`         |
| 7   | /cognitive/decide            | ✅ GO  | `{"action_type":"memory_search"}`                 |
| 8   | /memory/add                  | ✅ GO  | `{"ok":true,"stm_ids":[...]}`                     |
| 9   | /memory/search               | ✅ GO  | `{"stm":[...],"episodic":[...]}`                  |
| 10  | /psyche/update               | ✅ GO  | `{"mood":..,"energy":..,"focus":..}`              |
| 11  | /psyche/reflect              | ✅ GO  | `{"reflection":"..."}`                            |
| 12  | Caddy validate               | ✅ GO  | `caddy validate` → RC=0, "Valid configuration"    |
| 13  | HSTS header                  | ✅ GO  | `Strict-Transport-Security: max-age=63072000`     |
| 14  | Zero TODO/placeholder        | ✅ GO  | `grep -rn "TODO\|FIXME" aihub/` → 0 w zmienionych |
| 15  | Brak breaking changes        | ✅ GO  | Kontrakty endpointów bez zmian                    |

## Verdict

**GO** 🚀

## Rollback

```bash
git reset --hard v0.1.0-wireup
./stop.sh && ./start.sh
systemctl restart caddy
```

## Restart usług

```bash
systemctl restart aihub
systemctl restart caddy
```
