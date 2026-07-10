# `aihub/psyche/` — LEGACY / UNWIRED (pre-Psyche-V2 stack)

## Decision (06.07 repair sprint, P2)

This package (`policy.py`, `service.py`) implements an older, `legacy_ui`-shaped psyche state
model: a single global mood/beliefs/goals row (`psyche_state` table) plus a no-LLM heuristic
`PolicyEngine`. It predates **Psyche V2** (`aihub/psyche_v2_*.py`, `aihub/psyche_v2_api.py`,
mounted as `psyche_v2_router` in `aihub/main.py`), which is the active, per-user psyche runtime.

**Confirmed zero external references:** nothing outside `aihub/psyche/` itself imports from this
package (checked via grep across the repo, `06.07audyt.md` §13).

**Decision: keep as documented legacy, do not wire, do not delete, do not merge.**
- **Not wired**, because a second, single-global-row psyche model running alongside per-user
  Psyche V2 would be actively misleading (whose mood is it tracking?) and contradicts the single
  canonical runtime psyche.
- **Not deleted**, because it is real working code, not a stub, and destroying it provides no
  operational benefit.
- **Not merged**, because the data models (single global row vs. per-user Psyche V2 state) are
  fundamentally different; merging is a deliberate design decision, not a repair-sprint change.

## If this is ever revived

Any future decision to use this package again must be a deliberate architectural choice recorded
in `ARCHITECTURE.md`, not an accidental import. Until then, treat it as reference material for
the pre-V2 design only.
