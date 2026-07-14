# `aihub/api/*` — legacy boundary (19.07)

## Mounted on `aihub.main:app`

| Module | Prefix |
|--------|--------|
| `security_router` | `/system/security` |
| `self_heal_status_router` | `/system/self-heal-db` |

## Archived (19.07)

Unmounted routers (`fs`, `memory*`, `psyche*`, `sse`, `anomaly`) were moved to
`archive/legacy_routers/api_unmounted/` and are **not** importable as `aihub.api.*`.

Earlier dangerous routers remain in `archive/legacy_routers/` (ai_compat, ops, admin_events).
