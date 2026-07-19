"""Capability closing: detect intents and escalate chat into real tools/handoff."""

from __future__ import annotations

import re
from typing import Any

from aihub.chat_image_generation import is_image_generation_intent

# Local linguistic / editorial checks — must NOT escalate to research.query.
_LOCAL_CHECK_RE = re.compile(
    r"(?iu)("
    r"pisowni|ortografi|gramaty|interpunkcj|stylistyk|"
    r"poprawność\s+językow|poprawnosc\s+jezykow|"
    r"spelling|grammar|proofread|punctuation|\btypos?\b|"
    r"check\s+(?:my\s+)?(?:spelling|grammar|punctuation)|"
    r"sprawd[źz]\s+(?:pisownię|pisownie|ortografię|ortografie|gramatykę|gramatyke|"
    r"interpunkcję|interpunkcje|styl|ten\s+tekst|tego\s+tekstu|to\s+zdanie|tego\s+zdania|"
    r"ten\s+akapit|ten\s+fragment)"
    r")"
)

# Explicit web/search verbs — always external.
_SEARCH_RE = re.compile(
    r"(?iu)\b("
    r"wyszukaj|poszukaj|google|look\s*up|search\s+for|search\s+the\s+web"
    r")\b"
)

# Verify verb alone is not enough — needs an external cue (below).
_VERIFY_VERB_RE = re.compile(
    r"(?iu)\b(sprawdź|sprawdz|zweryfikuj|check|verify)\b"
)

# External-world cues that make "sprawdź X" a web/research intent.
_WEB_VERIFY_CUE_RE = re.compile(
    r"(?iu)\b("
    r"aktualn|cen[aęy]|kurs|bitcoin|btc|eth|pogoda|notowan|"
    r"wersj[aęei]|release|changelog|"
    r"w\s+internecie|online|w\s+sieci|na\s+żywo|najnowsz|"
    r"ile\s+kosztuje|jaka\s+jest\s+aktualn|"
    r"current|today|latest|live\s+price|right\s+now|"
    r"źródł|zrodl|source\s+of\s+truth|oficjaln"
    r")\b"
)

_EXECUTE_RE = re.compile(
    r"(?iu)\b("
    r"wykonaj|zrób\s+to|zrob\s+to|zrób\s+teraz|zrob\s+teraz|"
    r"odpal|uruchom|wdróż|wdroz|zastosuj|przeprowadź|przeprowadz|"
    r"execute|do\s+it|run\s+it|apply\s+now|go\s+ahead|"
    r"zrób\s+(?!obraz|grafik|rysunek)|zrob\s+(?!obraz|grafik|rysunek)"
    r")\b"
)

_REMEMBER_RE = re.compile(
    r"(?iu)\b("
    r"zapamiętaj|zapamietaj|zapisz[,\s]+że|zapisz[,\s]+ze|"
    r"remember\s+that|note\s+that|save\s+this\s+fact"
    r")\b"
)

_FRESHNESS_RE = re.compile(
    r"(?iu)\b("
    r"aktualn|dzisiaj|dziś|dzis|na\s+żywo|najnowsz|"
    r"current|today|latest|live\s+price|right\s+now|"
    r"wersja\s+\d|kurs\s+(?:eur|usd|btc|bitcoin)|pogoda"
    r")\b"
)

_PLAN_ONLY_RE = re.compile(
    r"(?iu)("
    r"niczego\s+nie\s+wykonuj|nie\s+wykonuj|tylko\s+plan|bez\s+wykonywania|"
    r"don't\s+execute|do\s+not\s+execute|napisz\s+plan|rozpisz\s+plan"
    r")"
)

_INGEST_RE = re.compile(
    r"(?iu)\b("
    r"wczytaj|ingest|zapisz\s+(?:tę|te|tą|ta)?\s*stron|"
    r"dodaj\s+do\s+pamięci|fetch\s+and\s+remember|save\s+(?:this\s+)?(?:page|url|link)|"
    r"przeczytaj\s+i\s+zapamiętaj|przeczytaj\s+i\s+zapamietaj"
    r")\b"
)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

# Mutations that must never be auto-confirmed by capability escalation / handoff.
_SENSITIVE_EXECUTE_RE = re.compile(
    r"(?iu)\b("
    r"usuń|usun|delete|skasuj|wyślij\s+mail|wyslij\s+mail|send\s+(?:an?\s+)?(?:email|mail)|"
    r"zapisz\s+(?:do\s+)?plik|write\s+file|fs\.write|nadpisz|overwrite"
    r")\b"
)


def is_local_editorial_check(message: str) -> bool:
    """True for spelling/grammar/style checks that stay on the LLM path."""
    return bool(_LOCAL_CHECK_RE.search(message or ""))


def is_external_verify_intent(message: str) -> bool:
    """True only when the user wants external/world verification or search."""
    text = (message or "").strip()
    if not text:
        return False
    if is_local_editorial_check(text):
        return False
    if _SEARCH_RE.search(text):
        return True
    if _URL_RE.search(text) and _VERIFY_VERB_RE.search(text):
        return True
    if _VERIFY_VERB_RE.search(text) and _WEB_VERIFY_CUE_RE.search(text):
        return True
    return False


