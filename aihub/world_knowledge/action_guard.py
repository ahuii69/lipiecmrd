"""ActionClaimGuard — block false 'done/fixed/deployed' claims without evidence."""

from __future__ import annotations

import re
from typing import Any

# Completed / performed action claims (past / resultative), not capability language.
_ACTION_CLAIM = re.compile(
    r"(?iu)\b("
    r"naprawion[eya]|naprawiłem|naprawiłam|"
    r"wysłan[eya]|wysłałem|wysłaliśmy|"
    r"wdrożon[eya]|wdrożyłem|wdrożyliśmy|"
    r"zapisane|zapisałem|zapisaliśmy|"
    r"został[ay]?\s+uruchomion[eya]|"
    r"uruchomiłem|uruchomiłam|uruchomiliśmy|"
    r"zrestartowan[eya]|zrestartowałem|"
    r"zrobiłem|zrobiłam|zrobiliśmy|"
    r"wykonałem|wykonałam|wykonaliśmy|"
    r"\bfixed\b|\bdeployed\b|\brestarted\b|\blaunched\b|\bcreated\b|\bexecuted\b"
    r")\b"
)

_ALREADY_HONEST = re.compile(
    r"(?iu)("
    r"nie\s+uruchomi|"
    r"nie\s+uruchamia|"
    r"nie\s+zrobił|"
    r"nie\s+wykonał|"
    r"żeby\s+wykona|"
    r"powinno\s+działa|"
    r"model-only|"
    r"bez\s+realnego|"
    r"nie\s+mogę\s+uczciwie|"
    r"mogę\s+uruchomić|"
    r"mogę\s+teraz\s+realnie"
    r")"
)

_SAFE_REWRITE = (
    "Przygotowałem plan / podjąłem próbę, ale brak potwierdzonego dowodu wykonania i walidacji — "
    "nie mogę uczciwie stwierdzić, że akcja została w pełni wykonana."
)


def apply_action_claim_guard(
    *,
    response_text: str,
    tool_results: list[Any] | None = None,
    validation_succeeded: bool = False,
    execution_effects: list[str] | None = None,
    trace: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Rewrite unverified action claims. Returns (text, meta)."""
    text = response_text or ""
    meta: dict[str, Any] = {
        "action_claim_guard_applied": False,
        "action_claim_verified": False,
        "action_claim_blocked": False,
    }
    if not _ACTION_CLAIM.search(text):
        return text, meta
    # Do not fight existing anti-hallucination wording.
    if _ALREADY_HONEST.search(text):
        meta["action_claim_guard_applied"] = True
        meta["action_claim_verified"] = False
        if trace is not None:
            trace["action_claim_guard_applied"] = True
            trace["action_claim_skipped_honest"] = True
        return text, meta

    meta["action_claim_guard_applied"] = True
    tools_ok = False
    for r in tool_results or []:
        ok = getattr(r, "ok", None)
        if ok is None and isinstance(r, dict):
            ok = r.get("ok")
        if ok:
            tools_ok = True
            break

    verified = bool(validation_succeeded and tools_ok)
    if not verified and tools_ok:
        # Tool succeeded with explicit validation payload.
        for r in tool_results or []:
            out = getattr(r, "output", None)
            if out is None and isinstance(r, dict):
                out = r.get("output")
            if isinstance(out, dict) and out.get("validation_ok") is True:
                verified = True
                break
            meta_r = getattr(r, "meta", None) or (r.get("meta") if isinstance(r, dict) else None)
            if isinstance(meta_r, dict) and meta_r.get("validation_ok") is True:
                verified = True
                break
    if not verified and execution_effects:
        verified = any(str(e).startswith("validated:") for e in execution_effects)

    if verified:
        meta["action_claim_verified"] = True
        if trace is not None:
            trace["action_claim_guard_applied"] = True
            trace["action_claim_verified"] = True
        return text, meta

    meta["action_claim_blocked"] = True
    rewritten = _ACTION_CLAIM.sub("niepotwierdzone jeszcze", text)
    if "nie mogę uczciwie" not in rewritten.lower():
        rewritten = _SAFE_REWRITE + "\n\n" + rewritten
    if trace is not None:
        trace["action_claim_guard_applied"] = True
        trace["action_claim_verified"] = False
        trace["action_claim_blocked"] = True
    return rewritten, meta
