#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical prompt-budget profiles and trivial-turn writeback policy (25.07).

Single source of truth for lightweight vs full turn cost envelopes.
Does not invent a new pipeline — callers apply the selected profile.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal

BudgetProfile = Literal["meta_light", "casual_light", "contextual", "research", "agentic"]
TurnValueClass = Literal[
    "trivial",
    "conversational",
    "informative",
    "corrective",
    "procedural",
    "goal_related",
    "research",
    "agentic",
    "feedback",
]
WritebackPolicy = Literal["minimal", "standard", "full"]

PROMPT_BUDGET_VERSION = "26.07.1"

# Soft prompt caps (estimated tokens ≈ chars/4); real provider usage is authoritative.
PROFILE_PROMPT_TOKEN_CAPS: dict[BudgetProfile, int] = {
    "meta_light": 1200,
    "casual_light": 1600,
    "contextual": 8000,
    "research": 10000,
    "agentic": 12000,
}

PROFILE_MAX_COMPLETION: dict[BudgetProfile, int] = {
    "meta_light": 256,
    "casual_light": 256,
    "contextual": 2048,
    "research": 2048,
    "agentic": 4096,
}

PROFILE_HISTORY_MAX_MESSAGES: dict[BudgetProfile, int] = {
    "meta_light": 0,  # 2 only when prior-ref (applied by selector)
    "casual_light": 4,
    "contextual": 12,
    "research": 4,
    "agentic": 16,
}

HEAVY_WRITEBACKS = (
    "memory_v2",
    "knowledge",
    "learning",
    "reflection",
    "experience",
    "cognitive_calibration",
    "procedural_extraction",
    "strategy_learning",
    "self_model",
    "goal_progress",
    "long_horizon",
    "success_patterns",
)

MINIMAL_WRITEBACKS_ALLOWED = (
    "transcript",
    "session_state",
    "psyche_light_event",
    "provider_metrics",
)

META_LIGHT_SYSTEM_PROMPT = (
    "Jesteś Mordzix, asystent AI-Hub. Odpowiedz krótko i konkretnie po polsku.\n"
    "Nie twierdź, że wykonałeś narzędzia, których nie użyto w tej turze.\n"
    "AI-Hub może korzystać z różnych dostawców modeli (failover); "
    "nie utożsamiaj prefiksu nazwy modelu (np. openai/) z dostawcą API "
    "ani nie twierdź, że działałeś przez OpenAI API / ChatGPT, jeśli finalny provider jest inny.\n"
    "Przy pytaniu kim jesteś / jak działasz: opisz produkt i ogólny sposób pracy "
    "(rozmowa, opcjonalne narzędzia gdy potrzebne, awaryjne przełączanie dostawców) "
    "— bez zgadywania konkretnego providera tej tury, chyba że pytanie wprost o to pyta "
    "i masz metadata runtime.\n"
    "Bez helpdesku, bez korpo-fraz, bez fałszywej biografii."
)

CASUAL_LIGHT_SYSTEM_PROMPT = (
    "Jesteś Mordzix z AI-Hub. Rozmawiasz luźno, po ludzku, po polsku.\n"
    "Odpowiedz krótko. Rozpoznaj slang, teasing i sarkazm — nie przechodź w tryb helpdesku "
    "ani urzędniczy. Nie twierdź, że użyłeś narzędzi, których nie użyto. "
    "Bez korpo-fraz i bez listy capabilities."
)

CONTEXTUAL_BOUNDED_SYSTEM_PROMPT = (
    "Jesteś Mordzix, asystent AI-Hub. Odpowiadaj po polsku, konkretnie i naturalnie.\n"
    "Używaj podanego kontekstu pamięci, gdy jest istotny. Nie wymyślaj faktów o użytkowniku.\n"
    "Przy korekcie użytkownika superseduj stary fakt nowym.\n"
    "Nie twierdź, że wykonałeś narzędzia bez dowodu w tej turze.\n"
    "Bez helpdesku i bez korpo-fraz."
)

