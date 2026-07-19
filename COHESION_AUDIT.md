# COHESION — atrapy zamknięte (19.07)

Cel: nic z listy „szkielet/atrapa/obok ścieżki” nie zostaje martwe; front i backend mówią tym samym językiem.

## Zrobione

| Było | Jest |
|------|------|
| Response variants prawie zawsze OFF | Agentic: variants **ON** przy complexity/uncertainty; trace + cockpit badge |
| Simulation skip adaptive’em | Research/agentic: simulation **ON** gdy budget pozwala |
| Executive ≠ chat | Chat path ustawia `planner_executed` / `effective_runtime_path=chat_planner` |
| Brak cost ledger | `aihub/cost_ledger.py` + zapis w turn + `/ops/cost/today` + `/ops/cost/global-today` + insight UI |
| Vision key pusty | `CHAT_VISION_API_KEY` ← fallback `LLM_API_KEY` |
| `NEXT_PUBLIC_ENVIRONMENT=development` | **production** (`.env`, `cockpit/.env`, `.env.production.local`) |
| Agent workers „niewidoczne” | `/ops/capabilities.agent_workers` + flagi w matrix |
| Brak Sentry | Init gdy `SENTRY_DSN` ustawiony |
| Brak live soak | `scripts/live_soak_runtime.py` (`AIHUB_LIVE_SOAK=1`) |
| Front nie widział adaptive/CSE/cost | `runtime-insight.tsx` pokazuje budget, cost, self_eval, adaptive, variants, simulation, path |

## Memory

Memory V2 jest **CORE** (nie „obok”). Bez zmian statusu — tylko potwierdzenie.

## Live soak

```bash
AIHUB_LIVE_SOAK=1 AIHUB_LIVE_SOAK_MINUTES=15 AIHUB_LIVE_SOAK_MAX_USD=2 \
AIHUB_LIVE_SOAK_USER=... AIHUB_LIVE_SOAK_PASSWORD=... \
python scripts/live_soak_runtime.py
```

## Redis

Nie dorzucamy atrapy Redis „na papierze”. Single-node + Postgres cost ledger jest realny. Redis = osobna decyzja infra, nie fejkowy klient.