def detect_capability_intents(message: str) -> dict[str, bool]:
    text = (message or "").strip()
    low = text.lower()
    plan_only = bool(_PLAN_ONLY_RE.search(low))
    execute = bool(_EXECUTE_RE.search(low)) and not plan_only
    has_url = bool(_URL_RE.search(text))
    ingest = has_url and (bool(_INGEST_RE.search(low)) or bool(_REMEMBER_RE.search(low)))
    local_check = is_local_editorial_check(text)
    verify = is_external_verify_intent(text)
    freshness = bool(_FRESHNESS_RE.search(low)) and not local_check
    return {
        "verify": verify,
        "local_check": local_check,
        "execute": execute,
        "plan_only": plan_only,
        "remember": bool(_REMEMBER_RE.search(low)) and not ingest,
        "freshness": freshness,
        "image": is_image_generation_intent(text),
        "ingest": ingest,
        "sensitive_mutation": bool(_SENSITIVE_EXECUTE_RE.search(low)),
    }


def apply_capability_escalation(
    decision_core: dict[str, Any],
    message: str,
) -> dict[str, bool]:
    """Mutate decision_core so existing tools can actually run.

    Called AFTER ``_finalize_escalation`` so flags stick. Does not disable strategies —
    escalates whitelist / web / handoff when intents demand it.

    Never auto-confirms tools that require user confirmation (fs.write, snapshot, …).
    """
    intents = detect_capability_intents(message)
    decision_core["capability_intents"] = dict(intents)
    forced: list[str] = list(decision_core.get("forced_tool_prefixes") or [])
    codes = list(decision_core.get("reason_codes") or [])

    strategy = str(decision_core.get("selected_strategy") or "instant")
    web = str(decision_core.get("web_decision") or "off")
    needs_tools = False

    # Explicit: capability closing never grants confirmation / sensitive mutations.
    decision_core["mutation_auto_confirm"] = False
    decision_core["respect_tool_confirmation"] = True

    if intents.get("local_check"):
        codes.append("CAPABILITY_LOCAL_CHECK_NO_WEB")

    if intents["freshness"] or intents["verify"]:
        if web == "off":
            decision_core["web_decision"] = "required"
            decision_core["web_decision_reason"] = (
                "capability_escalation_freshness"
                if intents["freshness"]
                else "capability_escalation_verify"
            )
        elif web == "optional":
            decision_core["web_decision"] = "required"
            decision_core["web_decision_reason"] = "capability_escalation_optional_to_required"
        for p in ("research.", "web.", "memory.search", "memory.get_context"):
            if p not in forced:
                forced.append(p)
        if strategy in {"instant", "direct", "contextual"}:
            decision_core["selected_strategy"] = "research"
            decision_core["strategy_escalated_from"] = strategy
            decision_core["strategy_escalation_reason"] = "capability_verify_or_freshness"
            codes.append("CAPABILITY_ESCALATE_RESEARCH")
        needs_tools = True

    if intents["remember"]:
        for p in ("memory.add_fact", "memory.", "memory.search"):
            if p not in forced:
                forced.append(p)
        decision_core["force_memory_add_fact"] = True
        needs_tools = True
        codes.append("CAPABILITY_FORCE_MEMORY_ADD_FACT")

    if intents["ingest"]:
        m = _URL_RE.search(message or "")
        if m:
            decision_core["ingest_url"] = m.group(0).rstrip(").,;]>\"'")
        for p in ("web.ingest_url", "web.", "memory."):
            if p not in forced:
                forced.append(p)
        decision_core["force_web_ingest"] = True
        needs_tools = True
        codes.append("CAPABILITY_FORCE_WEB_INGEST")

    if intents["image"]:
        if "image." not in forced:
            forced.append("image.")
        decision_core["force_image_generate"] = True
        needs_tools = True
        codes.append("CAPABILITY_FORCE_IMAGE_GENERATE")
        if strategy in {"instant", "direct"}:
            decision_core["selected_strategy"] = "contextual"
            decision_core["strategy_escalated_from"] = strategy
            decision_core["strategy_escalation_reason"] = "capability_image_generate"

    # Plan→Execute: handoff unless verify/freshness needs chat-side web tools first.
    if intents["execute"] and not intents["plan_only"]:
        if intents["verify"] or intents["freshness"]:
            codes.append("CAPABILITY_EXECUTE_DEFERRED_FOR_WEB")
        else:
            decision_core["force_agent_execute"] = True
            decision_core["escalation_final_mode"] = "planner"
            decision_core["execution_mode"] = "planner"
            # Handoff may plan mutating steps, but must not auto-confirm them.
            decision_core["mutation_confirmation_required"] = True
            if strategy in {"instant", "direct", "contextual", "research"}:
                decision_core["selected_strategy"] = "agentic"
                decision_core["strategy_escalated_from"] = strategy
                decision_core["strategy_escalation_reason"] = "capability_execute_intent"
            codes.append("CAPABILITY_FORCE_AGENT_EXECUTE")
            if intents.get("sensitive_mutation"):
                codes.append("CAPABILITY_SENSITIVE_MUTATION_NEEDS_CONFIRM")
            for p in ("planner.", "agent.", "runtime.", "fs.", "memory.", "system."):
                if p not in forced:
                    forced.append(p)
            needs_tools = True

    if forced:
        decision_core["forced_tool_prefixes"] = forced
        needs_tools = True

    if needs_tools:
        decision_core["escalation_use_tools"] = True
        decision_core["capability_tools_required"] = True

    decision_core["reason_codes"] = codes
    decision_core["final_strategy"] = str(
        decision_core.get("selected_strategy") or decision_core.get("final_strategy") or "instant"
    )
    return intents