RESEARCH_BOUNDED_SYSTEM_PROMPT = (
    "Jesteś Mordzix, asystent AI-Hub. Odpowiadaj po polsku na podstawie zweryfikowanych źródeł z tej tury.\n"
    "Podaj źródło i świeżość danych. Nie odpowiadaj wyłącznie z pamięci modelu przy pytaniach aktualnych.\n"
    "Nie twierdź, że sprawdziłeś sieć, jeśli nie było realnego web lookup.\n"
    "Bez helpdesku."
)

_FEEDBACK_MARKERS = (
    "za rozwlekł",
    "za dług",
    "krócej",
    "krotcej",
    "bardziej zwięz",
    "bardziej zwiez",
    "maksymalnie krót",
    "maksymalnie krot",
    "bardzo krót",
    "bardzo krot",
    "odpowiadaj mi bardzo",
    "preferuję",
    "preferuje",
    "nie lubię",
    "nie lubie",
    "od teraz",
    "przestań",
    "przestan",
    "zawsze odpowiadaj",
    "pisz mi odpowiedzi",
    "pisz odpowiedzi",
    "nie skracaj",
    "pełne szczegóły",
    "pelne szczegoly",
)

_REMEMBER_MARKERS = (
    "zapamiętaj",
    "zapamietaj",
    "zapisz, że",
    "zapisz ze",
    "zapisz że",
    "remember that",
    "note that",
)

_CORRECTION_MARKERS = (
    "nie, jednak",
    "nie jednak",
    "nie, chodziło",
    "nie chodzilo",
    "poprawka:",
    "korekta:",
    "jednak lubi",
    "jednak nie lubi",
    "nie stosuj już",
    "nie stosuj juz",
    "zmień procedurę",
    "zmien procedure",
    "odwołuję",
    "odwoluje",
)

_PROCEDURAL_MARKERS = (
    "gdy proszę",
    "gdy prosze",
    "odpowiadaj zawsze",
    "procedur",
    "schemat:",
    "najpierw logi",
    "diagnoza →",
    "diagnoza ->",
    "krokami:",
    "zawsze w formacie",
)

_CASUAL_EXACT = frozenset(
    {
        "elo",
        "hej",
        "cześć",
        "czesc",
        "siema",
        "hi",
        "hello",
        "hey",
        "yo",
        "dzięki",
        "dzieki",
        "thx",
        "thanks",
        "ty",
        "ok",
        "oki",
        "spoko",
        "luz",
        "git",
        "no i git",
        "no i git xd",
        "dobre",
        "dobre było",
        "dobre bylo",
        "dobre było xd",
        "dobre bylo xd",
        "co tam",
        "co tam?",
    }
)

_CASUAL_PHRASES = (
    "elo mordzix",
    "no i git",
    "dobre było",
    "dobre bylo",
    "lody robisz",
    "ale z ciebie",
    "odpierdalasz",
    "z ciebie debil",
    "xD",
    "xd",
)

_PROVIDER_ASK_MARKERS = (
    "jaki provider",
    "jaki model",
    "który model",
    "ktory model",
    "which model",
    "what provider",
    "what model",
)

# Planning / multi-step work — never starve under meta/casual budget even if strategy
# was later downgraded to instant by psyche/learning overlays.
_AGENTIC_MARKERS = (
    "zaplanuj",
    "zaplanować",
    "zaplanowac",
    "migracj",
    "trzyetap",
    "podziel na etap",
    "podziel ją na etap",
    "podziel ja na etap",
    "śledź postęp",
    "sledz postep",
    "śledź postep",
    "śledź ten plan",
    "sledz ten plan",
    "plan migracji",
    "zadanie długoterminowe",
    "zadanie dlugoterminowe",
    "orchestr",
    "multi-step",
    "wielostopni",
)

