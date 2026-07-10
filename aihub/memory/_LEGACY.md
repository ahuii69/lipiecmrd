# `aihub/memory/` — LEGACY / UNWIRED (pre-Memory-V2 stack)

## Decision (06.07 repair sprint, P2)

This package (`memory.py`, `service.py`, `embedder.py`, `helpers.py`, `utils.py`) implements a
**complete, working, older memory subsystem**: its own SQLite/Postgres tables (`memory`,
`memory_vec`, `memory_fts`), its own embedding calls, vector + FTS + hybrid search, decay scoring.
It is not broken code and not a stub — it is a fully separate implementation that predates
**Memory V2** (`aihub/memory_v2_*.py`, `aihub/memory_v2_api.py`, mounted as `memory_v2_router` in
`aihub/main.py`).

**Confirmed zero external references:** nothing outside `aihub/memory/` itself imports from this
package (checked via grep across the repo, `06.07audyt.md` §13). The active runtime's memory
pillar is **Memory V2**, not this package.

**Decision: keep as documented legacy, do not wire, do not delete, do not merge.**
- **Not wired**, because a second, independently-scored memory subsystem running alongside
  Memory V2 would fragment the memory pillar and contradict the single-canon architecture
  (`aihub/canonical_http_surface.py`).
- **Not deleted**, because it is real, non-trivial, working code (own schema, own search), not a
  worthless duplicate — deleting it destroys a working implementation for no operational benefit,
  which this repair sprint explicitly avoids ("nie usuwać działających funkcji dla świętego
  spokoju").
- **Not merged into Memory V2**, because the schemas and scoring models differ enough that a
  correct merge is a deliberate migration project, not a repair-sprint change.

## If this is ever revived

Any future decision to use this package again (e.g. as a lightweight fallback store) must be a
deliberate architectural choice recorded in `ARCHITECTURE.md`/`MEMORY.md`, not an accidental
import. Until then, treat any code here as reference material for the pre-V2 design, not as an
active or supported code path.
