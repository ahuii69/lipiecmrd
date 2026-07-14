#!/usr/bin/env python3

"""TurnOps — composition of stage mixins + turn orchestration."""

from __future__ import annotations

import json
from typing import Any

from aihub.chat_contracts import ChatTurnInput, ChatTurnResult
from aihub.turn._ops_ns import WEB_REQUIRED_QUERY_KEYWORDS  # noqa: F401
import aihub.turn._ops_ns as _ops_ns
from aihub.turn._ops_ns import (  # noqa: F401
    LLM_MODEL_NAME,
    ToolRouter,
    _TRACE_CACHE,
    _cr_hook,
    append_event,
    copy,
    emit_memory_used,
    emit_status,
    get_default_provider,
    get_memory_core,
    get_psyche_core,
    get_tool_registry,
    logger,
    memory_results_count_for_trace,
    memory_truth_for_prompt,
    record_user_correction_turn,
    stream_session_active,
    time,
    uuid,
    web_grounding_in_prompt,
    augment_trace_context_truth,
    build_history_trace,
    merge_canonical_for_llm_path,
    merge_canonical_decision_trace,
    trace_blocker_gate_outcome,
    ROUTE_BLOCKED_HARD,
    ROUTE_AGENT_HANDOFF_ERROR,
    merge_canonical_executive_handoff_success,
    merge_canonical_web_required_ungrounded,
    llm_path_verified_research_grounding,
    CHAT_MAX_TOOL_ITERATIONS,
    LLM_TOOL_CALLING_ENABLED,
    LLM_STREAMING_ENABLED,
    ToolCallRequest,
    ToolCallResult,
    ProviderUsage,
    ModelResponse,
    ChatMessage,
    ChatTurnContext,
    BlockerVerdict,
    ProviderError,
    ProviderChatRequest,
    ToolExecutionContext,
    sanitize_user_message_for_llm,
    smart_clip_chat_history,
    build_attachment_prompt_block,
    summarize_attachments_for_user,
    MAX_FILES_PER_TURN,
    fetch_recent_session_attachment_ids,
    clamp_ungrounded_speculative_reply,
    dry_fallback_response,
    ResponseVariantsEngine,
    synthesize_chat_handoff_user_text,
    build_agent_cycle_response,
    get_executive_controller,
    map_chat_execution_mode_to_force_strategy,
    is_image_generation_intent,
    listing_copy_no_web_intent,
    short_followup_no_web_intent,
)
# Ensure underscore helpers used by run_turn_core body are bound.
from aihub.turn.mixins import (
    DecisionMixin,
    PipelineMixin,
    ExecutionMixin,
    ExperienceMixin,
    PromptContextMixin,
    WebMixin,
)

class TurnOps(
    PipelineMixin,
    ExperienceMixin,
    DecisionMixin,
    WebMixin,
    PromptContextMixin,
    ExecutionMixin,
):
    def __init__(self) -> None:
        from aihub.llm.provider_registry import build_provider_execution_service, get_primary_provider

        self._tool_registry = get_tool_registry()
        self._tool_router = ToolRouter(self._tool_registry)
        from aihub.turn.tool_service import ToolExecutionService
        from aihub.turn.completion import TurnCompletionService

        primary = get_primary_provider()
        self._provider_service = build_provider_execution_service(primary=primary)
        self._tool_service = ToolExecutionService(self._tool_router)
        self._completion_service = TurnCompletionService()
        self._active_turn_ctx = None
        self._active_trace_builder = None
        # Managed hooks kept as attributes so runtime wiring is explicit and testable.
        self._memory_process_fn = get_memory_core().ingest_turn
        self._psyche_evolve_fn = get_psyche_core().evolve

    @property
    def _provider(self):
        """Primary LLM adapter — same object used by ProviderExecutionService."""
        return self._provider_service._primary

    @_provider.setter
    def _provider(self, value) -> None:
        self._provider_service._primary = value
        self._provider_service._provider = value

    async def run_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        res: ChatTurnResult | None = None
        err: BaseException | None = None
        try:
            res = await self.run_turn_core(turn)
            self._apply_persona_guard(turn, res)
            return res
        except BaseException as exc:
            err = exc
            raise
        finally:
            if str(getattr(turn, "runtime_mode", "") or "").lower() != "audit":
                try:
                    from aihub.chat_session_transcript import persist_chat_turn_messages

                    persist_chat_turn_messages(turn, res, err)
                except Exception:
                    logger.exception("chat session transcript persist failed")


    async def _run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
        """Backward-compatible alias for tests."""
        return await self.run_turn_core(turn)





# Late-bind TurnOps into mixin modules (staticmethod bodies reference TurnOps).
import aihub.turn.mixins.decision as _m_decision
import aihub.turn.mixins.pipeline as _m_pipeline
import aihub.turn.mixins.execution as _m_execution
import aihub.turn.mixins.experience as _m_experience
import aihub.turn.mixins.prompt_context as _m_prompt
import aihub.turn.mixins.web as _m_web
import aihub.turn.mixins.decision_pre_exec as _m_decision_pre_exec
import aihub.turn.mixins.decision_blocker as _m_decision_blocker
import aihub.turn.mixins.execution_handoff as _m_execution_handoff
import aihub.turn.mixins.prompt_system as _m_prompt_system

for _m in (_m_decision, _m_execution, _m_experience, _m_prompt, _m_web, _m_pipeline,
           _m_decision_pre_exec, _m_decision_blocker, _m_execution_handoff, _m_prompt_system):
    _m.TurnOps = TurnOps
TurnOps.__module__ = "aihub.turn.ops"

_RUNTIME: TurnOps | None = None


def get_turn_ops() -> TurnOps:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = TurnOps()
    else:
        try:
            from aihub.llm.provider_registry import build_provider_execution_service, get_primary_provider

            fresh_provider = get_primary_provider()
        except Exception:
            fresh_provider = None
        if fresh_provider is not None:
            current = getattr(_RUNTIME, "_provider", None)
            current_key = (type(current), getattr(current, "provider_name", None), getattr(current, "name", None))
            fresh_key = (type(fresh_provider), getattr(fresh_provider, "provider_name", None), getattr(fresh_provider, "name", None))
            if current is None or current_key != fresh_key:
                _RUNTIME._provider = fresh_provider
                _RUNTIME._provider_service = build_provider_execution_service(primary=fresh_provider)
    return _RUNTIME


def get_cached_chat_traces(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    traces = list(_TRACE_CACHE.get(user_id, []))
    return traces[-max(1, int(limit)) :]


def get_last_traces(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    return get_cached_chat_traces(user_id, limit=limit)