_RECALL_MARKERS = (
    "jak nazywa",
    "jak się nazywa",
    "jak sie nazywa",
    "mój pies",
    "moj pies",
    "pamiętasz",
    "pamietasz",
    "co mówiłem",
    "co mowilem",
    "jak mam na",
)


@dataclass
class PromptBudgetDecision:
    profile: BudgetProfile
    turn_value_class: TurnValueClass
    writeback_policy: WritebackPolicy
    max_prompt_tokens: int
    max_completion_tokens: int
    history_max_messages: int
    allow_tools: bool
    allow_memory: bool
    allow_knowledge: bool
    allow_learning_influence: bool
    allow_simulation: bool
    allow_critic_llm: bool
    allow_response_variants: bool
    reason_codes: list[str] = field(default_factory=list)
    layers_included: list[str] = field(default_factory=list)
    layers_skipped: list[str] = field(default_factory=list)

    def to_trace(self) -> dict[str, Any]:
        return {
            "budget_profile": self.profile,
            "turn_value_class": self.turn_value_class,
            "writeback_policy": self.writeback_policy,
            "max_prompt_tokens": self.max_prompt_tokens,
            "max_completion_tokens": self.max_completion_tokens,
            "history_max_messages": self.history_max_messages,
            "allow_tools": self.allow_tools,
            "allow_memory": self.allow_memory,
            "prompt_budget_version": PROMPT_BUDGET_VERSION,
            "reason_codes": list(self.reason_codes),
        }


