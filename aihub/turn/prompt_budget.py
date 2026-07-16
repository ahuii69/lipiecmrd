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

PROMPT_BUDGET_VERSION = "25.07.1"

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
    "casual_light": 320,
    "contextual": 2048,
    "research": 2048,
    "agentic": 4096,
}

PROFILE_HISTORY_MAX_MESSAGES: dict[BudgetProfile, int] = {
    "meta_light": 0,  # 2 only when prior-ref (applied by selector)
    "casual_light": 4,
    "contextual": 12,
    "research": 12,
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

_FEEDBACK_MARKERS = (
    "za rozwlekł",
    "za dług",
    "krócej",
    "krotcej",
    "bardziej zwięz",
    "bardziej zwiez",
    "preferuję",
    "preferuje",
    "nie lubię",
    "nie lubie",
    "od teraz",
    "przestań",
    "przestan",
    "zawsze odpowiadaj",
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
    "plan migracji",
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
        is_simple_greeting,
        meta_ask_refers_to_prior_conversation,
    )

    text = (user_text or "").strip()
    strat = (selected_strategy or "instant").strip().lower()
    web = (web_decision or "off").strip().lower()
    turn_mode = (mode or "chat").strip().lower()
    reasons: list[str] = []

    has_feedback = any(m in text.lower() for m in _FEEDBACK_MARKERS)
    provider_ask = any(m in text.lower() for m in _PROVIDER_ASK_MARKERS)
    meta = is_assistant_meta_ask(text)
    prior = meta and meta_ask_refers_to_prior_conversation(text)
    greeting = is_simple_greeting(text)

    low = text.lower()
    looks_agentic = any(k in low for k in _AGENTIC_MARKERS)
    looks_recall = any(k in low for k in _RECALL_MARKERS)

    # Explicit agent mode is never a lightweight chat envelope.
    if turn_mode in ("agent", "planner", "executive") or strat == "agentic" or (
        looks_agentic and not meta and not greeting
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

    if (strat in ("contextual",) or looks_recall) and not meta and not greeting:
        return _profile(
            "contextual",
            turn_value="informative",
            writeback="standard",
            reasons=["BUDGET_CONTEXTUAL"] + (["BUDGET_RECALL_CONTEXTUAL"] if looks_recall else []),
        )

    if meta and has_feedback:
        # Preference/feedback — not pure trivial; still light prompt, but allow user-model update.
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

    if greeting:
        return _profile(
            "casual_light",
            turn_value="trivial",
            writeback="minimal",
            reasons=["BUDGET_CASUAL_LIGHT"],
        )

    # Instant/direct may be a learning/psyche downgrade of a heavier turn — only
    # keep casual_light for short small-talk, otherwise prefer contextual.
    if strat in ("instant", "direct"):
        words = len(text.split())
        if words <= 6 and not looks_agentic and not looks_recall:
            return _profile(
                "casual_light",
                turn_value="conversational",
                writeback="standard",
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
        included = ["base_identity", "persona_light", "psyche_light"]
        skipped = ["memory_heavy", "knowledge", "learning", "tools", "web_policy", "critic"]
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
        allow_tools=name != "meta_light",
        allow_memory=name in ("contextual", "research", "agentic", "casual_light"),
        allow_knowledge=name in ("contextual", "research", "agentic"),
        allow_learning_influence=name in ("contextual", "research", "agentic"),
        allow_simulation=name in ("research", "agentic"),
        allow_critic_llm=name in ("research", "agentic", "contextual") and name != "meta_light",
        allow_response_variants=name in ("agentic",),
        reason_codes=list(reasons),
        layers_included=included,
        layers_skipped=skipped,
    )


def build_meta_light_system_prompt() -> str:
    return META_LIGHT_SYSTEM_PROMPT


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
    if budget is not None:
        if has_correction or any(m in (user_text or "").lower() for m in _FEEDBACK_MARKERS):
            return "feedback"
        return budget.turn_value_class
    if has_web:
        return "research"
    if has_tool_results:
        return "procedural"
    if has_correction:
        return "corrective"
    return "conversational"


def writebacks_for_policy(policy: WritebackPolicy) -> tuple[list[str], list[str]]:
    """Return (executed_allowed, skipped)."""
    if policy == "minimal":
        return list(MINIMAL_WRITEBACKS_ALLOWED), list(HEAVY_WRITEBACKS)
    if policy == "standard":
        skipped = ["procedural_extraction", "long_horizon"]
        executed = [w for w in HEAVY_WRITEBACKS if w not in skipped] + list(
            MINIMAL_WRITEBACKS_ALLOWED
        )
        return executed, skipped
    return list(MINIMAL_WRITEBACKS_ALLOWED) + list(HEAVY_WRITEBACKS), []


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
