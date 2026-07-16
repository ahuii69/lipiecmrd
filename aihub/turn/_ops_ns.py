"""Shared bindings for TurnOps mixins (imports + module state helpers)."""

from __future__ import annotations

# TurnOps stage helpers — imported by mixins; do not call from HTTP directly.
# ``first_turn_in_thread`` is True iff ``len(turn.history) == 0`` in ChatTurnInput.

import copy
import hashlib
import json
import logging
import re
import time
import uuid
from collections import Counter, defaultdict, deque
from typing import Any

from aihub.chat_context_compose import (
    augment_trace_context_truth,
    memory_results_count_for_trace,
    memory_truth_for_prompt,
    sanitize_user_message_for_llm,
    smart_clip_chat_history,
    web_grounding_in_prompt,
)
from aihub.chat_contracts import (
    BlockerVerdict,
    ChatMessage,
    ChatTurnContext,
    ChatTurnInput,
    ChatTurnResult,
    ModelResponse,
    ProviderUsage,
    ToolCallRequest,
    ToolCallResult,
)
from aihub.chat_decision_trace import (
    ROUTE_AGENT_HANDOFF_ERROR,
    ROUTE_BLOCKED_HARD,
    apply_provider_failure_response_trace_honesty,
    llm_path_verified_research_grounding,
    merge_canonical_decision_trace,
    merge_canonical_executive_handoff_success,
    merge_canonical_for_llm_path,
    merge_canonical_web_required_ungrounded,
    merge_provider_trace_from_builder,
    trace_blocker_gate_outcome,
)
from aihub.chat_file_service import (
    MAX_FILES_PER_TURN,
    build_attachment_prompt_block,
    fetch_recent_session_attachment_ids,
    summarize_attachments_for_user,
)
from aihub.chat_handoff_user_text import synthesize_chat_handoff_user_text
from aihub.chat_history_trace import build_history_trace
from aihub.chat_image_generation import is_image_generation_intent
from aihub.chat_product_policy import (
    clamp_ungrounded_speculative_reply,
    global_anti_hallucination_prompt_prefix,
    skip_experience_blocker_escalation,
)
from aihub.chat_stream_session import (
    emit_memory_used,
    emit_status,
    emit_tool_event,
    stream_session_active,
)
from aihub.config import (
    CHAT_DEFAULT_MODE,
    CHAT_MAX_TOOL_ITERATIONS,
    LLM_MODEL_NAME,
    LLM_STREAMING_ENABLED,
    LLM_TOOL_CALLING_ENABLED,
)
from aihub.db import append_event, get_experiences_by_user
from aihub.user_correction import (
    build_correction_hints_for_prompt,
    record_user_correction_turn,
)
from aihub.executive_controller import (
    build_agent_cycle_response,
    get_executive_controller,
    map_chat_execution_mode_to_force_strategy,
)
from aihub.llm import provider_registry as _provider_registry


def get_default_provider():
    """Resolve the default LLM provider at runtime.

    Prefer ``aihub.chat_runtime.get_default_provider`` when that module attribute
    differs from this function (thin wrapper or test monkeypatch). Otherwise use
    the registry hook so patches on ``provider_registry.get_default_provider`` apply.
    """
    try:
        import aihub.chat_runtime as _cr

        cr_fn = getattr(_cr, "get_default_provider", None)
        if cr_fn is not None and cr_fn is not get_default_provider:
            return cr_fn()
    except Exception:
        return _provider_registry.get_default_provider()
    return _provider_registry.get_default_provider()
from aihub.llm.provider_types import (
    ProviderChatRequest,
    ProviderError,
    ProviderToolSpec,
)
from aihub.memory_core import get_memory_core
from aihub.memory_engine import retrieve_context
from aihub.psyche_core import get_psyche_core
from aihub.response_persona_guard import (
    PERSONA_CONTRACT_PROMPT,
    dry_fallback_response,
    sanitize_persona_leakage,
    strip_reasoning_leak,
)
from aihub.response_variants_engine import ResponseVariantsEngine
from aihub.strategy_selector import (
    listing_copy_no_web_intent,
    short_followup_no_web_intent,
)
from aihub.tools.registry import get_tool_registry
from aihub.tools.router import ToolRouter
from aihub.tools.types import ToolExecutionContext

logger = logging.getLogger(__name__)

def _cr_hook(name: str, fallback):
    """Prefer monkeypatched symbol on aihub.chat_runtime when present."""
    import sys

    cr = sys.modules.get("aihub.chat_runtime")
    if cr is not None and hasattr(cr, name):
        return getattr(cr, name)
    return fallback



# Follow-up bez ponownego uploadu: gdy klient nie dołączy ID, a treść wskazuje na załącznik.
_SESSION_ATTACHMENT_DEICTIC_RE = re.compile(
    r"(?is)\b(ten|ta|to|tego|tej|tamten|tamta|tamto|poprzedni|ostatni|"
    r"w\s+tym|na\s+tym|o\s+tym|załącznik|obraz|zdjęci|dokument|plik|"
    r"dołączon|wgrany|wrzucon|upload)\b",
)

_TRACE_CACHE: dict[str, deque[dict[str, Any]]] = defaultdict(lambda: deque(maxlen=20))

# Słowa kluczowe zapytania → wymuszony web (świeże/ceny/aktualne/sport/news). Patrz _local_non_research_guardrails.
WEB_REQUIRED_QUERY_KEYWORDS: tuple[str, ...] = (
    "dziś",
    "dzis",
    "dzisiaj",
    "wczoraj",
    "jutro",
    "teraz",
    "obecnie",
    "aktualnie",
    "ostatnio",
    "najnowsze",
    "najświeższe",
    "ceny",
    "cena",
    "kurs",
    "kosztuje",
    "aktualne",
    "wynik",
    "mecz",
    "news",
    "newsy",
    "sprawdź",
    "sprawdz",
    "zbadaj",
)



def _is_audit_runtime(turn) -> bool:
    """Trusted audit skip — never based on user_id prefix."""
    return str(getattr(turn, "runtime_mode", "") or "").lower() == "audit"



# Populated by aihub.turn.ops after TurnOps class is defined (circular-safe hooks).
_TURN_OPS_TYPE = None  # set by ops.py