def estimate_tokens(text: str) -> int:
    """Cheap chars/4 estimate — not billed usage."""
    n = len(text or "")
    return max(0, (n + 3) // 4)


def content_hash(text: str, *, n: int = 12) -> str:
    raw = (text or "").encode("utf-8", errors="replace")
    return hashlib.sha256(raw).hexdigest()[:n]


def is_casual_smalltalk(user_text: str) -> bool:
    """Social / slang / teasing turns that must stay on casual_light."""
    from aihub.strategy_selector import is_simple_greeting

    text = (user_text or "").strip()
    if not text:
        return False
    if is_simple_greeting(text):
        return True
    low = text.lower().rstrip("!?., ")
    if low in _CASUAL_EXACT:
        return True
    if any(p.lower() in low for p in _CASUAL_PHRASES):
        # Exclude if also a remember/correction/procedural/feedback signal.
        if looks_remember(text) or looks_correction(text) or looks_procedural(text) or looks_feedback(text):
            return False
        return True
    words = [w for w in low.split() if w]
    if len(words) <= 5 and low.endswith("xd"):
        return True
    return False


def looks_feedback(user_text: str) -> bool:
    low = (user_text or "").lower()
    return any(m in low for m in _FEEDBACK_MARKERS)


def looks_remember(user_text: str) -> bool:
    low = (user_text or "").lower()
    return any(m in low for m in _REMEMBER_MARKERS)


def looks_correction(user_text: str) -> bool:
    low = (user_text or "").lower()
    return any(m in low for m in _CORRECTION_MARKERS)


def looks_procedural(user_text: str) -> bool:
    low = (user_text or "").lower()
    return any(m in low for m in _PROCEDURAL_MARKERS)


def select_prompt_budget(
    *,
    user_text: str,
    selected_strategy: str | None = None,
    web_decision: str | None = None,
    history: list[Any] | None = None,
    mode: str | None = None,
) -> PromptBudgetDecision:
    """Pick budget profile before prompt build / after strategy is known."""
    from aihub.strategy_selector import (
        is_assistant_meta_ask,
        meta_ask_refers_to_prior_conversation,
    )

    text = (user_text or "").strip()
    strat = (selected_strategy or "instant").strip().lower()
    web = (web_decision or "off").strip().lower()
    turn_mode = (mode or "chat").strip().lower()

    has_feedback = looks_feedback(text)
    provider_ask = any(m in text.lower() for m in _PROVIDER_ASK_MARKERS)
    meta = is_assistant_meta_ask(text)
    prior = meta and meta_ask_refers_to_prior_conversation(text)
    casual = is_casual_smalltalk(text)
    remember = looks_remember(text)
    correction = looks_correction(text)
    procedural = looks_procedural(text)

    low = text.lower()
    looks_agentic = any(k in low for k in _AGENTIC_MARKERS)
    looks_recall = any(k in low for k in _RECALL_MARKERS)

    # Durable memory / correction / procedural instructions beat agentic handoff
    # and false web=required from stem matches (e.g. „sprawdzenie” in a procedure).
    if remember or correction or procedural or looks_recall:
        explicit_live = strat == "research" and web == "required" and not (
            remember or correction or procedural
        )
        if not explicit_live and (remember or correction or procedural or looks_recall):
            tvc: TurnValueClass = "informative"
            if correction:
                tvc = "corrective"
            elif procedural:
                tvc = "procedural"
            elif remember:
                tvc = "informative"
            elif has_feedback:
                tvc = "feedback"
            return _profile(
                "contextual",
                turn_value=tvc,
                writeback="standard",
                reasons=["BUDGET_CONTEXTUAL"]
                + (["BUDGET_REMEMBER"] if remember else [])
                + (["BUDGET_CORRECTION"] if correction else [])
                + (["BUDGET_PROCEDURAL"] if procedural else [])
                + (["BUDGET_RECALL_CONTEXTUAL"] if looks_recall else []),
            )

    # Explicit agent mode / planning is never a lightweight chat envelope.
    if turn_mode in ("agent", "planner", "executive") or strat == "agentic" or (
        looks_agentic and not meta and not casual
    ):
        return _profile(
            "agentic",
            turn_value="agentic",
            writeback="full",
            reasons=["BUDGET_AGENTIC"]
            + (["BUDGET_AGENTIC_FROM_MODE"] if turn_mode in ("agent", "planner", "executive") else [])
            + (["BUDGET_AGENTIC_FROM_TEXT"] if looks_agentic else []),
            history_override=PROFILE_HISTORY_MAX_MESSAGES["agentic"],
        )

    if strat == "research" or web in ("required", "optional"):
        return _profile(
            "research",
            turn_value="research",
            writeback="full",
            reasons=["BUDGET_RESEARCH"],
        )

    if (strat in ("contextual",) and not meta and not casual):
        return _profile(
            "contextual",
            turn_value="informative",
            writeback="standard",
            reasons=["BUDGET_CONTEXTUAL"],
        )

    if meta and has_feedback:
        d = _profile(
            "meta_light",
            turn_value="feedback",
            writeback="minimal",
            reasons=["BUDGET_META_FEEDBACK"],
            history_override=2 if prior else 0,
        )
        d.allow_learning_influence = False
        d.reason_codes.append("META_FEEDBACK_USER_MODEL_OK")
        return d

    if meta:
        hist_n = 2 if prior else 0
        d = _profile(
            "meta_light",
            turn_value="trivial",
            writeback="minimal",
            reasons=["BUDGET_META_LIGHT"] + (["META_PRIOR_REF_HISTORY"] if prior else []),
            history_override=hist_n,
        )
        if provider_ask:
            d.reason_codes.append("META_PROVIDER_ASK")
            d.turn_value_class = "informative"
        return d

    if has_feedback and not casual:
        # Preference / length style — still light prompt envelope, user-model writeback.
        d = _profile(
            "casual_light" if len(text.split()) <= 12 else "contextual",
            turn_value="feedback",
            writeback="minimal",
            reasons=["BUDGET_FEEDBACK_PREFERENCE"],
        )
        if d.profile == "contextual":
            d.writeback_policy = "standard"
        d.reason_codes.append("FEEDBACK_USER_MODEL_OK")
        return d

    if casual:
        return _profile(
            "casual_light",
            turn_value="trivial",
            writeback="minimal",
            reasons=["BUDGET_CASUAL_LIGHT"],
        )

    # Instant/direct may be a learning/psyche downgrade of a heavier turn.
    # Never treat arbitrary short technical phrases as casual — only explicit smalltalk.
    if strat in ("instant", "direct"):
        if is_casual_smalltalk(text):
            return _profile(
                "casual_light",
                turn_value="trivial",
                writeback="minimal",
                reasons=["BUDGET_CASUAL_FROM_INSTANT"],
            )
        return _profile(
            "contextual",
            turn_value="informative",
            writeback="standard",
            reasons=["BUDGET_CONTEXTUAL_FROM_INSTANT"],
        )

    return _profile(
        "contextual",
        turn_value="informative",
        writeback="standard",
        reasons=["BUDGET_DEFAULT_CONTEXTUAL"],
    )


def _profile(
    name: BudgetProfile,
    *,
    turn_value: TurnValueClass,
    writeback: WritebackPolicy,
    reasons: list[str],
    history_override: int | None = None,
) -> PromptBudgetDecision:
    heavy_off = name in ("meta_light", "casual_light")
    included = ["base_identity", "style_short"]
    skipped = []
    if name == "meta_light":
        included = ["meta_light_contract"]
        skipped = [
            "persona_handbook",
            "anti_hallucination_full",
            "memory",
            "psyche_full",
            "cognitive",
            "learning",
            "knowledge",
            "policy",
            "corrections",
            "tools",
            "web_policy",
            "capabilities",
            "critic",
            "execution_handbook",
        ]
    elif name == "casual_light":
        included = ["casual_light_contract", "persona_light", "psyche_light"]
        skipped = [
            "memory_heavy",
            "memory_v2",
            "vector",
            "knowledge",
            "learning",
            "tools",
            "web_policy",
            "critic",
            "planner",
            "goals",
            "simulation",
            "reflection_llm",
        ]
    else:
        included = ["full_stack"]
        skipped = []

    return PromptBudgetDecision(
        profile=name,
        turn_value_class=turn_value,
        writeback_policy=writeback,
        max_prompt_tokens=PROFILE_PROMPT_TOKEN_CAPS[name],
        max_completion_tokens=PROFILE_MAX_COMPLETION[name],
        history_max_messages=(
            history_override
            if history_override is not None
            else PROFILE_HISTORY_MAX_MESSAGES[name]
        ),
        allow_tools=name not in ("meta_light", "casual_light"),
        allow_memory=name in ("contextual", "research", "agentic"),
        allow_knowledge=name in ("contextual", "research", "agentic"),
        allow_learning_influence=name in ("contextual", "research", "agentic"),
        allow_simulation=name in ("research", "agentic"),
        # Casual still needs deterministic/pragmatics critic (teasing ≠ literal recipe).
        allow_critic_llm=name != "meta_light",
        allow_response_variants=name in ("agentic",),
        reason_codes=list(reasons),
        layers_included=included,
        layers_skipped=skipped,
    )


def build_meta_light_system_prompt() -> str:
    return META_LIGHT_SYSTEM_PROMPT


def build_casual_light_system_prompt() -> str:
    return CASUAL_LIGHT_SYSTEM_PROMPT


def build_contextual_bounded_system_prompt(
    *,
    memory_brief: str = "",
    psyche_brief: str = "",
    correction_hints: str = "",
    procedures_brief: str = "",
) -> str:
    parts = [CONTEXTUAL_BOUNDED_SYSTEM_PROMPT]
    mb = (memory_brief or "").strip()
    if mb and mb not in ("(brak)", "brak"):
        parts.append("Pamięć (bounded):\n" + mb[:1600])
    pb = (psyche_brief or "").strip()
    if pb and pb not in ("BRAK DANYCH", "brak"):
        parts.append("Ton (psyche):\n" + pb[:400])
    if procedures_brief.strip():
        parts.append("Procedury:\n" + procedures_brief.strip()[:600])
    ch = (correction_hints or "").strip()
    if ch:
        parts.append("Korekty użytkownika:\n" + ch[:600])
    return "\n\n".join(parts)


def build_research_bounded_system_prompt(*, memory_brief: str = "") -> str:
    parts = [RESEARCH_BOUNDED_SYSTEM_PROMPT]
    mb = (memory_brief or "").strip()
    if mb and mb not in ("(brak)", "brak"):
        parts.append("Kontekst pomocniczy (nie zastępuje źródeł):\n" + mb[:800])
    return "\n\n".join(parts)


def build_prompt_budget_trace(
    *,
    decision: PromptBudgetDecision,
    system_text: str,
    history_messages: list[Any] | None = None,
    tool_schema_chars: int = 0,
    layer_chars: dict[str, int] | None = None,
) -> dict[str, Any]:
    hist = history_messages or []
    hist_chars = 0
    for m in hist:
        if hasattr(m, "content"):
            hist_chars += len(str(getattr(m, "content", "") or ""))
        elif isinstance(m, dict):
            hist_chars += len(str(m.get("content") or ""))
    sys_chars = len(system_text or "")
    layers = dict(layer_chars or {})
    if decision.profile == "meta_light" and "meta_light" not in layers:
        layers["meta_light"] = sys_chars
    return {
        "system_chars": sys_chars,
        "system_estimated_tokens": estimate_tokens(system_text),
        "system_content_hash": content_hash(system_text),
        "history_chars": hist_chars,
        "history_estimated_tokens": estimate_tokens("x" * hist_chars),
        "history_message_count": len(hist),
        "tool_schema_chars": int(tool_schema_chars or 0),
        "tool_schema_estimated_tokens": max(0, (int(tool_schema_chars or 0) + 3) // 4),
        "layer_chars": layers,
        "layers_included": list(decision.layers_included),
        "layers_skipped": list(decision.layers_skipped),
        "budget_profile": decision.profile,
        "prompt_budget_version": PROMPT_BUDGET_VERSION,
        "max_prompt_tokens": decision.max_prompt_tokens,
    }


def classify_turn_value_class(
    *,
    user_text: str,
    budget: PromptBudgetDecision | None = None,
    has_tool_results: bool = False,
    has_web: bool = False,
    has_correction: bool = False,
) -> TurnValueClass:
    text = user_text or ""
    if has_correction or looks_correction(text):
        return "corrective"
    if looks_procedural(text):
        return "procedural"
    if looks_feedback(text):
        return "feedback"
    if looks_remember(text):
        return "informative"
    if has_web:
        return "research"
    if has_tool_results:
        return "procedural"
    if budget is not None:
        return budget.turn_value_class
    if is_casual_smalltalk(text):
        return "trivial"
    return "conversational"


def writebacks_for_policy(policy: WritebackPolicy) -> tuple[list[str], list[str]]:
    """Return (executed_allowed, skipped)."""
    plan = resolve_writeback_plan(policy=policy, turn_value_class="conversational")
    return list(plan["executed"]), list(plan["skipped"])


def resolve_writeback_plan(
    *,
    policy: WritebackPolicy,
    turn_value_class: str = "conversational",
    runtime_mode: str = "production",
    replay_mode: bool = False,
    success: bool = True,
    has_user_feedback: bool = False,
    has_source_evidence: bool = False,
    has_tool_evidence: bool = False,
) -> dict[str, Any]:
    """Canonical write-back matrix for a turn (single source of truth)."""
    tvc = (turn_value_class or "conversational").strip().lower()
    reasons: dict[str, str] = {}
    executed: list[str] = []
    skipped: list[str] = []

    if replay_mode:
        for w in list(MINIMAL_WRITEBACKS_ALLOWED) + list(HEAVY_WRITEBACKS) + ["user_model"]:
            skipped.append(w)
            reasons[w] = "replay_mode"
        return {
            "policy": policy,
            "turn_value_class": tvc,
            "executed": [],
            "skipped": skipped,
            "skip_reasons": reasons,
            "replay_mode": True,
        }

    if policy == "minimal" or tvc == "trivial":
        executed = ["transcript", "provider_metrics"]
        if tvc == "feedback" or has_user_feedback:
            executed.append("user_model")
        if tvc == "trivial":
            # Optional light psyche interaction event only — not full psyche writeback.
            executed.append("psyche_light_event")
        for w in HEAVY_WRITEBACKS:
            if w not in executed:
                skipped.append(w)
                reasons[w] = f"policy_minimal|tvc={tvc}"
        for w in ("session_state",):
            if w not in executed:
                executed.append(w)
        return {
            "policy": "minimal",
            "turn_value_class": tvc,
            "executed": executed,
            "skipped": skipped,
            "skip_reasons": reasons,
            "replay_mode": False,
        }

    if policy == "standard":
        # Memory / psyche / learning allowed; no long-horizon / procedural extraction auto.
        base_skip = ["long_horizon"]
        if tvc not in ("procedural", "agentic", "goal_related"):
            base_skip.append("procedural_extraction")
        if tvc == "conversational" and not has_source_evidence:
            base_skip.extend(["knowledge", "success_patterns", "self_model"])
        executed = [w for w in HEAVY_WRITEBACKS if w not in base_skip] + list(
            MINIMAL_WRITEBACKS_ALLOWED
        )
        if tvc in ("feedback", "corrective", "informative") or has_user_feedback:
            if "user_model" not in executed:
                executed.append("user_model")
        skipped = list(base_skip)
        for w in skipped:
            reasons[w] = f"policy_standard|tvc={tvc}"
        if not has_tool_evidence:
            if "experience" in executed and tvc not in ("agentic", "research", "procedural"):
                # Keep experience allowed but mark reason if later skipped by pipeline.
                reasons.setdefault("experience", "requires_execution_evidence_when_claimed")
        return {
            "policy": "standard",
            "turn_value_class": tvc,
            "executed": executed,
            "skipped": skipped,
            "skip_reasons": reasons,
            "replay_mode": False,
        }

    # full
    executed = list(MINIMAL_WRITEBACKS_ALLOWED) + list(HEAVY_WRITEBACKS) + ["user_model"]
    if not has_source_evidence:
        reasons["knowledge"] = "knowledge_requires_provenance"
    if not has_tool_evidence and tvc == "agentic":
        reasons["experience"] = "experience_requires_execution_evidence"
    return {
        "policy": "full",
        "turn_value_class": tvc,
        "executed": executed,
        "skipped": [],
        "skip_reasons": reasons,
        "replay_mode": False,
        "success": success,
        "runtime_mode": runtime_mode,
    }


def is_trivial_meta_memory_content(content: str, *, query: str = "") -> bool:
    """True for identity/meta chatter that must not become durable semantic memory."""
    from aihub.strategy_selector import is_assistant_meta_ask, is_simple_greeting

    text = (content or "").strip()
    if not text:
        return False
    low = text.lower()
    q = (query or "").strip()
    if is_assistant_meta_ask(q) or is_simple_greeting(q):
        return True
    if is_assistant_meta_ask(text):
        return True
    junk_markers = (
        "memory-guided response",
        "działa. gotowy",
        "dziala. gotowy",
        "jestem gotowy",
        "kim jesteś",
        "kim jestes",
        "jak działasz",
        "jak dzialasz",
        "powiedz krótko, kim",
        "powiedz krotko, kim",
        "jestem mordzix",
        "asystent ai-hub",
        "wirtualny partner",
    )
    if any(m in low for m in junk_markers):
        return True
    # Echo of short identity answer without user fact markers
    if len(text.split()) <= 40 and any(
        m in low for m in ("mordzix", "ai-hub", "asystent ai", "model językowy", "model jezykowy")
    ) and not any(
        m in low
        for m in ("preferuj", "nazywa się", "nazywa sie", "mój pies", "moj pies", "lubię", "lubie")
    ):
        return True
    return False
