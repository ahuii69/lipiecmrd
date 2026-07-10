#!/usr/bin/env python3

"""Chat runtime orchestration: model <-> tools loop with canonical runtime delegation.

Źródło prawdy: pierwszy turn wątku vs kontynuacja
-----------------------------------------------
``first_turn_in_thread`` jest True wtedy i tylko wtedy, gdy ``len(turn.history) == 0``
w payloadzie ``ChatTurnInput`` (to samo żądanie, co obsługuje ``POST /chat/turn``).

* To jest **per wątek konwersacji wysłany przez klienta** (Cockpit: jedna sesja UI
  = jedna linia czatu; ``history`` zawiera poprzednie user/assistant z tej sesji).
* **Nie** jest to „pierwszy turn per user_id w całym systemie”: nowa sesja UI z tym
  samym ``user_id`` wysyła znowu pustą ``history`` → z punktu widzenia runtime
  znów „pierwszy turn w tym wątku” (krótkie przywitanie w promptach jest dopuszczalne).
* Pamięć długoterminowa (STM/LTM, Memory V2) jest powiązana z ``user_id``, więc
  kontekst użytkowy może wracać między sesjami; instrukcja „nie witaj ponownie”
  dotyczy **tego samego wątku** (niepusta historia), a nie globalnie konta.

Pola observability w ``trace`` (udane tury): ``chat_thread_first_turn``,
``chat_history_message_count``.
"""

from __future__ import annotations

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
    llm_path_verified_research_grounding,
    merge_canonical_decision_trace,
    merge_canonical_executive_handoff_success,
    merge_canonical_for_llm_path,
    merge_canonical_web_required_ungrounded,
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

    Kept as a module-level hook so older tests/integrations can monkeypatch
    ``aihub.chat_runtime.get_default_provider``, while newer code can patch
    ``aihub.llm.provider_registry.get_default_provider`` and still affect fresh
    ChatRuntime instances.
    """
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


class ChatRuntime:
    """Dedicated runtime that orchestrates provider calls and capability execution."""

    def __init__(self) -> None:
        self._provider = get_default_provider()
        self._tool_registry = get_tool_registry()
        self._tool_router = ToolRouter(self._tool_registry)
        # Managed hooks kept as attributes so runtime wiring is explicit and testable.
        self._memory_process_fn = get_memory_core().ingest_turn
        self._psyche_evolve_fn = get_psyche_core().evolve

    def _current_provider_name(self) -> str:
        return str(
            getattr(
                self._provider,
                "provider_name",
                getattr(self._provider, "name", "mock"),
            )
        )

    @staticmethod
    def _safe_preview(obj: Any, max_chars: int = 600) -> str:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(obj)
        if len(text) > max_chars:
            return text[:max_chars] + " ...[TRUNCATED]"
        return text

    @staticmethod
    def _tokenize_for_similarity(text: str) -> set[str]:
        """Tokenize text for lightweight heuristic similarity matching."""
        raw = re.findall(r"[\wąćęłńóśźż]{3,}", (text or "").lower())
        stop = {
            "oraz",
            "który",
            "która",
            "które",
            "których",
            "przez",
            "jest",
            "było",
            "będzie",
            "tego",
            "that",
            "this",
            "with",
            "from",
            "have",
            "will",
            "into",
            "without",
        }
        return {t for t in raw if t not in stop}

    def _lookup_experience_signal(
        self,
        *,
        user_id: str,
        message: str,
        selected_strategy: str,
    ) -> dict[str, Any]:
        """Load, rank and aggregate user experiences into execution-driving signal."""
        base_signal: dict[str, Any] = {
            "lookup_happened": False,
            "matches_count": 0,
            "experience_signal_summary": "no_lookup",
            "recommended_strategy": None,
            "confidence_adjustment": None,
            "handoff_bias": None,
            "blocker_reason": None,
            "blocker_severity": None,
            "recurring_failure_detected": False,
            "caution_hints": [],
            "dominant_strategy_success": None,
            "dominant_strategy_failure": None,
            "recurring_failure_types": [],
            "action_bias": {},
        }

        try:
            recent = get_experiences_by_user(user_id, limit=120)
            base_signal["lookup_happened"] = True
        except Exception:  # noqa: BLE001
            logger.debug("Experience lookup failed for user=%s", user_id, exc_info=True)
            base_signal["experience_signal_summary"] = "lookup_failed"
            return base_signal

        if not recent:
            base_signal["experience_signal_summary"] = "no_history"
            return base_signal

        query_tokens = self._tokenize_for_similarity(message or "")
        ranked: list[tuple[float, dict[str, Any]]] = []
        now = time.time()

        for exp in recent:
            summary = str(exp.get("user_input_summary") or "")
            lesson = str(exp.get("short_lesson_learned") or "")
            seed = str(exp.get("reflection_seed") or "")
            failure_type = str(exp.get("failure_type") or "")
            reason_blob = " ".join(str(rc) for rc in (exp.get("reason_codes") or []))
            text_blob = (
                f"{summary} {lesson} {seed} {failure_type} {reason_blob}".strip()
            )

            candidate_tokens = self._tokenize_for_similarity(text_blob)
            overlap = 0.0
            if query_tokens and candidate_tokens:
                overlap = len(query_tokens & candidate_tokens) / max(
                    1, len(query_tokens)
                )

            created_at = float(exp.get("created_at") or 0.0)
            age_days = max(0.0, (now - created_at) / 86400.0) if created_at else 999.0
            recency = max(0.0, 1.0 - min(age_days, 45.0) / 45.0)

            strat_bonus = (
                0.12
                if str(exp.get("selected_strategy") or "") == selected_strategy
                else 0.0
            )
            quality_bonus = 0.06 if bool(exp.get("success", False)) else -0.03

            score = overlap * 0.62 + recency * 0.20 + strat_bonus + quality_bonus
            if score >= 0.18 and (overlap >= 0.10 or strat_bonus > 0):
                ranked.append((score, exp))

        ranked.sort(key=lambda item: item[0], reverse=True)
        ranked = ranked[:16]

        if not ranked:
            base_signal["experience_signal_summary"] = "no_similar_matches"
            return base_signal

        base_signal["matches_count"] = len(ranked)

        by_strategy: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "weight": 0.0,
                "success": 0.0,
                "failure": 0.0,
                "fallback": 0.0,
                "degraded": 0.0,
                "unmet_tools": 0.0,
                "unmet_research": 0.0,
            }
        )
        failure_counter: Counter[str] = Counter()

        total_weight = 0.0
        weighted_success = 0.0
        weighted_failure = 0.0
        handoff_weight = 0.0
        handoff_score = 0.0
        unmet_tool_need_weight = 0.0

        for score, exp in ranked:
            w = max(0.05, float(score))
            total_weight += w

            strategy = str(exp.get("selected_strategy") or "instant")
            strat_stats = by_strategy[strategy]
            strat_stats["weight"] += w

            success = bool(exp.get("success", False))
            if success:
                weighted_success += w
                strat_stats["success"] += w
            else:
                weighted_failure += w
                strat_stats["failure"] += w

            fallback_flag = bool(exp.get("fallback_flag", False))
            degraded_flag = bool(exp.get("degraded_flag", False))
            tools_needed = bool(exp.get("tools_needed", False))
            tools_executed = bool(exp.get("tools_executed", False))
            research_needed = bool(exp.get("research_needed", False))
            research_executed = bool(exp.get("research_executed", False))
            planner_executed = bool(exp.get("planner_executed", False))
            agentic_executed = bool(exp.get("agentic_executed", False))

            if fallback_flag:
                strat_stats["fallback"] += w
            if degraded_flag:
                strat_stats["degraded"] += w
            if tools_needed and not tools_executed:
                strat_stats["unmet_tools"] += w
                unmet_tool_need_weight += w
            if research_needed and not research_executed:
                strat_stats["unmet_research"] += w

            failure_type = str(exp.get("failure_type") or "").strip().lower()
            if not success and failure_type:
                failure_counter[failure_type] += 1

            if planner_executed or agentic_executed:
                handoff_weight += w
                handoff_score += w if success else -w

        if total_weight <= 0:
            base_signal["experience_signal_summary"] = "matches_without_weight"
            return base_signal

        success_rate = weighted_success / total_weight
        failure_rate = weighted_failure / total_weight

        confidence_adjust = (success_rate - failure_rate) * 0.18
        confidence_adjust -= (unmet_tool_need_weight / total_weight) * 0.08
        confidence_adjust = max(-0.24, min(0.18, confidence_adjust))

        handoff_bias: float | None = None
        if handoff_weight > 0:
            handoff_bias = max(
                -0.55, min(0.55, (handoff_score / handoff_weight) * 0.40)
            )
        elif unmet_tool_need_weight > 0:
            handoff_bias = max(
                0.0, min(0.35, (unmet_tool_need_weight / total_weight) * 0.35)
            )

        dominant_success: tuple[str, float] | None = None
        dominant_failure: tuple[str, float] | None = None
        for strategy, stats in by_strategy.items():
            sw = max(1e-6, stats["weight"])
            succ_rate = stats["success"] / sw
            fail_rate = stats["failure"] / sw
            if stats["weight"] >= 0.6 and succ_rate >= 0.62:
                if dominant_success is None or succ_rate > dominant_success[1]:
                    dominant_success = (strategy, succ_rate)
            if stats["weight"] >= 0.6 and fail_rate >= 0.58:
                if dominant_failure is None or fail_rate > dominant_failure[1]:
                    dominant_failure = (strategy, fail_rate)

        recommended_strategy: str | None = None
        selected_stats = by_strategy.get(selected_strategy)
        selected_fail_rate = 0.0
        if selected_stats and selected_stats["weight"] > 0:
            selected_fail_rate = selected_stats["failure"] / selected_stats["weight"]

        if (
            dominant_success
            and dominant_success[0] != selected_strategy
            and selected_fail_rate >= 0.45
        ):
            recommended_strategy = dominant_success[0]

        recurring_failure_types = [k for k, v in failure_counter.items() if v >= 2]
        blocker_reason: str | None = None
        blocker_severity: float | None = None
        caution_hints: list[str] = []
        if recurring_failure_types:
            top_failure = recurring_failure_types[0]
            blocker_reason = (
                f"Powtarzalne porażki typu '{top_failure}' w podobnych turach"
            )
            blocker_severity = 0.72
            caution_hints.append(f"recurring_failure:{top_failure}")
        elif (
            selected_fail_rate >= 0.65
            and selected_stats
            and selected_stats["weight"] >= 1.0
        ):
            blocker_reason = (
                f"Wysoki wskaźnik porażek dla strategii {selected_strategy} "
                f"({selected_fail_rate:.0%})"
            )
            blocker_severity = 0.62
            caution_hints.append("high_strategy_failure_rate")

        if skip_experience_blocker_escalation(message):
            recurring_failure_types = []
            blocker_reason = None
            blocker_severity = None
            caution_hints = []

        strategy_to_action = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        action_bias: dict[str, dict[str, float]] = {}
        for strategy, stats in by_strategy.items():
            sw = max(1e-6, stats["weight"])
            succ_rate = stats["success"] / sw
            fail_rate = stats["failure"] / sw
            delta = max(-0.18, min(0.18, (succ_rate - fail_rate) * 0.22))
            action = strategy_to_action.get(strategy)
            if not action:
                continue
            action_bias[action] = {
                "confidence_delta": round(delta, 3),
                "risk_delta": round(-delta * 0.65, 3),
                "utility_delta": round(delta * 0.40, 3),
            }

        summary = (
            f"matches={len(ranked)} succ={success_rate:.2f} fail={failure_rate:.2f} "
            f"conf_adj={confidence_adjust:+.2f}"
        )

        # ── Deliberation history extraction (execution-driving read-path) ──
        # Extract deliberation outcomes from past experiences to bias future
        # deliberation trigger decisions and variant evaluation.
        deliberation_history = self._extract_deliberation_history(ranked)

        base_signal.update(
            {
                "experience_signal_summary": summary,
                "recommended_strategy": recommended_strategy,
                "confidence_adjustment": round(confidence_adjust, 3),
                "handoff_bias": (
                    round(handoff_bias, 3) if handoff_bias is not None else None
                ),
                "blocker_reason": blocker_reason,
                "blocker_severity": blocker_severity,
                "recurring_failure_detected": bool(recurring_failure_types),
                "caution_hints": caution_hints,
                "dominant_strategy_success": (
                    dominant_success[0] if dominant_success else None
                ),
                "dominant_strategy_failure": (
                    dominant_failure[0] if dominant_failure else None
                ),
                "recurring_failure_types": recurring_failure_types,
                "action_bias": action_bias,
                # Deliberation history — feeds trigger logic and evaluation
                "deliberation_history": deliberation_history,
            }
        )
        return base_signal

    @staticmethod
    def _extract_deliberation_history(
        ranked: list[tuple[float, dict[str, Any]]],
    ) -> dict[str, Any]:
        """Extract deliberation outcome patterns from past experiences.

        Produces execution-driving signal consumed by:
          - should_run_variants(): deliberation_trigger_bias
          - evaluate_candidates(): variant_type preference weights
          - synthesis: quality expectation baseline

        Returns:
          deliberation_count           – how many past matches had deliberation
          deliberation_success_rate    – weighted success rate of deliberated turns
          avg_quality_score            – average deliberation_outcome_quality.quality_score
          avg_confidence_gain          – average confidence_gain from deliberation
          dominant_winner_type         – most common winner variant_type
          winner_type_distribution     – {direct: N, contextual: N, actionable: N}
          deliberation_trigger_bias    – float: + favour trigger, - suppress trigger
          variant_preference_weights   – {type: weight} bias for evaluation (0.8–1.2)
          should_suppress_deliberation – bool: true if deliberation was consistently unhelpful
          should_force_deliberation    – bool: true if deliberation was consistently helpful
        """
        result: dict[str, Any] = {
            "deliberation_count": 0,
            "deliberation_success_rate": 0.0,
            "avg_quality_score": 0.0,
            "avg_confidence_gain": 0.0,
            "dominant_winner_type": None,
            "winner_type_distribution": {},
            "deliberation_trigger_bias": 0.0,
            "variant_preference_weights": {},
            "should_suppress_deliberation": False,
            "should_force_deliberation": False,
        }

        deliberated_exps: list[tuple[float, dict[str, Any]]] = []
        for score, exp in ranked:
            if exp.get("response_variants_triggered"):
                deliberated_exps.append((score, exp))

        if not deliberated_exps:
            return result

        result["deliberation_count"] = len(deliberated_exps)

        total_weight = 0.0
        weighted_success = 0.0
        quality_scores: list[float] = []
        confidence_gains: list[float] = []
        winner_types: Counter[str] = Counter()
        should_have_count = 0
        should_not_have_count = 0

        for score, exp in deliberated_exps:
            w = max(0.05, float(score))
            total_weight += w

            success = bool(exp.get("success", False))
            if success:
                weighted_success += w

            # Extract outcome quality if stored
            oq = exp.get("deliberation_outcome_quality")
            if isinstance(oq, dict):
                qs = float(oq.get("quality_score") or 0.0)
                quality_scores.append(qs)
                cg = float(oq.get("confidence_gain") or 0.0)
                confidence_gains.append(cg)
                if oq.get("should_have_deliberated"):
                    should_have_count += 1
                else:
                    should_not_have_count += 1
            else:
                # Fallback: use raw confidence as quality proxy
                conf = float(exp.get("response_variants_confidence") or 0.5)
                quality_scores.append(conf)

            winner = exp.get("response_variants_winner_type")
            if winner:
                winner_types[winner] += 1

        if total_weight > 0:
            result["deliberation_success_rate"] = round(
                weighted_success / total_weight, 3
            )

        if quality_scores:
            result["avg_quality_score"] = round(
                sum(quality_scores) / len(quality_scores), 4
            )
        if confidence_gains:
            result["avg_confidence_gain"] = round(
                sum(confidence_gains) / len(confidence_gains), 4
            )

        if winner_types:
            result["dominant_winner_type"] = winner_types.most_common(1)[0][0]
            result["winner_type_distribution"] = dict(winner_types)

        # ── Trigger bias: should we trigger more or less often? ──
        # Positive → favour triggering, Negative → suppress triggering
        trigger_bias = 0.0
        if should_have_count + should_not_have_count >= 2:
            ratio = should_have_count / (should_have_count + should_not_have_count)
            trigger_bias = round((ratio - 0.5) * 0.40, 4)  # [-0.20, +0.20]
        elif quality_scores:
            avg_q = sum(quality_scores) / len(quality_scores)
            trigger_bias = round((avg_q - 0.50) * 0.30, 4)  # [-0.15, +0.15]
        result["deliberation_trigger_bias"] = max(-0.20, min(0.20, trigger_bias))

        # ── Variant preference weights (bias evaluation scores) ──
        # Winner types that historically performed well get a slight boost
        variant_weights: dict[str, float] = {}
        total_wins = sum(winner_types.values()) or 1
        for vtype in ("direct", "contextual", "actionable"):
            win_count = winner_types.get(vtype, 0)
            # Base weight 1.0, adjust ±0.2 based on historical win rate
            win_rate = win_count / total_wins
            variant_weights[vtype] = round(
                max(0.80, min(1.20, 1.0 + (win_rate - 0.33) * 0.60)),
                3,
            )
        result["variant_preference_weights"] = variant_weights

        # ── Suppress/force deliberation ──
        if len(deliberated_exps) >= 3:
            if (
                result["deliberation_success_rate"] < 0.30
                and result["avg_quality_score"] < 0.40
            ):
                result["should_suppress_deliberation"] = True
            elif (
                result["deliberation_success_rate"] >= 0.75
                and result["avg_quality_score"] >= 0.60
            ):
                result["should_force_deliberation"] = True

        return result

    def _build_memory_brief(
        self,
        memory_context: dict[str, Any],
        *,
        include_stm: bool = True,
    ) -> str:
        if not isinstance(memory_context, dict):
            return "BRAK DANYCH"

        stm = memory_context.get("stm") or []
        episodic = memory_context.get("episodic") or []
        semantic = memory_context.get("semantic") or []
        dense = memory_context.get("dense_hits") or []
        graph = memory_context.get("graph_hits") or []
        total = int(memory_context.get("total") or 0)

        if (
            total <= 0
            and not episodic
            and not semantic
            and not dense
            and not graph
            and not (include_stm and stm)
        ):
            return "Brak trafień pamięci dla tej wiadomości."

        stm_lines: list[str] = []
        if include_stm:
            for item in stm[-10:]:
                if isinstance(item, dict):
                    role = str(item.get("role") or "")
                    body = str(item.get("content") or "")
                    stm_lines.append(f"- [{role}] {body[:220]}")

        epi_lines = []
        for item in episodic[:2]:
            if isinstance(item, dict):
                epi_lines.append(f"- {str(item.get('content', ''))[:180]}")

        sem_lines = []
        for item in semantic[:4]:
            if isinstance(item, dict):
                sem_lines.append(f"- {str(item.get('content', ''))[:180]}")

        dense_lines = []
        for item in dense[:3]:
            if isinstance(item, dict):
                dense_lines.append(f"- {str(item.get('text', ''))[:160]}")

        if include_stm:
            stm_block = (
                "STM (ostatnia sesja, chronologicznie — najniższy priorytet faktów):\n"
                f"{chr(10).join(stm_lines) if stm_lines else '- brak'}\n"
            )
        else:
            stm_block = (
                "STM: pominięty w skrócie — bieżąca sesja jest w historii wiadomości; "
                "poniżej tylko LTM / retrieval.\n"
            )

        # Priorytet odczytu dla modelu: L2 (fakty) → wektor → epizody → STM na końcu.
        return (
            f"total={total}; stm={len(stm)}; episodic={len(episodic)}; semantic={len(semantic)}; "
            f"dense={len(dense)}; graph={len(graph)}\n"
            f"PRIORYTET: najpierw FAKTY (L2), potem VECTOR, potem EPISODIC; nie używaj epizodu jeśli "
            f"jest trafienie L2 na to samo pytanie.\n"
            f"Semantic (L2 fakty) top:\n{chr(10).join(sem_lines) if sem_lines else '- brak'}\n"
            f"Dense (vector) top:\n{chr(10).join(dense_lines) if dense_lines else '- brak'}\n"
            f"Episodic (L1) top:\n{chr(10).join(epi_lines) if epi_lines else '- brak'}\n"
            f"{stm_block}"
        )

    @staticmethod
    def _build_memory_used_trace(
        memory_context: dict[str, Any],
        *,
        include_stm: bool = True,
    ) -> list[dict[str, Any]]:
        """Observability: snapshot tego, co faktycznie poszło do promptu (STM opcjonalnie)."""
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _sha1_text(s: str) -> str:
            return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()

        def _add(
            source: str,
            mid: str,
            text: str,
            extra: dict[str, Any] | None = None,
        ) -> None:
            t = (text or "").strip()
            if not t:
                return
            key = (source, mid)
            if key in seen:
                return
            seen.add(key)
            row: dict[str, Any] = {
                "id": mid,
                "text": t[:2000],
                "source": source,
                "used": True,
            }
            if extra:
                row.update(extra)
            out.append(row)

        if not isinstance(memory_context, dict):
            return out

        for m in (memory_context.get("stm") or [])[:15] if include_stm else []:
            if not isinstance(m, dict):
                continue
            raw_id = str(m.get("id") or "").strip()
            content = str(m.get("content") or "")
            mid = raw_id or _sha1_text(content)
            role = str(m.get("role") or "")
            _add("stm", mid, f"[{role}] {content}" if role else content)

        for m in (memory_context.get("semantic") or [])[:12]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("id") or "").strip() or _sha1_text(content)
            _add("L2", mid, content)

        for m in (memory_context.get("dense_hits") or [])[:8]:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "")
            _add("vector", _sha1_text(text), text)

        for m in (memory_context.get("episodic") or [])[:12]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("id") or "").strip() or _sha1_text(content)
            _add("L1", mid, content)

        for m in (memory_context.get("graph_hits") or [])[:12]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("node_id") or "").strip() or _sha1_text(content)
            _add("graph", mid, content)

        for m in (memory_context.get("memory_v2_items") or [])[:20]:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            content = str(m.get("content") or "")
            combined = f"{title}: {content}".strip(": ").strip() if title else content
            mid = str(m.get("id") or "").strip() or _sha1_text(f"{title}|{content}")
            extra_v2: dict[str, Any] = {}
            for fk in ("is_suppressed", "is_pinned", "is_archived"):
                if fk in m:
                    extra_v2[fk] = bool(m.get(fk))
            _add("memory_v2", mid, combined, extra_v2 if extra_v2 else None)

        return out

    @staticmethod
    def _augment_memory_observability(
        trace: dict[str, Any],
        memory_used_trace: list[dict[str, Any]] | None,
        memory_context: dict[str, Any] | None = None,
    ) -> None:
        used = list(memory_used_trace or [])
        trace["memory_used_bool"] = bool(used)
        trace["memory_hits"] = len(used)
        sources: list[str] = []
        for row in used:
            src = str(row.get("source") or "").strip()
            if src and src not in sources:
                sources.append(src)
        trace["memory_source"] = sources
        mc = memory_context if isinstance(memory_context, dict) else None
        if mc:
            errs = mc.get("memory_read_errors")
            if isinstance(errs, list) and errs:
                trace["memory_read_errors"] = list(errs)
            ro = mc.get("retrieval_priority_order")
            if isinstance(ro, list) and ro:
                trace["memory_retrieval_priority_order"] = list(ro)
            pack = mc.get("context_pack")
            if isinstance(pack, dict):
                trace["memory_context_pack_selected_ids"] = list(pack.get("selected_ids") or [])
                trace["memory_context_pack_used_chars"] = int(pack.get("used_chars") or 0)
                trace["memory_context_pack_source_distribution"] = dict(pack.get("source_distribution") or {})
                trace["memory_context_pack_injected"] = bool(pack.get("selected_ids"))

    @staticmethod
    def _correction_trace_flat(
        corr: dict[str, Any] | None, *, hints_chars: int = 0
    ) -> dict[str, Any]:
        t = corr if isinstance(corr, dict) else {}
        return {
            "user_correction_recorded": bool(t.get("recorded")),
            "user_correction_kind": t.get("kind"),
            "user_correction_durable_marked": bool(t.get("durable")),
            "correction_hints_in_prompt_chars": int(hints_chars),
        }

    @staticmethod
    def _correction_trace_fields(ctx: ChatTurnContext | None) -> dict[str, Any]:
        if not ctx:
            return {}
        ct = ctx.system_context.get("correction_turn_trace")
        if not isinstance(ct, dict):
            ct = {}
        hints = str(ctx.system_context.get("correction_hints_text") or "")
        return ChatRuntime._correction_trace_flat(ct, hints_chars=len(hints))

    @staticmethod
    def _effective_attached_file_ids(turn: ChatTurnInput) -> list[str]:
        """ID załączników z żądania lub (fallback) ostatnie pliki sesji przy odwołaniach wskazujących."""
        raw = [
            str(x).strip()
            for x in (turn.attached_file_ids or [])
            if str(x).strip()
        ][:MAX_FILES_PER_TURN]
        if raw:
            return raw
        msg = (turn.message or "").strip()
        if not msg or _SESSION_ATTACHMENT_DEICTIC_RE.search(msg) is None:
            return []
        return fetch_recent_session_attachment_ids(
            user_id=turn.user_id,
            session_id=turn.session_id,
            limit=MAX_FILES_PER_TURN,
        )

    def _build_psyche_brief(self, psyche_state: dict[str, Any]) -> str:
        compact = self._compact_psyche_state(psyche_state)
        if not compact:
            return "BRAK DANYCH"

        mood = compact.get("mood")
        energy = compact.get("energy")
        focus = compact.get("focus")
        style = compact.get("style")
        traits = compact.get("traits") or {}

        directness = traits.get("directness", "BRAK DANYCH")

        # NOTE (06.07 response-quality fix): we intentionally no longer inject raw sarcasm/swearing
        # trait values into the prompt. Surfacing them pushed the model toward theatrical, personified
        # replies. Psyche now only hints at *tone modulation* (directness / brevity / warmth); it must
        # never justify fake biography, aggression, or mirroring the user's hostile tone.
        return (
            f"style={style}, mood={mood}, energy={energy}, focus={focus}, directness={directness}. "
            "Traktuj to wyłącznie jako subtelną modulację tonu (bezpośredniość, ciepło, zwięzłość) — "
            "nie zmieniaj przez to faktów, nie personifikuj się i nie kopiuj agresywnego tonu użytkownika."
        )

    def _build_system_prompt(
        self,
        ctx: ChatTurnContext,
        *,
        memory_brief: str,
        psyche_brief: str,
        decision_hints: str = "",
        correction_hints: str = "",
        memory_v2_context=None,
        psyche_v2_context=None,
        files_context: str = "",
        first_turn_in_thread: bool,
        history_rollup: str | None = None,
        listing_sales_boost: bool = False,
    ) -> str:
        caps = [f"- {c.name}: {c.description}" for c in ctx.capabilities]
        capabilities_text = "\n".join(caps) if caps else "- brak dostępnych narzędzi"

        # Behavioral instructions from Psyche V2
        behavior_instructions = ""
        if psyche_v2_context and psyche_v2_context.loaded:
            style_parts = []

            # Directness
            if psyche_v2_context.directness_bias > 0.7:
                style_parts.append(
                    "Możesz być bardziej bezpośredni i konkretny, ale tylko o tyle, o ile nie psuje to zadania."
                )
            elif psyche_v2_context.directness_bias < 0.3:
                style_parts.append(
                    "Możesz lekko zwiększyć ostrożność i niuans, bez rozwlekania odpowiedzi."
                )

            # Verbosity
            if psyche_v2_context.verbosity_bias < 0.3:
                style_parts.append("Trzymaj odpowiedzi zwięźle — bez lania wody.")
            elif psyche_v2_context.verbosity_bias > 0.7:
                style_parts.append(
                    "Możesz rozwinąć więcej szczegółów tam, gdzie naprawdę pomagają."
                )

            # Caution
            if psyche_v2_context.caution_bias > 0.7 or psyche_v2_context.pressure > 0.6:
                style_parts.append(
                    "Wysoka ostrożność: weryfikuj starannie, zaznaczaj niepewności, unikaj zbyt pewnych twierdzeń."
                )

            # Friction
            if psyche_v2_context.friction > 0.5:
                style_parts.append(
                    "Przy napięciu relacyjnym zwiększ precyzję i ogranicz luźne interpretacje."
                )

            # Warmth
            if psyche_v2_context.warmth > 0.7 and psyche_v2_context.trust > 0.6:
                style_parts.append(
                    "Przy wysokim trust możesz być bardziej naturalny, ale nadal trzymaj się celu użytkownika."
                )

            # Autonomy
            if psyche_v2_context.autonomy_bias > 0.7:
                style_parts.append(
                    "Przy oczywistych i niskiego ryzyka rzeczach możesz działać samodzielnie, ale nie zgaduj brakujących faktów."
                )
            elif psyche_v2_context.autonomy_bias < 0.3:
                style_parts.append(
                    "Przy większych decyzjach bądź ostrożniejszy — doprecyzuj, zanim polecisz coś ryzykownego."
                )

            # Structure
            if (
                psyche_v2_context.structuredness_bias > 0.7
                or psyche_v2_context.pressure > 0.5
            ):
                style_parts.append(
                    "Odpowiedź uporządkowana i strukturalna — punkty, kroki, jasna struktura."
                )

            if style_parts:
                behavior_instructions = (
                    "\n\nAKTYWNE WSKAZÓWKI BEHAWIORALNE (Psyche V2):\n"
                    + "\n".join(f"• {part}" for part in style_parts)
                )

        # Memory context injection
        memory_context_injection = ""
        if memory_v2_context and memory_v2_context.loaded:
            ctx_parts = []

            if memory_v2_context.top_facts:
                facts_text = "; ".join(
                    [f"{f['title']}" for f in memory_v2_context.top_facts[:3]]
                )
                ctx_parts.append(f"Fakty: {facts_text}")

            if memory_v2_context.top_preferences:
                prefs_text = "; ".join(
                    [f"{p['title']}" for p in memory_v2_context.top_preferences[:3]]
                )
                ctx_parts.append(f"Preferencje: {prefs_text}")

            proc_floor = 0.58
            evs = [
                int(p.get("evidence_count") or 0)
                for p in memory_v2_context.top_procedures
            ]
            if evs and max(evs) < 3:
                proc_floor = 0.66
            if (
                memory_v2_context.top_procedures
                and memory_v2_context.confidence_modifier > proc_floor
            ):
                procs_text = "; ".join(
                    [
                        f"{p['name']} (conf={p['confidence']:.2f}, n={p.get('evidence_count', 0)})"
                        for p in memory_v2_context.top_procedures[:2]
                    ]
                )
                ctx_parts.append(f"Procedury: {procs_text}")

            if memory_v2_context.contradiction_alerts:
                ctx_parts.append(
                    f"UWAGA SPRZECZNOŚCI: {'; '.join(memory_v2_context.contradiction_alerts)}"
                )

            if memory_v2_context.autobiographical_summary:
                ctx_parts.append(
                    f"Autobiografia: {memory_v2_context.autobiographical_summary[:150]}"
                )

            if ctx_parts:
                memory_context_injection = (
                    "\n\nKONTEKST PAMIĘCI (Memory V2 — wzbogacony):\n"
                    + "\n".join(f"• {part}" for part in ctx_parts)
                )

        if first_turn_in_thread:
            thread_continuity = (
                "Stan rozmowy: pierwsza odpowiedź w tym wątku (brak wcześniejszych wiadomości "
                "w historii tej sesji). Krótkie, naturalne przywitanie jest OK; unikaj sztywnej "
                "infolinii i tonu „jak mogę Ci dzisiaj pomóc”.\n\n"
            )
        else:
            thread_continuity = (
                "Stan rozmowy: kontynuacja — w historii żądania są już wcześniejsze wiadomości z tej sesji. "
                "Nie otwieraj od nowego przywitania ani resetu tonu; kontynuuj rzeczowo i nawiązuj do "
                "wcześniejszych tur, zamiast zaczynać jak świeży ticket w supportcie "
                "albo „proszę czekać, sprawdzam”.\n\n"
            )

        # Kolejność warstw: GLOBAL (anty-halucynacja) → system → product → execution rules → …
        global_anti_hallucination_layer = global_anti_hallucination_prompt_prefix()
        # Kolejność warstw: system → product → execution rules → psyche/style →
        # memory (LTM/V2 skrót) → web policy → capabilities → hints/pliki (poniżej).
        attachment_rules = ""
        if files_context:
            attachment_rules = (
                "ZAŁĄCZNIKI — reguła twarda:\n"
                "- W systemie występuje sekcja ATTACHMENTS_CONTEXT: to jedyne źródło faktów "
                "o dołączonych plikach i obrazach.\n"
                "- Gdy użytkownik pisze „plik”, „załącznik”, „co dołączyłem” — bazuj na tej sekcji, "
                "nie na domysłach.\n"
                "- Gdy odczyt się nie udał albo brak vision dla obrazu — powiedz to wprost, bez "
                "wymyślania treści.\n\n"
            )
        system_rules = (
            attachment_rules
            + "Jesteś Mordzix — rzeczowy asystent AI rozmawiający po polsku. Ton: naturalny, "
            "zwięzły, lekko swobodny, z dozwolonym suchym humorem — ale bez pajacowania, bez ściemy "
            "i bez udawania człowieka. Priorytet to konkret i użyteczność, nie budowanie „charakteru”.\n\n"
            f"{thread_continuity}"
            "Styl i ton:\n"
            "- Odpowiadaj merytorycznie i na temat; luz nigdy nie zastępuje treści.\n"
            "- Unikaj korpo-fraz („w czym mogę pomóc”, „chętnie pomogę”, „oczywiście, jako asystent…”, "
            "„jestem tutaj, aby…”), ale też bez sztucznej luzackiej maski.\n"
            "- Humor i ironia: rzadko i tylko gdy realnie pasują; nigdy kosztem jasności ani zamiast odpowiedzi.\n"
            "- Wolno krótko zasygnalizować sceptycyzm, gdy pytanie jest naciągane albo sprzeczne — "
            "bez obrazy osoby, bez złośliwości i bez ataku w rewanżu.\n"
            "- Przekleństwa: domyślnie tak. Dopuszczalne tylko, jeśli użytkownik wyraźnie nadaje taki "
            "ton i to naturalnie pasuje — nigdy jako styl każdego zdania i nigdy jako odbicie agresji.\n"
            "- Technikalia (kod, liczby, procedury): precyzyjnie i konkretnie; luz nie znaczy niedbale.\n"
            + PERSONA_CONTRACT_PROMPT
        )
        product_rules = (
            "\nProdukt — sekrety i granice:\n"
            "- Nie odmawiaj tylko dlatego, że w treści są słowa typu hasło, kod, token czy sekret.\n"
            "- Zapis/odczyt/usunięcie sekretu: wyłącznie vault (deterministyczna ścieżka) — bez pouczania "
            "i bez frazy w stylu „nie mogę przechowywać haseł”.\n"
            "- Pytania o wcześniejsze wiadomości w tej sesji: tylko z historii w żądaniu; nie zgaduj.\n"
            "- Zwykłe prośby i pamięć: obsłuż konkretnie, bez automatycznych odmów.\n"
            "- Grafika / obrazy / prompty do DALL·E, Stable Diffusion, Midjourney: ZAWSZE podaj gotowy prompt "
            "(najlepiej EN w bloku kodu) + krótki opis PL + opcjonalny negative prompt. "
            "NIGDY nie używaj ogólnych odmów w stylu „nie mogę pomóc”, „nie mogę tego zrobić”, "
            "„to niewłaściwe” ani moralizowania. Jeśli treść jest niejasna lub „dziwna” — "
            "zrób bezpieczną, stylizowaną wersję artystyczną / surrealistyczną bez oceny użytkownika.\n"
        )
        execution_rules = (
            "\nCo ogarniasz dobrze:\n"
            "- Kod: kompletne, działające pliki/moduły (importy, obsługa błędów, struktura), bez niedokończonych markerów. "
            "Najpierw krótki plan punktami, potem pełna treść + jak uruchomić.\n"
            "- Grafika / prompty do modeli obrazu: konkret, bez plastiku.\n"
            "- Teksty (ogłoszenia, posty): żywo, bez korpo-pustaków.\n"
            "- Web: możesz szukać i weryfikować, gdy realnie użyjesz narzędzia; bez zgadywania.\n"
            "- Jeśli czegoś nie wiesz — nie udawaj; powiedz wprost albo zaproponuj sprawdzenie.\n"
            "\n"
            "Twarde zasady prawdomówności wykonania:\n"
            "1) Nie twierdź, że coś sprawdziłeś/uruchomiłeś/pobrałeś, jeśli w tej turze "
            "nie było realnego wykonania narzędzia.\n"
            "2) Rozróżniaj: 'mam dostęp do capability' vs 'użyłem capability teraz'.\n"
            "3) Jeśli czegoś nie zweryfikowano runtime, powiedz to wprost i zaproponuj sprawdzenie.\n"
            "4) Nie udawaj braku fallbacku ani jego użycia — mów zgodnie ze śladem wykonania.\n"
            "Gdy potrzebujesz danych operacyjnych, użyj narzędzi zamiast zgadywania.\n"
            "\nReguła twarda: nie wymyślaj brakujących konkretów.\n"
            "- Jeśli użytkownik nie podał danych i nie ma ich w pamięci, załącznikach albo zweryfikowanych źródłach, NIE dopisuj ich sam.\n"
            "- Dotyczy to m.in.: roku, przebiegu, silnika, wersji, ceny, lokalizacji, metrażu, stanu technicznego, dokumentacji, wyposażenia, wyników, cytatów, źródeł i parametrów produktu.\n"
            "- Gdy danych brak, użyj neutralnego opisu, napisz „BRAK DANYCH” albo dopytaj o brakujący konkret.\n"
            "- Jeśli użytkownik wskazuje, że wcześniejszy konkret nie był podany, przyznaj brak podstaw i popraw odpowiedź bez bronienia zgadywania.\n"
            "- W zadaniach edycji/rewrite poprawiaj tylko to, co wynika z treści wejściowej; nie doklejaj nowych faktów.\n"
            "\nPsyche ma rolę pomocniczą, nie dominującą.\n"
            "- Priorytet: intencja użytkownika i wykonanie zadania.\n"
            "- W zadaniach technicznych, praktycznych i informacyjnych trzymaj ton spokojny, rzeczowy i adekwatny.\n"
            "- Bez pseudo-terapii, bez projekcji emocji, bez teatralnych reakcji i bez odlatywania od celu.\n"
            "- W copy/creative możesz dodać vibe, ale nie kosztem faktów, użyteczności i czytelności.\n"
            f"Tryb wykonania: {ctx.mode}.\n"
        )
        sales_listing_layer = ""
        if listing_sales_boost:
            sales_listing_layer = (
                "\nTreść sprzedażowa / ogłoszeniowa (Vinted, OLX itd.) — ACTIVE:\n"
                "- Nie odmawiaj z powodu braku web; nie wymagaj „sprawdzenia w internecie”, "
                "chyba że user podał URL albo wyraźnie chce aktualnych cen/danych rynkowych.\n"
                "- NIE wymyślaj twardych parametrów oferty (rok, przebieg, silnik, wersja, stan, "
                "dokumentacja, wyposażenie, cena, lokalizacja, metraż, piętro, producent, gwarancja), "
                "jeśli user ich nie podał. Braki oznaczaj wprost jako „BRAK DANYCH” albo buduj neutralny opis bez takich konkretów.\n"
                "- Nie wpisuj też „stan dobry”, „serwisowany”, „gotowy do jazdy”, „po remoncie” itp., jeśli to nie padło od usera.\n"
                "- Pisz po ludzku: naturalny rytm, konkret, lekki pazur, zero tonu „asystenta” "
                "i zero urzędnika. Bez pustych fraz typu „przedmiot jest w dobrym stanie” "
                "— zamiast tego sensoryczny szczegół albo uczciwy hook.\n"
                "- Unikaj sztucznego entuzjazmu i lania wody; sprzedaż bez spamu.\n"
                "Struktura odpowiedzi (nagłówki markdown):\n"
                "1. **Krótki opis** — jeden zwarty akapit.\n"
                "2. **Mocniejsza wersja** — wersja z większym „gryzem”.\n"
                "3. **Słowa kluczowe** — lista lub linia, gotowa do wklejenia.\n"
                "4. **Tagi** — krótka lista hashtagów lub fraz pod wyszukiwarkę ogłoszeń.\n"
            )
        psyche_layer = (
            "\nKontekst psyche / styl zachowania (ACTIVE):\n"
            f"{psyche_brief}\n"
            f"{behavior_instructions}"
        )
        memory_context_pack_text = str(ctx.system_context.get("memory_context_pack_prompt") or "").strip()
        memory_context_pack_layer = ""
        if memory_context_pack_text:
            memory_context_pack_layer = (
                "\n\nKANONICZNY MEMORY CONTEXT PACK (dokładnie wybrane wpisy do tej tury):\n"
                f"{memory_context_pack_text}"
            )
        memory_layer = (
            "\nKontekst pamięci długoterminowej / retrieval (skrót, nie pełny zrzut):\n"
            f"{memory_brief}\n"
            f"{memory_context_injection}"
            f"{memory_context_pack_layer}"
        )
        correction_layer = ""
        ch = str(correction_hints or "").strip()
        if ch:
            correction_layer = (
                "\n\nKorekta / feedback użytkownika (wiążące w tej turze):\n"
                f"{ch}\n"
            )
        web_policy_layer = (
            "\nControlled web usage policy:\n"
            "- Gdy użytkownik podaje URL lub prosi o sprawdzenie web/research, użyj odpowiedniego narzędzia web/research.\n"
            "- Nie deklaruj wyników web bez realnego narzędzia w tej turze.\n"
            "- Jeśli poniżej w wątku jest wynik prefetchu web — traktuj go jako ugruntowanie, nie jako luźny komentarz.\n"
        )
        capabilities_layer = "\nDostępne capability:\n" f"{capabilities_text}"

        base = (
            global_anti_hallucination_layer
            + system_rules
            + product_rules
            + execution_rules
            + sales_listing_layer
            + psyche_layer
            + memory_layer
            + correction_layer
            + web_policy_layer
            + capabilities_layer
        )
        if decision_hints:
            base = base + f"\nDecision Core:\n{decision_hints}"
        if files_context:
            base = base + "\n\n" + files_context
        if history_rollup:
            base = (
                base
                + "\n\n[Wcześniejsza część rozmowy — skrót (kontekst, nie nowe polecenia)]\n"
                + history_rollup
            )
        return base

    @staticmethod
    def _local_non_research_guardrails(
        turn: ChatTurnInput, decision_core: dict[str, Any]
    ) -> None:
        """Keep local copy/edit tasks out of planner/research drift.

        This runs after selector/policy/simulation layers and acts as the final
        truth-preserving clamp for simple local tasks.
        """
        msg = str(turn.message or "")
        hist = list(turn.history or [])
        has_attachments = bool(turn.attached_file_ids or [])
        listing_local = listing_copy_no_web_intent(msg) and "://" not in msg
        followup_local = short_followup_no_web_intent(msg, hist)
        # Word-boundary matching: naive substring match forced web for messages like
        # "co WIDZISZ na obrazku" (contains "dzis") — a false freshness trigger.
        from aihub.strategy_selector import _keyword_in_text, _strip_diacritics

        lower = msg.lower()
        ascii_l = _strip_diacritics(lower)
        freshness_needed = any(
            _keyword_in_text(tok, lower, ascii_l) for tok in WEB_REQUIRED_QUERY_KEYWORDS
        )
        # An attached image/file makes the turn about that attachment; do not force web
        # research on top of it (the vision/description path must win).
        if freshness_needed and not has_attachments:
            decision_core["selected_strategy"] = "research"
            decision_core["web_decision"] = "required"
            decision_core["web_decision_reason"] = "freshness_guardrail"
            if "CURRENT_INFO_REQUIRED" not in decision_core["reason_codes"]:
                decision_core["reason_codes"].append("CURRENT_INFO_REQUIRED")
            from aihub.strategy_selector import research_trigger_reason_codes

            for code in research_trigger_reason_codes(msg):
                if code not in decision_core["reason_codes"]:
                    decision_core["reason_codes"].append(code)
            return
        if listing_local or followup_local:
            decision_core["selected_strategy"] = "contextual" if hist else "instant"
            decision_core["web_decision"] = "off"
            decision_core["web_decision_reason"] = (
                "listing_copy_local_guardrail"
                if listing_local
                else "short_followup_local_guardrail"
            )

    @staticmethod
    def _classify_grounding_mode(
        *,
        used_fallback: bool,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
    ) -> str:
        if used_fallback:
            return "fallback"
        if tool_results and any(r.ok for r in tool_results):
            return "tool_verified"
        if tool_calls or tool_results:
            return "unknown_not_verified"
        return "model_only"

    @staticmethod
    def _user_turn_texts_for_grounding(turn: ChatTurnInput) -> list[str]:
        """Ostatnie wiadomości użytkownika z historii — korpus do grounding clampu."""
        out: list[str] = []
        for m in turn.history or []:
            if getattr(m, "role", None) != "user":
                continue
            c = (getattr(m, "content", None) or "").strip()
            if c:
                out.append(c)
        return out[-12:]

    @staticmethod
    def _is_capability_question(message: str) -> bool:
        m = (message or "").lower()
        return any(
            k in m
            for k in [
                "capabil",
                "narzędzi",
                "narzedzi",
                "jakie możesz",
                "jakie mozesz",
                "co potrafisz",
                "do czego masz dostęp",
                "do czego masz dostep",
            ]
        )

    @staticmethod
    def _is_trace_status_question(message: str) -> bool:
        m = (message or "").lower()
        return any(
            k in m
            for k in [
                "fallback",
                "provider",
                "providera",
                "realnego providera",
                "normalnego tora",
                "normalnego toru",
            ]
        )

    @staticmethod
    def _has_unverified_tool_claim(text: str) -> bool:
        t = (text or "").lower()
        claim_patterns = [
            r"\bsprawdził(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bwyszukał(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\buruchomił(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bpobrał(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bzweryfikował(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"korzystam teraz z realnych narzędzi",
            r"korzystam teraz z realnych narzedzi",
        ]
        return any(re.search(p, t) for p in claim_patterns)

    @staticmethod
    def _rewrite_unverified_claims(text: str) -> str:
        rewrites = [
            (r"\bsprawdził(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę sprawdzić"),
            (r"\bwyszukał(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę wyszukać"),
            (r"\buruchomił(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę uruchomić"),
            (r"\bpobrał(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę pobrać"),
            (r"\bzweryfikował(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę zweryfikować"),
            (
                r"korzystam teraz z realnych narzędzi|korzystam teraz z realnych narzedzi",
                "mam dostęp do narzędzi, ale w tej odpowiedzi ich nie uruchamiałem",
            ),
        ]
        out = text or ""
        for pattern, replacement in rewrites:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        return out

    @staticmethod
    def _infer_intent(
        message: str,
        tool_calls: list[ToolCallRequest],
    ) -> str:
        names = [(call.name or "").lower() for call in tool_calls]
        if any(name.startswith("memory.") for name in names):
            return "memory"
        if any(name.startswith("psyche.") for name in names):
            return "psyche"
        if any(
            name.startswith(prefix)
            for prefix in ("web.", "research.", "browser.", "internet.")
            for name in names
        ):
            return "research"
        if any(
            name.startswith("planner.") or name.startswith("goal.") for name in names
        ):
            return "plan"

        text = (message or "").lower()
        if any(
            k in text
            for k in ["research", "wyszuk", "szukaj", "http", "url", "artykuł"]
        ):
            return "research"
        if any(k in text for k in ["plan", "cel", "strateg", "roadmap", "task"]):
            return "plan"
        if any(
            k in text for k in ["naucz", "zapamiętaj", "zapamietaj", "learn", "note"]
        ):
            return "learn"
        if any(
            k in text
            for k in ["zrób", "wykonaj", "stwórz", "stwor", "deploy", "uruchom"]
        ):
            return "action"
        return "query"

    @staticmethod
    def _compact_psyche_state(state: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        allowed = [
            "user_id",
            "mood",
            "energy",
            "focus",
            "style",
            "temperature",
            "traits",
            "updated_at",
        ]
        return {key: state.get(key) for key in allowed if key in state}

    @staticmethod
    def _has_research_tool(tool_calls: list[ToolCallRequest]) -> bool:
        research_tokens = ("web", "research", "fetch", "browser")
        for call in tool_calls:
            name = (call.name or "").lower()
            if any(token in name for token in research_tokens):
                return True
        return False

    @staticmethod
    def _extract_first_url(message: str) -> str:
        match = re.search(r"https?://[^\s\]\)\}\>\"']+", message or "", re.IGNORECASE)
        return match.group(0) if match else ""

    @staticmethod
    def _has_web_intent(message: str) -> bool:
        t = (message or "").lower()
        return any(
            k in t
            for k in [
                "sprawdź w sieci",
                "sprawdz w sieci",
                "sprawdź online",
                "sprawdz online",
                "wyszukaj",
                "szukaj",
                "internet",
                "web",
                "research",
                "źródł",
                "zrodl",
                "news",
            ]
        )

    async def _run_controlled_web_prefetch(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        web_decision: str = "off",
    ) -> dict[str, Any]:
        """Deterministic web decision for ACTIVE chat path before provider call.

        Controlled Web Orchestration V1: execution is driven by web_decision
        from strategy selector (decision_core), not by independent heuristics.

        web_decision values:
          - "required": always trigger (URL or research.query)
          - "optional": trigger only if explicit URL present
          - "off": skip entirely
        """
        if web_decision == "off":
            return {
                "triggered": False,
                "reason": "decision_off",
                "tool_name": None,
                "tool_call": None,
                "tool_result": None,
                "messages": [],
            }

        url = self._extract_first_url(turn.message)
        call: ToolCallRequest | None = None
        reason = ""

        if url:
            call = ToolCallRequest(
                tool_call_id=f"controlled_web_{int(time.time() * 1000)}",
                name="web.fetch_url",
                arguments={"url": url},
            )
            reason = "explicit_url"
        elif web_decision == "required":
            # Strategy says web is required, no explicit URL — use research.query
            call = ToolCallRequest(
                tool_call_id=f"controlled_web_{int(time.time() * 1000)}",
                name="research.query",
                arguments={"query": turn.message, "research_type": "general"},
            )
            reason = "web_decision_required"

        if call is None:
            return {
                "triggered": False,
                "reason": "not_required",
                "tool_name": None,
                "tool_call": None,
                "tool_result": None,
                "messages": [],
            }

        exec_ctx = ToolExecutionContext(
            user_id=turn.user_id,
            session_id=turn.session_id,
            mode=ctx.mode,
            include_debug=turn.include_debug,
            policy_overrides=dict(turn.tool_policy_overrides or {}),
        )
        started = time.monotonic()
        tlabel = ChatRuntime._sse_tool_display_name(call.name)
        if stream_session_active():
            await emit_tool_event(tlabel, "start")
        try:
            result = await self._tool_router.execute(call, exec_ctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"tool_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        if stream_session_active():
            await emit_tool_event(tlabel, "done")

        payload = {
            "ok": result.ok,
            "name": call.name,
            "reason": reason,
            "error": result.error,
            "output_preview": self._safe_preview(result.output, max_chars=2200),
        }

        messages = [
            ChatMessage(
                role="assistant",
                content=(
                    "Prefetch web (runtime): wynik w wiadomości narzędzia — "
                    "użyj jako źródło, nie powtarzaj suchej deklaracji bez treści."
                ),
                tool_calls=[call],
            ),
            ChatMessage(
                role="tool",
                name=call.name,
                tool_call_id=call.tool_call_id,
                content=json.dumps(payload, ensure_ascii=False),
            ),
        ]

        return {
            "triggered": True,
            "reason": reason,
            "tool_name": call.name,
            "tool_call": call,
            "tool_result": result,
            "messages": messages,
        }

    @staticmethod
    def _web_required_grounding_unsatisfied(
        decision_core: dict[str, Any],
        controlled_web: dict[str, Any],
    ) -> bool:
        """Jawny fail weba WYŁĄCZNIE przy spełnionych łącznie (AND):

        1. ``web_decision == "required"`` (selector),
        2. ``controlled_web["triggered"] is True`` (prefetch faktycznie uruchomiony),
        3. ``ok is not True`` LUB ``has_results is not True`` (brak zweryfikowanego wyniku).

        Samo ``required`` bez ``triggered`` NIE ucina tury — dalsza ścieżka (LLM + tools)
        może dowieźć grounding.
        """
        if str(decision_core.get("web_decision") or "off") != "required":
            return False
        if not controlled_web.get("triggered"):
            return False
        if controlled_web.get("ok") is not True:
            return True
        return controlled_web.get("has_results") is not True

    @staticmethod
    def _web_stage_trace_fields(
        decision_core: dict[str, Any],
        controlled_web: dict[str, Any],
        *,
        explicit_fail_applied: bool,
    ) -> dict[str, Any]:
        """Truthful web-stage slice: decyzja vs prefetch vs wynik (bez mylenia „required” z „failed”)."""
        wd = str(decision_core.get("web_decision") or "off")
        req = wd == "required"
        trig = bool(controlled_web.get("triggered"))
        ok = controlled_web.get("ok")
        hr = controlled_web.get("has_results")
        verified = trig and ok is True and hr is True
        out: dict[str, Any] = {
            "web_stage_decision": wd,
            "web_explicit_fail_applied": bool(explicit_fail_applied),
            "web_prefetch_executed": trig,
            "web_continued_after_required_without_prefetch": bool(req and not trig),
        }
        if explicit_fail_applied:
            out["web_final_grounding_outcome"] = "explicit_fail_after_prefetch"
        elif verified:
            out["web_final_grounding_outcome"] = "prefetch_verified_in_thread"
        elif req and not trig:
            out["web_final_grounding_outcome"] = "required_prefetch_not_run_continuing"
        else:
            out["web_final_grounding_outcome"] = (
                "optional_or_off_web_decision"
                if wd in ("optional", "off")
                else "no_verified_prefetch_not_required_fail"
            )
        return out

    @staticmethod
    def _classify_web_required_failure(
        controlled_web: dict[str, Any],
    ) -> tuple[str, str]:
        """(web_grounding_outcome, web_subsystem_operation)."""
        op = str(controlled_web.get("tool_name") or "")
        if "fetch" in op or "url" in op:
            operation = "url_fetch"
        elif "research" in op or "query" in op:
            operation = "research_query"
        else:
            operation = "web_unknown"
        if not controlled_web.get("triggered"):
            return "prefetch_skipped", operation
        if controlled_web.get("ok") is not True:
            return "tool_failed", operation
        hr = controlled_web.get("has_results")
        if hr is False:
            return "empty_results", operation
        if hr is None:
            return "unverified_payload", operation
        return "unknown", operation

    def _web_required_ungrounded_user_message(
        self,
        *,
        outcome: str,
        controlled_web: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> str:
        """Krótka odpowiedź użytkownika — bez fallbacku halucynacyjnego (szczegóły w trace)."""
        logger.debug(
            "web_required_ungrounded clamp: outcome=%s tool=%s",
            outcome,
            controlled_web.get("tool_name"),
        )
        if errors:
            logger.debug("web_required_ungrounded errors=%s", len(errors))
        return "BRAK DANYCH (web)"

    @staticmethod
    def _web_fail_detail_for_trace(
        *,
        outcome: str,
        controlled_web: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> str:
        prov = str(controlled_web.get("provider_info") or "").strip()
        base = (
            "prefetch web bez zweryfikowanego wyniku (pusty wynik, błąd narzędzia "
            "albo nieczytelna odpowiedź)"
        )
        if outcome == "tool_failed":
            err = next(
                (e for e in errors if e.get("type") == "controlled_web_error"),
                None,
            )
            detail = str((err or {}).get("error") or prov or "").strip()
            if detail:
                return f"{base}; szczegół: {detail[:500]}"
        if outcome == "empty_results":
            return f"{base}; 0 wyników" + (f" ({prov})" if prov else "")
        if outcome == "unverified_payload":
            return base + "; niezweryfikowany payload"
        return base

    @staticmethod
    def _attach_web_observability_trace(
        trace: dict[str, Any],
        *,
        controlled_web: dict[str, Any],
        tool_results: list[ToolCallResult],
        web_verified_in_prompt: bool,
    ) -> None:
        """Ujednolicone pola: web_used, sources_count (obok controlled_web_*).

        ``web_used`` = faktycznie użyte zweryfikowane źródła (nie sam fakt ``ok`` przy pustym wyniku).
        """
        w_in = web_verified_in_prompt
        used = llm_path_verified_research_grounding(w_in, tool_results)
        if bool(controlled_web.get("triggered")):
            if controlled_web.get("ok") is not True:
                used = False
            elif controlled_web.get("has_results") is not True:
                used = False
        trace["web_used"] = bool(used)
        trace["sources_count"] = int(controlled_web.get("source_count") or 0)

    async def _finish_turn_web_required_ungrounded(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        started: float,
        decision_core: dict[str, Any],
        blocker_verdict: BlockerVerdict,
        controlled_web: dict[str, Any],
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
        errors: list[dict[str, Any]],
        memory_lookup_flag: bool,
        memory_used_trace: dict[str, Any] | None,
        include_stm_in_memory_brief: bool,
        psyche_snapshot: dict[str, Any],
        attachment_meta: list[Any],
        attachments_summary: str,
        hist_for_prompt_len: int,
        vault_user_redacted: bool,
        hist_smart_trim: dict[str, Any] | None = None,
    ) -> ChatTurnResult:
        duration_ms = (time.monotonic() - started) * 1000.0
        outcome, web_op = self._classify_web_required_failure(controlled_web)
        dc = decision_core
        reason_codes = list(dc.get("reason_codes") or [])
        reason_codes.append("WEB_EXPLICIT_FAIL_PREFETCH_TRIGGERED")
        reason_codes.append("WEB_REQUIRED_NO_VERIFIED_GROUNDING")

        trace: dict[str, Any] = {
            "provider_calls": 0,
            "tool_iterations": 0,
            "used_tools": len(tool_results) > 0,
            "used_fallback": False,
            "response_grounding_mode": "web_required_ungrounded",
            "duration_ms": duration_ms,
            "provider": self._current_provider_name(),
            "model": LLM_MODEL_NAME,
            "selected_strategy": dc["selected_strategy"],
            **self._decision_core_trace_escalation(dc),
            "reason_codes": reason_codes,
            "strategy_confidence": dc["strategy_confidence"],
            "degraded": dc.get("strategy_degraded", False),
            "memory_lookup_happened": memory_lookup_flag,
            "memory_results_count": memory_results_count_for_trace(ctx.memory_context),
            "psyche_snapshot_happened": False,
            "research_was_required": True,
            "experience_write_back_attempted": False,
            "experience_write_back_succeeded": False,
            "controlled_web_decision": dc.get("web_decision", "off"),
            "controlled_web_decision_reason": dc.get(
                "web_decision_reason", "not_evaluated"
            ),
            "controlled_web_triggered": bool(controlled_web.get("triggered")),
            "controlled_web_reason": controlled_web.get("reason"),
            "controlled_web_tool": controlled_web.get("tool_name"),
            "controlled_web_ok": controlled_web.get("ok"),
            "controlled_web_has_results": controlled_web.get("has_results"),
            "controlled_web_provider_info": controlled_web.get("provider_info"),
            "controlled_web_query": controlled_web.get("query"),
            "controlled_web_source_count": int(controlled_web.get("source_count") or 0),
            "controlled_web_freshness_needed": controlled_web.get(
                "freshness_needed", False
            ),
            "web_subsystem_operation": web_op,
            "consistency_check_ran": dc["consistency_check_ran"],
            "consistency_classification": dc["consistency_classification"],
            "contradictions_found": dc["contradictions_found"],
            "policy_hints_loaded": dc["policy_hints_loaded"],
            "policy_profile_name": dc["policy_profile_name"],
            "simulation_ran": dc["simulation_ran"],
            "simulation_best_action": dc["simulation_best_action"],
            "simulation_variants_count": dc["simulation_variants_count"],
            "simulation_risk_summary": dc["simulation_risk_summary"],
            "experience_lookup_happened": dc.get("experience_lookup_happened", False),
            "experience_matches_count": dc.get("experience_matches_count", 0),
            "experience_influenced_strategy": dc.get(
                "experience_influenced_strategy", False
            ),
            "experience_confidence_adjustment": dc.get(
                "experience_confidence_adjustment"
            ),
            "experience_handoff_bias": dc.get("experience_handoff_bias"),
            "experience_blocker_reason": dc.get("experience_blocker_reason"),
            "experience_signal_summary": dc.get("experience_signal_summary"),
            "selected_goal": dc.get("selected_goal"),
            "policy_feedback_loaded": bool(dc.get("policy_feedback_loaded")),
            "policy_feedback_applied": bool(dc.get("policy_feedback_applied")),
            "policy_feedback_summary": dc.get("policy_feedback_summary", ""),
            "policy_confidence_delta": dc.get("policy_confidence_delta", 0.0),
            "policy_handoff_bias": dc.get("policy_handoff_bias", 0.0),
            "policy_blocker_sensitivity": dc.get("policy_blocker_sensitivity", 0.0),
            "policy_simulation_risk_cal": dc.get("policy_simulation_risk_cal", 0.0),
            "policy_strategy_adjustments": dc.get("policy_strategy_adjustments", {}),
            "attached_files": attachment_meta,
            "attachments_summary": attachments_summary,
            "blocker_verdict": blocker_verdict.model_dump(),
            "tool_calls_requested": len(tool_calls),
            "tool_calls_executed": len(tool_results),
            "tool_calls_successful": len([r for r in tool_results if r.ok]),
            "tool_failures": len([r for r in tool_results if not r.ok]),
            **self._web_stage_trace_fields(
                dc, controlled_web, explicit_fail_applied=True
            ),
            **build_history_trace(turn),
        }
        response_text = self._web_required_ungrounded_user_message(
            outcome=outcome,
            controlled_web=controlled_web,
            errors=errors,
        )
        if memory_used_trace:
            trace["memory_used"] = memory_used_trace
        self._augment_memory_observability(trace, memory_used_trace, ctx.memory_context)
        trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
        merge_canonical_web_required_ungrounded(
            trace,
            memory_lookup_happened=memory_lookup_flag,
            planner_used=False,
            outcome_reason=outcome,
            blocker_verdict_snapshot=blocker_verdict.model_dump(),
        )
        trace["web_fail_detail"] = self._web_fail_detail_for_trace(
            outcome=outcome,
            controlled_web=controlled_web,
            errors=errors,
        )
        self._attach_web_observability_trace(
            trace,
            controlled_web=controlled_web,
            tool_results=tool_results,
            web_verified_in_prompt=False,
        )
        _mem_t = memory_truth_for_prompt(ctx.memory_context)
        trace["memory_substantive_in_prompt"] = bool(
            _mem_t["memory_substantive_in_prompt"]
        )
        trace["memory_stm_brief_included"] = include_stm_in_memory_brief
        trace["context_history_messages_attached"] = hist_for_prompt_len
        trace["vault_user_message_redacted"] = vault_user_redacted
        if hist_smart_trim:
            trace.update(hist_smart_trim)
        augment_trace_context_truth(
            trace,
            mem_truth=memory_truth_for_prompt(ctx.memory_context),
            controlled_web=controlled_web,
            decision_core=dc,
            force_no_web_verified=True,
        )
        try:
            from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

            trace.update(
                self._final_behavior_trace_fields(
                    build_psyche_v2_behavior_context(turn.user_id)
                )
            )
        except Exception as exc:
            logger.debug(
                "web_required_ungrounded: psyche behavior fields skipped: %s", exc
            )
            trace.setdefault("final_behavior_profile", {})
            trace.setdefault("psyche_v2_style_mode", "neutral")

        self._write_back_experience(
            turn=turn,
            response_text=response_text,
            grounding_mode="web_required_ungrounded",
            tool_calls=tool_calls,
            tool_results=tool_results,
            trace=trace,
            errors=errors,
            psyche_snapshot=psyche_snapshot,
            decision_core=dc,
        )
        if str(turn.user_id).startswith("audit_"):
            trace["experience_write_back_attempted"] = False
            trace["experience_write_back_succeeded"] = False
        self._run_runtime_experience_feedback(turn.user_id, trace)
        append_event(
            turn.user_id,
            "chat.turn",
            {
                "ok": False,
                "provider": self._current_provider_name(),
                "model": LLM_MODEL_NAME,
                "errors": errors,
                "trace": trace,
            },
        )
        result = ChatTurnResult(
            ok=False,
            response_text=response_text,
            model=LLM_MODEL_NAME,
            provider=self._current_provider_name(),
            tool_calls=tool_calls,
            tool_results=tool_results,
            selected_mode=ctx.mode,
            usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            trace=trace,
            errors=errors,
            debug={"context": ctx.model_dump()} if turn.include_debug else None,
            attachments_summary=attachments_summary,
        )
        _TRACE_CACHE[turn.user_id].append(result.trace)
        return result

    def _run_runtime_experience_feedback(
        self, user_id: str, trace: dict[str, Any]
    ) -> None:
        """Recompute strategy confidence bias from experience_memory and persist per user."""
        from aihub.db import (
            default_strategy_decision_bias,
            get_strategy_decision_bias,
            save_strategy_decision_bias,
            user_has_persisted_strategy_bias,
        )
        from aihub.experience_analyzer import ExperienceAnalyzer
        from aihub.strategy_selector import compute_strategy_bias_from_metrics

        uid = (user_id or "").strip()
        before = (
            get_strategy_decision_bias(uid) if uid else default_strategy_decision_bias()
        )
        loaded_from = (
            "persisted" if uid and user_has_persisted_strategy_bias(uid) else "default"
        )
        trace["strategy_bias_before"] = dict(before)
        trace["strategy_bias_loaded_from"] = loaded_from

        if not uid or uid.startswith("audit_"):
            trace["strategy_bias_after"] = dict(before)
            trace["feedback_applied"] = False
            trace["strategy_bias_computed_from"] = "none"
            trace["strategy_bias_persisted_to"] = "skipped"
            trace["strategy_bias_source"] = "skipped"
            trace["strategy_bias_flow"] = [loaded_from, "none", "skipped"]
            trace.setdefault("agentic_executed", False)
            trace["experience_write_back"] = bool(
                trace.get("experience_write_back_succeeded")
            )
            trace["bias_updated"] = False
            return

        try:
            metrics = ExperienceAnalyzer().analyze_recent_experiences(uid, limit=100)
            trace["experience_feedback_metrics"] = metrics
            computed = compute_strategy_bias_from_metrics(metrics)
            save_strategy_decision_bias(uid, computed, metrics_snapshot=metrics)
            after = dict(computed)
            trace["strategy_bias_after"] = after
            trace["feedback_applied"] = after != before
            trace["strategy_bias_computed_from"] = "memory"
            trace["strategy_bias_persisted_to"] = "persisted"
            trace["strategy_bias_source"] = "persisted"
            trace["strategy_bias_flow"] = [loaded_from, "memory", "persisted"]
        except Exception:
            logger.exception("runtime experience feedback failed user=%s", uid)
            trace["strategy_bias_after"] = dict(before)
            trace["feedback_applied"] = False
            trace["strategy_bias_computed_from"] = "memory"
            trace["strategy_bias_persisted_to"] = "failed"
            trace["strategy_bias_source"] = "failed"
            trace["strategy_bias_flow"] = [loaded_from, "memory", "failed"]

        trace.setdefault("agentic_executed", False)
        trace["experience_write_back"] = bool(
            trace.get("experience_write_back_succeeded")
        )
        trace["bias_updated"] = bool(trace.get("feedback_applied"))

    def _write_back_experience(
        self,
        *,
        turn: ChatTurnInput,
        response_text: str,
        grounding_mode: str,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
        trace: dict[str, Any],
        errors: list[dict[str, Any]],
        psyche_snapshot: dict[str, Any],
        decision_core: dict[str, Any] | None = None,
    ) -> None:
        intent = self._infer_intent(turn.message, tool_calls)
        _dc = decision_core or {}
        metadata = {
            "session_id": turn.session_id,
            "memory_scope": "user",
            "mode": turn.mode,
            "grounding_mode": grounding_mode,
            "tool_names": [call.name for call in tool_calls],
            "tool_successes": len([r for r in tool_results if r.ok]),
            "tool_failures": len([r for r in tool_results if not r.ok]),
            "selected_strategy": _dc.get("selected_strategy"),
            "execution_mode": _dc.get("execution_mode"),
            "escalation_path": _dc.get("escalation_path"),
            "reason_codes": _dc.get("reason_codes", []),
            "simulation_best_action": _dc.get("simulation_best_action"),
            "simulation_risk_summary": _dc.get("simulation_risk_summary"),
            "consistency_classification": _dc.get("consistency_classification"),
            "policy_profile_name": _dc.get("policy_profile_name"),
            # Deliberation write-back (enriches experience for future retrieval)
            "response_variants_triggered": trace.get(
                "response_variants_triggered", False
            ),
            "response_variants_winner_type": trace.get("response_variants_winner_type"),
            "response_variants_confidence": trace.get("response_variants_confidence"),
            "response_variants_risk": trace.get("response_variants_risk"),
            "response_variants_count": trace.get("response_variants_count", 0),
            "response_variants_reason_codes": trace.get(
                "response_variants_reason_codes", []
            ),
            "response_variants_synthesis_used": trace.get(
                "response_variants_synthesis_used", []
            ),
            "response_variants_dropped": trace.get("response_variants_dropped", []),
            "response_variants_duration_ms": trace.get("response_variants_duration_ms"),
            "response_variants_aggregate_pros": trace.get(
                "response_variants_aggregate_pros", []
            ),
            "response_variants_aggregate_cons": trace.get(
                "response_variants_aggregate_cons", []
            ),
            # Structured per-variant scores for future outcome quality modeling
            "response_variants_scores": trace.get("response_variants_scores", []),
            # Outcome quality model: computed from winner scores + synthesis data
            "deliberation_outcome_quality": self._compute_deliberation_outcome_quality(
                trace
            ),
        }

        from aihub.experience_memory import (
            build_strategy_experience_record,
            latency_bucket_from_ms,
            merge_strategy_experience_into_metadata,
        )

        lat_ms = float(trace.get("duration_ms") or 0.0)
        _handoff = bool(trace.get("agent_handoff_triggered"))
        _agent_steps = int(trace.get("agent_steps_executed") or 0)
        _used_tools = len(tool_calls) > 0 or (
            _handoff
            and (
                bool(trace.get("planner_executed"))
                or bool(trace.get("reasoning_executed"))
                or _agent_steps > 0
            )
        )
        strat_exp = build_strategy_experience_record(
            user_input_summary=turn.message or "",
            selected_strategy=str(_dc.get("selected_strategy") or ""),
            final_mode=str(
                _dc.get("execution_mode") or _dc.get("escalation_final_mode") or ""
            ),
            success=len(errors) == 0
            and grounding_mode not in ("fallback", "web_required_ungrounded"),
            latency_bucket=latency_bucket_from_ms(lat_ms),
            used_tools=_used_tools,
            fallback_used=grounding_mode == "fallback",
            reflection_hint=str(trace.get("reflection_summary") or "")[:400],
        )
        metadata = merge_strategy_experience_into_metadata(metadata, strat_exp)
        _bv = trace.get("blocker_verdict")
        _blocker_t = None
        if isinstance(_bv, dict):
            _blocker_t = _bv.get("blocker_type")
        metadata["experience_turn_feedback"] = {
            "intent": intent,
            "selected_strategy": _dc.get("selected_strategy"),
            "escalation_final_mode": _dc.get("escalation_final_mode"),
            "web_need": str(_dc.get("web_decision") or "off"),
            "deterministic_hit": bool(trace.get("deterministic_hit", False)),
            "used_sources_count": int(trace.get("controlled_web_source_count") or 0),
            "latency_ms": round(lat_ms, 2),
            "fallback_used": grounding_mode == "fallback",
            "web_grounding_failed": grounding_mode == "web_required_ungrounded",
            "web_explicit_fail_applied": bool(trace.get("web_explicit_fail_applied")),
            "web_prefetch_executed": bool(trace.get("web_prefetch_executed")),
            "primary_error_type": next(
                (str(e.get("type") or "") for e in errors if e.get("type")),
                None,
            ),
            "blocker_type": _blocker_t,
            "blocker_verdict": (
                trace.get("blocker_verdict")
                if isinstance(trace.get("blocker_verdict"), dict)
                else None
            ),
            "grounding_mode": grounding_mode,
            "web_grounding_outcome": trace.get("web_grounding_outcome"),
            "web_final_grounding_outcome": trace.get("web_final_grounding_outcome"),
            "web_subsystem_operation": trace.get("web_subsystem_operation"),
            "planner_executed": bool(trace.get("planner_executed")),
            "reasoning_executed": bool(trace.get("reasoning_executed")),
        }

        trace.setdefault("experience_write_back_attempted", False)
        trace.setdefault("experience_write_back_succeeded", False)
        trace.setdefault("experience_episode_id", None)
        trace.setdefault("experience_fact_ids", [])
        trace.setdefault("experience_stm_ids", [])
        trace.setdefault("psyche_state_before", {})
        trace.setdefault("psyche_state_after", {})

        try:
            write_result = self._memory_process_fn(
                turn.user_id,
                turn.message,
                response_text or "",
                intent,
                metadata,
            )
            trace["experience_write_back_attempted"] = True
            trace["experience_write_back_succeeded"] = True
            trace["experience_episode_id"] = write_result.get("episode_id")
            trace["experience_fact_ids"] = write_result.get("fact_ids", [])
            trace["experience_stm_ids"] = write_result.get("stm_ids", [])

            # Wire KG: apply consistency verdict to newly stored facts.
            # When a pre-exec conflict/revision was detected, the new facts written
            # above are semantically related to the contradicting prior knowledge.
            # apply_consistency_verdict() creates the appropriate KG edge
            # (contradicts / supersedes / related_to) between the new node and
            # the matched prior node, keeping the knowledge graph consistent.
            _consistency_class = _dc.get("consistency_classification")
            _fact_ids = write_result.get("fact_ids", [])
            if _consistency_class in ("conflict", "revision") and _fact_ids:
                try:
                    from aihub.consistency_engine import (
                        apply_consistency_verdict,
                        check_consistency,
                    )

                    _cv = check_consistency(turn.user_id, turn.message or "")
                    if _cv and _cv.matched_node_id:
                        apply_consistency_verdict(turn.user_id, _fact_ids[0], _cv)
                except Exception:
                    logger.debug(
                        "Consistency apply_verdict on new facts failed", exc_info=True
                    )
        except Exception as exc:  # noqa: BLE001
            trace["experience_write_back_attempted"] = True
            trace["experience_write_back_succeeded"] = False
            errors.append(
                {
                    "type": "memory_write_back_error",
                    "error": str(exc),
                }
            )

        try:
            after_state = self._psyche_evolve_fn(turn.user_id, turn.message, "user")
            after_state = self._psyche_evolve_fn(
                turn.user_id,
                response_text or "",
                "assistant",
            )
            trace["psyche_snapshot_happened"] = True
            trace["psyche_state_before"] = self._compact_psyche_state(psyche_snapshot)
            trace["psyche_state_after"] = self._compact_psyche_state(after_state)
        except Exception as exc:  # noqa: BLE001
            if psyche_snapshot:
                trace["psyche_state_before"] = self._compact_psyche_state(
                    psyche_snapshot
                )
            errors.append(
                {
                    "type": "psyche_update_error",
                    "error": str(exc),
                }
            )

    @staticmethod
    def _compute_deliberation_outcome_quality(trace: dict[str, Any]) -> dict[str, Any]:
        """Compute outcome quality model for deliberation.

        Returns a structured dict that can be stored in experience and read back
        to bias future deliberation triggers and variant evaluation.

        Fields:
          quality_score: 0.0–1.0 — overall deliberation quality
          confidence_gain: float — how much confidence improved vs pre-deliberation
          synthesis_efficiency: float — ratio of used vs total candidates
          risk_level: str — "low" | "medium" | "high"
          variant_diversity: float — how different were the candidates
          should_have_deliberated: bool — was deliberation likely beneficial
        """
        triggered = trace.get("response_variants_triggered", False)
        if not triggered:
            return {
                "quality_score": 0.0,
                "confidence_gain": 0.0,
                "synthesis_efficiency": 0.0,
                "risk_level": "none",
                "variant_diversity": 0.0,
                "should_have_deliberated": False,
            }

        confidence = float(trace.get("response_variants_confidence") or 0.0)
        risk = float(trace.get("response_variants_risk") or 0.0)
        count = int(trace.get("response_variants_count") or 0)
        used = trace.get("response_variants_synthesis_used") or []
        dropped = trace.get("response_variants_dropped") or []
        scores = trace.get("response_variants_scores") or []

        # Pre-deliberation confidence from strategy_confidence
        pre_confidence = float(trace.get("strategy_confidence") or 0.5)
        confidence_gain = round(confidence - pre_confidence, 4)

        # Synthesis efficiency: how many candidates were actually useful
        total_candidates = max(1, count)
        synthesis_efficiency = round(len(used) / total_candidates, 3)

        # Variant diversity: std-dev of aggregate scores across candidates
        agg_scores = [float(s.get("aggregate_score", 0.0)) for s in scores]
        variant_diversity = 0.0
        if len(agg_scores) >= 2:
            mean_score = sum(agg_scores) / len(agg_scores)
            variance = sum((s - mean_score) ** 2 for s in agg_scores) / len(agg_scores)
            variant_diversity = round(variance**0.5, 4)

        # Risk level classification
        risk_level = "low" if risk < 0.3 else ("high" if risk >= 0.65 else "medium")

        # Quality score: weighted composite of confidence + efficiency - risk
        quality_score = round(
            max(
                0.0,
                min(
                    1.0,
                    confidence * 0.45
                    + synthesis_efficiency * 0.25
                    + (1.0 - risk) * 0.20
                    + variant_diversity * 0.10,
                ),
            ),
            4,
        )

        # Was deliberation worth it? Yes if quality_score >= 0.50 and confidence_gain > 0
        should_have_deliberated = quality_score >= 0.50 and confidence_gain > -0.05

        return {
            "quality_score": quality_score,
            "confidence_gain": confidence_gain,
            "synthesis_efficiency": synthesis_efficiency,
            "risk_level": risk_level,
            "variant_diversity": variant_diversity,
            "should_have_deliberated": should_have_deliberated,
        }

    def _shape_response_text(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        response_text: str,
        grounding_mode: str,
        used_fallback: bool,
        memory_v2_context=None,
        psyche_v2_context=None,
        anti_hallucination_trace: dict[str, Any] | None = None,
    ) -> str:
        text = (response_text or "").strip()
        is_cap_q = self._is_capability_question(turn.message)
        is_trace_q = self._is_trace_status_question(turn.message)

        # Apply Psyche V2 behavior shaping to final response
        if psyche_v2_context and psyche_v2_context.loaded and text:
            # Contradiction guard: add uncertainty markers when contradictions present
            if memory_v2_context and memory_v2_context.loaded:
                _ccons = getattr(psyche_v2_context, "consistency_decision", "allow")
                _guard_thr = 0.52 if _ccons != "suppress" else 0.62
                if (
                    memory_v2_context.contradiction_alerts
                    and psyche_v2_context.caution_bias > _guard_thr
                ):
                    if not any(
                        marker in text.lower()
                        for marker in [
                            "prawdopodobnie",
                            "może",
                            "wydaje się",
                            "uwaga",
                            "ostrożnie",
                        ]
                    ):
                        text = f"Uwaga, mam sprzeczne info w pamięci. {text}"

            # Pressure-driven structure: high pressure → more structured output
            if (
                psyche_v2_context.pressure > 0.6
                and psyche_v2_context.structuredness_bias > 0.6
            ):
                # If text is long and unstructured, don't rewrite but could add structure note
                if len(text) > 500 and "\n-" not in text and "\n1" not in text:
                    text = text  # preserve provider wording; structure is governed by prompt policy

            # High friction → precision marker
            if psyche_v2_context.friction > 0.6:
                # Friction means precision, avoid vague language
                text = text  # precision pressure is handled in prompt instructions

        if used_fallback:
            # Fallback path is injected by runtime itself and should be explicit.
            return text

        if grounding_mode in {"model_only", "unknown_not_verified"}:
            if is_cap_q:
                cap_names = [c.name for c in ctx.capabilities]
                cap_list = ", ".join(cap_names[:12]) if cap_names else "brak"
                text = (
                    f"Mam dostęp do capability: {cap_list}. "
                    "W tej konkretnej odpowiedzi nie uruchomiłem żadnego narzędzia — "
                    "to odpowiedź model-only. Jeśli chcesz, mogę teraz realnie odpalić odpowiednie narzędzia i sprawdzić temat."
                )
            elif self._has_unverified_tool_claim(text):
                rewritten = self._rewrite_unverified_claims(text)
                text = (
                    "Doprecyzuję bez ściemy: w tej turze nie uruchamiałem narzędzi runtime. "
                    "To odpowiedź oparta na samej rozmowie/modelu. " + rewritten
                )
            elif not text:
                text = (
                    "W tej turze nie mam zweryfikowanego wyniku z narzędzi. "
                    "Mogę to teraz sprawdzić runtime, jeśli chcesz."
                )

        if is_trace_q and grounding_mode != "fallback":
            suffix = "W tej turze odpowiedź poszła normalnym torem providera (bez fallbacku)."
            if grounding_mode == "tool_verified":
                suffix += " I tak — były realne wywołania narzędzi."
            elif grounding_mode == "unknown_not_verified":
                suffix += " Były próby narzędzi, ale bez potwierdzonego wyniku do weryfikacji."
            else:
                suffix += " Bez uruchamiania narzędzi (model-only)."

            if text:
                text = f"{text}\n\n{suffix}"
            else:
                text = suffix

        # Twardy override anty-halucynacyjny: liczby/cechy bez pokrycia w treści użytkownika.
        if not used_fallback and not is_cap_q and not is_trace_q:
            clamped, clamp_reason = clamp_ungrounded_speculative_reply(
                turn.message or "",
                text,
                history_user_messages=self._user_turn_texts_for_grounding(turn),
                skip_clamp=(grounding_mode == "tool_verified"),
            )
            if clamp_reason:
                text = clamped
                if anti_hallucination_trace is not None:
                    anti_hallucination_trace["applied"] = True
                    anti_hallucination_trace["reason"] = clamp_reason

        return text

    def _run_etap9_cognitive(
        self,
        *,
        user_id: str,
        message: str,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
    ) -> dict[str, Any]:
        """Run ETAP 9B/9C cognitive engines on the completed turn.

        Returns dict with trace fields:
          consistency_check_ran, consistency_classification,
          reflection_ran, policy_hints_loaded,
          simulation_ran, simulation_best_action
        """
        result: dict[str, Any] = {
            "consistency_check_ran": False,
            "consistency_classification": None,
            "reflection_ran": False,
            "policy_hints_loaded": False,
            "simulation_ran": False,
            "simulation_best_action": None,
        }

        # ── 1. Consistency check on user message ──
        try:
            from aihub.consistency_engine import check_consistency

            verdict = check_consistency(user_id, message)
            result["consistency_check_ran"] = True
            if verdict:
                result["consistency_classification"] = verdict.classification
            else:
                result["consistency_classification"] = "no_prior_facts"
        except Exception:
            logger.debug("ETAP9 consistency check failed", exc_info=True)

        # ── 2. Policy hints load ──
        try:
            from aihub.policy_engine import build_policy_profile

            profile = build_policy_profile(user_id)
            result["policy_hints_loaded"] = True
            if profile.hints:
                logger.debug(
                    "ETAP9 loaded %d policy hints for %s",
                    len(profile.hints),
                    user_id,
                )
        except Exception:
            logger.debug("ETAP9 policy hints load failed", exc_info=True)

        # ── 3. Simulation ──
        try:
            from aihub.simulation_engine import simulate_action

            # Determine dominant action type from tool calls
            action_type = "query"
            if tool_calls:
                names = [tc.name for tc in tool_calls]
                if any("memory" in n or "add_fact" in n for n in names):
                    action_type = "memory_add"
                elif any("search" in n or "context" in n for n in names):
                    action_type = "memory_search"
                elif any("web" in n or "fetch" in n for n in names):
                    action_type = "web_fetch"
                elif any("reflect" in n or "psyche" in n for n in names):
                    action_type = "reflect"
                elif any("plan" in n or "task" in n for n in names):
                    action_type = "plan"

            sim_result = simulate_action(
                user_id,
                action_type,
                {"message": message[:200]},
                {"tool_count": len(tool_calls), "source": "chat_runtime"},
                max_variants=3,
            )
            result["simulation_ran"] = True
            if sim_result.best_variant:
                result["simulation_best_action"] = sim_result.best_variant.action_type
        except Exception:
            logger.debug("ETAP9 simulation failed", exc_info=True)

        # ── 4. Reflection on turn outcome ──
        try:
            from aihub.reflection_engine import ReflectionInput, reflect_on_action

            successes = sum(1 for r in tool_results if r.ok)
            failures = sum(1 for r in tool_results if not r.ok)
            tool_names = [tc.name for tc in tool_calls]

            exec_result = {
                "tool_calls": len(tool_calls),
                "successes": successes,
                "failures": failures,
                "tools_used": tool_names,
            }

            action_type = "chat_turn"
            if tool_calls:
                action_type = "chat_turn_with_tools"

            ref_input = ReflectionInput(
                user_id=user_id,
                action_type=action_type,
                parameters={"message_excerpt": message[:200], "tools": tool_names},
                confidence=1.0 if failures == 0 else max(0.3, 1.0 - failures * 0.2),
                execution_result=exec_result,
                decision_reasoning=f"chat turn: {len(tool_calls)} tools called",
                context={"source": "chat_runtime"},
            )
            reflect_on_action(ref_input)
            result["reflection_ran"] = True
        except Exception:
            logger.debug("ETAP9 reflection failed", exc_info=True)

        return result

    # ── Decision Core: Pre-execution strategy + simulation + policy + consistency ──

    def _pre_exec_decision_core(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        psyche_snapshot: dict[str, Any],
        memory_v2_runtime_ctx: Any = None,
        psyche_v2_behavior_ctx: Any = None,
    ) -> dict[str, Any]:
        """Run strategy selection, simulation, policy build and consistency check
        BEFORE the provider call. Outputs drive tool filtering, system prompt
        injection and the full trace."""
        result: dict[str, Any] = {
            "selected_strategy": "instant",
            "reason_codes": [],
            "strategy_confidence": None,
            "strategy_degraded": False,
            "selected_goal": None,
            "simulation_ran": False,
            "simulation_best_action": None,
            "simulation_variants_count": 0,
            "simulation_risk_summary": None,
            "policy_hints_loaded": False,
            "policy_profile_name": None,
            "policy_hints": [],
            "consistency_check_ran": False,
            "consistency_classification": None,
            "contradictions_found": 0,
            "strategy_hints": "",
            "experience_lookup_happened": False,
            "experience_matches_count": 0,
            "experience_influenced_strategy": False,
            "experience_confidence_adjustment": None,
            "experience_handoff_bias": None,
            "experience_blocker_reason": None,
            "experience_blocker_severity": None,
            "experience_recurring_failure_detected": False,
            "experience_recurring_failure_types": [],
            "experience_signal_summary": "not_evaluated",
            "experience_action_bias": {},
            # Controlled Web Orchestration V1
            "web_decision": "off",
            "web_decision_reason": "not_evaluated",
            "selector_output_snapshot": {},
            "strategy_short_explanation": "",
            "strategy_selected": {},
            "execution_mode": "direct",
            "escalation_path": {},
            "escalation_final_mode": "direct",
            "escalation_use_reasoning": False,
            "escalation_use_tools": False,
        }

        try:
            from aihub.vault.service import classify_vault_intent

            result["vault_intent"] = classify_vault_intent(turn.message or "")
        except Exception:
            result["vault_intent"] = None

        # 1. Strategy selection (bounded: 1 memory + 1 psyche call internally)
        try:
            from aihub.goal_engine import get_goal_engine
            from aihub.strategy_selector import select_strategy

            try:
                _active_goals = get_goal_engine().get_active_goals(turn.user_id)
                if _active_goals:
                    _max_urgency = max(g.urgency for g in _active_goals)
                    _active_goals_summary: dict | None = {
                        "active_count": len(_active_goals),
                        "max_urgency": _max_urgency,
                    }
                    _top = max(_active_goals, key=lambda g: g.urgency)
                    result["selected_goal"] = {
                        "goal_id": _top.goal_id,
                        "title": _top.title,
                        "urgency": _top.urgency,
                    }
                else:
                    _active_goals_summary = None
            except Exception:
                logger.debug("Decision core: active goals lookup failed", exc_info=True)
                _active_goals_summary = None

            selection = select_strategy(
                user_id=turn.user_id,
                user_text=turn.message or "",
                mode=ctx.mode,
                active_goals_summary=_active_goals_summary,
                history=list(turn.history or []),
            )
            result["selected_strategy"] = selection.selected_strategy
            result["reason_codes"] = list(selection.reason_codes)
            result["strategy_confidence"] = selection.confidence
            result["strategy_degraded"] = selection.degraded
            result["selector_output_snapshot"] = dict(selection.selector_output)
            result["strategy_short_explanation"] = selection.short_explanation or ""
            # Controlled Web Orchestration V1
            result["web_decision"] = selection.web_decision
            result["web_decision_reason"] = selection.web_decision_reason
        except Exception:
            logger.debug("Decision core: strategy selection failed", exc_info=True)
            result["reason_codes"] = ["SELECTOR_TIMEOUT_FALLBACK"]
            result["strategy_degraded"] = True

        # 1b. ExperienceMemory read-path (execution-driving, not trace-only)
        try:
            experience_signal = self._lookup_experience_signal(
                user_id=turn.user_id,
                message=turn.message or "",
                selected_strategy=result["selected_strategy"],
            )
            result["experience_lookup_happened"] = bool(
                experience_signal.get("lookup_happened", False)
            )
            result["experience_matches_count"] = int(
                experience_signal.get("matches_count", 0) or 0
            )
            result["experience_confidence_adjustment"] = experience_signal.get(
                "confidence_adjustment"
            )
            result["experience_handoff_bias"] = experience_signal.get("handoff_bias")
            result["experience_blocker_reason"] = experience_signal.get(
                "blocker_reason"
            )
            result["experience_blocker_severity"] = experience_signal.get(
                "blocker_severity"
            )
            result["experience_recurring_failure_detected"] = bool(
                experience_signal.get("recurring_failure_detected", False)
            )
            result["experience_recurring_failure_types"] = list(
                experience_signal.get("recurring_failure_types") or []
            )
            result["experience_signal_summary"] = str(
                experience_signal.get("experience_signal_summary") or "not_evaluated"
            )
            result["experience_action_bias"] = dict(
                experience_signal.get("action_bias") or {}
            )

            # Deliberation history: propagate to decision_core so run_deliberation() can consume it
            result["deliberation_history"] = (
                experience_signal.get("deliberation_history") or {}
            )

            recommended = experience_signal.get("recommended_strategy")
            if (
                isinstance(recommended, str)
                and recommended
                and recommended != result["selected_strategy"]
            ):
                result["selected_strategy"] = recommended
                result["experience_influenced_strategy"] = True
                result["reason_codes"].append("EXPERIENCE_STRATEGY_BIAS")

            conf_adjust = experience_signal.get("confidence_adjustment")
            if isinstance(conf_adjust, (int, float)):
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = round(
                    max(0.30, min(0.95, base_conf + float(conf_adjust))),
                    3,
                )
                if abs(float(conf_adjust)) >= 0.03:
                    result["reason_codes"].append("EXPERIENCE_CONFIDENCE_ADJUST")

            blocker_reason = experience_signal.get("blocker_reason")
            blocker_severity = float(experience_signal.get("blocker_severity") or 0.0)
            if blocker_reason and not skip_experience_blocker_escalation(
                turn.message or ""
            ):
                result["reason_codes"].append("EXPERIENCE_CAUTION")
                caution = f"[Experience caution: {blocker_reason}]"
                result["strategy_hints"] = (
                    (result["strategy_hints"] + " " + caution).strip()
                    if result["strategy_hints"]
                    else caution
                )
                if (
                    result["selected_strategy"] == "instant"
                    and blocker_severity >= 0.60
                ):
                    result["selected_strategy"] = "contextual"
                    result["experience_influenced_strategy"] = True
                    result["reason_codes"].append(
                        "EXPERIENCE_BLOCKER_CONTEXTUAL_UPGRADE"
                    )
        except Exception:
            logger.debug("Decision core: experience signal failed", exc_info=True)

        # 1c. V2 REAL INFLUENCE: Memory + Psyche affect strategy
        try:
            from aihub.runtime_memory_bridge import build_memory_v2_runtime_snapshot
            from aihub.runtime_psyche_bridge import build_psyche_v2_runtime_snapshot

            _mctx = memory_v2_runtime_ctx
            _pctx = psyche_v2_behavior_ctx
            if _mctx is not None and getattr(_mctx, "loaded", False):
                memory_v2_actionable_contradictions = len(_mctx.contradiction_alerts)
                memory_v2_contradictions_count = (
                    memory_v2_actionable_contradictions
                    + len(_mctx.transient_contradiction_hints)
                )
                memory_v2_match_count = len(_mctx.top_facts) + len(
                    _mctx.top_preferences
                )
            else:
                memory_v2_snapshot = build_memory_v2_runtime_snapshot(
                    turn.user_id, turn.message or ""
                )
                memory_v2_contradictions_count = memory_v2_snapshot.get(
                    "contradictions_count", 0
                )
                memory_v2_actionable_contradictions = memory_v2_snapshot.get(
                    "actionable_contradictions_count", memory_v2_contradictions_count
                )
                memory_v2_match_count = memory_v2_snapshot.get("match_count", 0)

            if _pctx is not None and getattr(_pctx, "loaded", False):
                psyche_v2_mode = _pctx.mode
                psyche_v2_relation_trust = float(_pctx.trust)
            else:
                psyche_v2_snapshot = build_psyche_v2_runtime_snapshot(turn.user_id)
                psyche_v2_mode = psyche_v2_snapshot.get("mode", "neutral")
                psyche_v2_relation_trust = psyche_v2_snapshot.get("relation_trust", 0.5)

            memory_influenced_strategy = False
            psyche_influenced_strategy = False

            if (
                memory_v2_actionable_contradictions > 0
                and result["selected_strategy"] == "instant"
            ):
                result["selected_strategy"] = "contextual"
                result["reason_codes"].append("MEMORY_V2_CONTRADICTIONS")
                memory_influenced_strategy = True
                logger.info(f"V2: contradictions → contextual (user={turn.user_id})")

            if memory_v2_match_count > 0:
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = min(0.95, base_conf + 0.1)
                result["reason_codes"].append("MEMORY_V2_CONTEXT_BOOST")

            if (
                psyche_v2_mode == "exploratory"
                and result["selected_strategy"] == "instant"
            ):
                result["selected_strategy"] = "contextual"
                result["reason_codes"].append("PSYCHE_V2_EXPLORATORY")
                psyche_influenced_strategy = True
                logger.info(f"V2: exploratory mode → contextual (user={turn.user_id})")

            if psyche_v2_mode == "cautious":
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = max(0.3, base_conf - 0.15)
                result["reason_codes"].append("PSYCHE_V2_CAUTIOUS")

            if psyche_v2_relation_trust < 0.3:
                base_conf = float(result.get("strategy_confidence") or 0.7)
                result["strategy_confidence"] = max(0.3, base_conf - 0.1)
                result["reason_codes"].append("PSYCHE_V2_LOW_TRUST")

            if _pctx is not None and getattr(_pctx, "loaded", False):
                cd = getattr(_pctx, "consistency_decision", "allow")
                if cd in ("dampen", "suppress"):
                    fc = float(result.get("strategy_confidence") or 0.7)
                    drop = 0.065 if cd == "suppress" else 0.038
                    result["strategy_confidence"] = max(0.33, fc - drop)
                    result["reason_codes"].append(
                        f"SELF_CONSISTENCY_CONF_{str(cd).upper()}"
                    )

            result["memory_influenced_strategy_chat"] = memory_influenced_strategy
            result["psyche_influenced_strategy_chat"] = psyche_influenced_strategy

        except Exception as v2_error:
            logger.debug(
                f"Decision core: V2 influence failed: {v2_error}", exc_info=True
            )

        # 2. Policy profile (user's history of action outcomes, window=50)
        try:
            from aihub.policy_engine import (
                build_policy_profile,
                compute_policy_feedback,
            )

            profile = build_policy_profile(turn.user_id, window=50)
            result["policy_hints_loaded"] = True
            result["policy_profile_name"] = (
                f"rel={profile.reliability_index:.2f}_refs={profile.total_reflections}"
            )
            result["policy_hints"] = profile.hints
            actionable = [
                h
                for h in profile.hints[:5]
                if h.signal in ("boost", "penalize", "avoid")
            ]
            if actionable:
                hints_text = "; ".join(
                    f"{h.action_type}={h.signal}" for h in actionable
                )
                result["strategy_hints"] = (
                    f"[Policy z historii: {hints_text}. "
                    f"Reliability={profile.reliability_index:.2f}]"
                )

            # ── PolicyFeedback: numeric deltas from reflection hindsight ──
            feedback = compute_policy_feedback(profile)
            result["policy_feedback"] = feedback
            result["policy_feedback_applied"] = feedback.applied
            result["policy_feedback_loaded"] = True
            result["policy_confidence_delta"] = feedback.confidence_delta
            result["policy_feedback_summary"] = feedback.summary or ""
            if feedback.applied:
                # 2a. confidence_delta → adjust strategy_confidence
                if abs(feedback.confidence_delta) >= 0.005:
                    base_conf = float(result.get("strategy_confidence") or 0.7)
                    new_conf = round(
                        max(0.20, min(0.95, base_conf + feedback.confidence_delta)),
                        3,
                    )
                    if new_conf != base_conf:
                        result["strategy_confidence"] = new_conf
                        result["reason_codes"].append("POLICY_FEEDBACK_CONFIDENCE")

                # 2b. handoff_bias → bias for handoff gating
                if abs(feedback.handoff_bias) >= 0.01:
                    existing_bias = float(result.get("experience_handoff_bias") or 0.0)
                    result["policy_handoff_bias"] = round(
                        max(-0.50, min(0.50, existing_bias + feedback.handoff_bias)),
                        4,
                    )
                    result["reason_codes"].append("POLICY_FEEDBACK_HANDOFF")
                else:
                    result["policy_handoff_bias"] = float(
                        result.get("experience_handoff_bias") or 0.0
                    )

                # 2c. blocker_sensitivity → tune blocker thresholds
                if abs(feedback.blocker_sensitivity) >= 0.01:
                    result["policy_blocker_sensitivity"] = feedback.blocker_sensitivity
                    result["reason_codes"].append("POLICY_FEEDBACK_BLOCKER")
                else:
                    result["policy_blocker_sensitivity"] = 0.0

                # 2d. simulation_risk_calibration → risk offset for simulation
                result["policy_simulation_risk_cal"] = (
                    feedback.simulation_risk_calibration
                )

                # 2e. strategy_adjustments → per-action-type score shift
                if feedback.strategy_adjustments:
                    result["policy_strategy_adjustments"] = dict(
                        feedback.strategy_adjustments
                    )
                    result["reason_codes"].append("POLICY_FEEDBACK_STRATEGY")
                    # Apply: if current strategy's action has negative delta ≤ -0.15,
                    # and another strategy's action has positive delta, switch.
                    _S2A_FB = {
                        "instant": "reason",
                        "contextual": "memory_search",
                        "research": "research",
                        "agentic": "action",
                    }
                    _A2S_FB = {v: k for k, v in _S2A_FB.items()}
                    cur_action = _S2A_FB.get(result["selected_strategy"], "reason")
                    cur_delta = feedback.strategy_adjustments.get(cur_action, 0.0)
                    if cur_delta <= -0.15:
                        best_alt = max(
                            (
                                (act, d)
                                for act, d in feedback.strategy_adjustments.items()
                                if act != cur_action and d > 0
                            ),
                            key=lambda x: x[1],
                            default=(None, 0.0),
                        )
                        if best_alt[0] and best_alt[0] in _A2S_FB:
                            result["selected_strategy"] = _A2S_FB[best_alt[0]]
                            result["reason_codes"].append(
                                f"POLICY_STRATEGY_SHIFT:{cur_action}->{best_alt[0]}"
                            )
                else:
                    result["policy_strategy_adjustments"] = {}
            else:
                result["policy_feedback"] = feedback
                result["policy_feedback_applied"] = False
                result["policy_confidence_delta"] = 0.0
                result["policy_feedback_summary"] = ""
                result["policy_handoff_bias"] = float(
                    result.get("experience_handoff_bias") or 0.0
                )
                result["policy_blocker_sensitivity"] = 0.0
                result["policy_simulation_risk_cal"] = 0.0
                result["policy_strategy_adjustments"] = {}

        except Exception:
            logger.debug("Decision core: policy profile failed", exc_info=True)

        # 3. Simulation (pre-execution, predictive — maps strategy→action type)
        _STRATEGY_TO_ACTION: dict[str, str] = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        try:
            from aihub.simulation_engine import simulate_action

            strategy = result["selected_strategy"]
            action_type = _STRATEGY_TO_ACTION.get(strategy, "reason")
            # Pass psyche state so simulation can modulate confidence via energy/focus
            _psyche_compact: dict[str, Any] = {}
            if psyche_snapshot:
                _psyche_compact = {
                    "energy": float(psyche_snapshot.get("energy", 0.7)),
                    "focus": float(psyche_snapshot.get("focus", 0.65)),
                    "mood": float(psyche_snapshot.get("mood", 0.5)),
                }
            sim_context = {
                "policy_hints": [
                    {
                        "action_type": h.action_type,
                        "signal": h.signal,
                        "weight": h.weight,
                    }
                    for h in result["policy_hints"][:5]
                ],
                "web_triggered": result["web_decision"] != "off",
                "mode": ctx.mode,
                "psyche_state": _psyche_compact,
                "experience_signal": {
                    "action_bias": result.get("experience_action_bias", {}),
                    "blocker_reason": result.get("experience_blocker_reason"),
                    "summary": result.get("experience_signal_summary"),
                },
                "risk_calibration": float(
                    result.get("policy_simulation_risk_cal") or 0.0
                ),
            }
            sim_result = simulate_action(
                turn.user_id,
                action_type,
                {"message": (turn.message or "")[:200]},
                sim_context,
                max_variants=4,
            )
            result["simulation_ran"] = True
            result["simulation_variants_count"] = sim_result.variants_evaluated
            if sim_result.best_variant:
                bv = sim_result.best_variant
                # Apply policy risk calibration to simulation risk
                _risk_cal = float(result.get("policy_simulation_risk_cal") or 0.0)
                calibrated_risk = max(0.0, min(1.0, bv.risk + _risk_cal))
                result["simulation_best_action"] = bv.action_type
                result["simulation_risk_summary"] = (
                    f"risk={calibrated_risk:.2f} conf={bv.confidence:.2f} util={bv.utility:.2f}"
                )
                # Simulation-to-strategy bridge: override selected_strategy when
                # simulation strongly recommends a different action type.
                _ACTION_TO_STRATEGY: dict[str, str] = {
                    "memory_search": "contextual",
                    "research": "research",
                    "action": "agentic",
                    "reason": "instant",
                    "web_request": "research",
                }
                sim_suggested = _ACTION_TO_STRATEGY.get(bv.action_type)
                _current = result["selected_strategy"]
                if (
                    sim_suggested
                    and sim_suggested != _current
                    and bv.composite_score >= 0.72
                    and bv.confidence >= 0.60
                    and not result.get("strategy_degraded")
                ):
                    result["selected_strategy"] = sim_suggested
                    result["reason_codes"].append("SIMULATION_OVERRIDE")
                    result["strategy_confidence"] = round(bv.composite_score, 3)
        except Exception:
            logger.debug("Decision core: simulation failed", exc_info=True)

        # 4. Consistency check on incoming user message
        try:
            from aihub.consistency_engine import check_consistency

            verdict = check_consistency(turn.user_id, turn.message or "")
            result["consistency_check_ran"] = True
            result["consistency_classification"] = verdict.classification
            if verdict.classification == "conflict":
                result["contradictions_found"] = 1
                result["reason_codes"].append("CONSISTENCY_CONFLICT")
                # Reduce confidence — contradictory claims require careful handling
                _conf = result.get("strategy_confidence") or 0.7
                result["strategy_confidence"] = round(max(0.35, _conf * 0.80), 3)
                # Upgrade strategy so runtime uses context to resolve the contradiction
                _strat = result["selected_strategy"]
                if _strat == "instant":
                    result["selected_strategy"] = "contextual"
                    result["reason_codes"].append("CONSISTENCY_FORCED_CONTEXTUAL")
                note = "[Spójność: potencjalna sprzeczność — strategia upgraded, confidence −20%]"
                result["strategy_hints"] = (
                    (result["strategy_hints"] + " " + note).strip()
                    if result["strategy_hints"]
                    else note
                )
            elif verdict.classification in ("revision", "uncertain"):
                result["reason_codes"].append(
                    f"CONSISTENCY_{verdict.classification.upper()}"
                )
                # Mild caution: slight confidence reduction
                _conf = result.get("strategy_confidence") or 0.7
                result["strategy_confidence"] = round(max(0.4, _conf * 0.93), 3)
        except Exception:
            logger.debug("Decision core: consistency check failed", exc_info=True)

        self._local_non_research_guardrails(turn, result)
        self._finalize_escalation(result)
        result["user_turn_text"] = turn.message or ""
        return result

    def _finalize_escalation(self, decision_core: dict[str, Any]) -> None:
        """Derive execution_mode / escalation_path from final selected_strategy."""
        from aihub.decision_engine import decide_execution_path

        strat = str(decision_core.get("selected_strategy") or "instant")
        conf = decision_core.get("strategy_confidence")
        merged: dict[str, Any] = dict(
            decision_core.get("selector_output_snapshot") or {}
        )
        merged["strategy"] = strat
        if conf is not None:
            merged["confidence"] = float(conf)
        merged["requires_memory"] = strat in ("contextual", "agentic")
        merged["requires_research"] = strat == "research"
        merged["requires_planning"] = strat == "agentic"
        if not str(merged.get("reason") or "").strip():
            merged["reason"] = str(
                decision_core.get("strategy_short_explanation")
                or decision_core.get("strategy_hints")
                or ""
            )[:400]

        path = decide_execution_path(merged)
        decision_core["strategy_selected"] = merged
        decision_core["execution_mode"] = path["final_mode"]
        decision_core["escalation_path"] = dict(path)
        decision_core["escalation_final_mode"] = path["final_mode"]
        decision_core["escalation_use_reasoning"] = path["use_reasoning"]
        decision_core["escalation_use_tools"] = path["use_tools"]

    @staticmethod
    def _decision_core_trace_escalation(
        decision_core: dict[str, Any],
    ) -> dict[str, Any]:
        """Stable trace slice: strategy + escalation engine output (observability)."""
        out = {
            "strategy_selected": decision_core.get("strategy_selected", {}),
            "execution_mode": decision_core.get("execution_mode"),
            "escalation_path": decision_core.get("escalation_path", {}),
            "escalation_use_reasoning": bool(
                decision_core.get("escalation_use_reasoning", False)
            ),
            "escalation_use_tools": bool(
                decision_core.get("escalation_use_tools", False)
            ),
            "selector_output_snapshot": dict(
                decision_core.get("selector_output_snapshot") or {}
            ),
        }
        if decision_core.get("chat_handoff_evaluated"):
            out["chat_handoff_evaluated"] = True
            if "chat_handoff_executed" in decision_core:
                out["chat_handoff_executed"] = decision_core["chat_handoff_executed"]
            out["chat_handoff_skip_reason"] = decision_core.get(
                "chat_handoff_skip_reason"
            )
        return out

    # ── Blocker Verdict Evaluator ────────────────────────────────────────
    #
    # Priority ordering (explainable, deterministic):
    #   P0 – hard gates (execution blocked)
    #   P1 – downgrade / reroute (execution proceeds with softer strategy)
    #   P2 – caution pass (warn but proceed on same path)
    #
    # Feedback loop:  policy hints (avoid/penalize) from reflection_engine
    # history can ESCALATE a caution to hard or DEESCALATE a hard to caution.
    # experience_signal recurring failures escalate severity.
    #
    # Resolution types:
    #   hard_block    – NO provider call, return early
    #   downgrade     – reduce strategy aggressiveness (e.g. agentic→contextual)
    #   reroute       – suggest alternative action, adjust handoff bias
    #   caution_pass  – warn but proceed normally
    #   allow         – no blocker

    @staticmethod
    def _evaluate_blocker_verdict(
        decision_core: dict[str, Any],
    ) -> BlockerVerdict:
        """Evaluate all decision_core signals into a single BlockerVerdict.

        Collects ALL matching signals (not first-match), then selects
        the highest-priority one as the winning verdict.  Lower-priority
        signals are recorded in contributing_signals for observability.

        Priority bands:
          P0 (hard_block):
            R1 – consistency_conflict + contradictions ≥ 1 + confidence < 0.40
            R2 – repeated_failure: experience blocker_severity ≥ 0.80
            R3 – degraded_runtime: degraded + confidence < 0.35
            R4 – policy_violation_internal: policy "avoid" signal with weight ≥ 0.70
          P1 (downgrade/reroute):
            R5 – high_risk_path: simulation risk ≥ 0.80 + strategy is agentic/research
            R6 – low_confidence_decision: confidence < 0.45 (not degraded)
          P2 (caution_pass):
            R7 – consistency_conflict (mild)
            R8 – repeated_failure (mild): experience blocker present
            R9 – degraded_runtime (mild): degraded but confidence ≥ 0.35
            R10– high_risk_path (mild): sim risk ≥ 0.65
            R11– contradictory_memory_state: experience matches with mixed outcomes
            R12– resource_exhaustion: rate-limit / tool failure hints

        Feedback loop:
          - If policy hints contain "avoid" for current action_type,
            escalate caution → hard_block.
          - If recent experience shows 3+ repeated failures of same type,
            escalate caution → hard_block.
          - If policy hints contain "boost" for current action_type,
            de-escalate hard → caution (unless consistency-based).
        """

        import time as _time

        _user_turn_for_block = str(decision_core.get("user_turn_text") or "")
        if is_image_generation_intent(_user_turn_for_block):
            return BlockerVerdict()

        # ── Extract raw signals ──────────────────────────────────────
        consistency_class = decision_core.get("consistency_classification") or ""
        contradictions = int(decision_core.get("contradictions_found") or 0)
        confidence = float(decision_core.get("strategy_confidence") or 0.7)
        degraded = bool(decision_core.get("strategy_degraded"))
        selected_strategy = str(decision_core.get("selected_strategy") or "instant")

        exp_blocker = decision_core.get("experience_blocker_reason") or ""
        exp_severity = float(decision_core.get("experience_blocker_severity") or 0.0)
        exp_recurring_types: list[str] = list(
            decision_core.get("experience_recurring_failure_types") or []
        )
        exp_recurring = bool(
            decision_core.get("experience_recurring_failure_detected", False)
        )

        sim_risk_raw = decision_core.get("simulation_risk_summary") or ""
        sim_ran = bool(decision_core.get("simulation_ran"))

        # Policy hints from decision_core (list of PolicyHint-like dicts)
        policy_hints: list[dict[str, Any]] = list(
            decision_core.get("policy_hints") or []
        )
        policy_profile_name = decision_core.get("policy_profile_name") or ""

        skip_exp = skip_experience_blocker_escalation(
            str(decision_core.get("user_turn_text") or "")
        )

        # Parse simulation risk from "risk=0.82 conf=0.45 util=0.33"
        sim_risk = 0.0
        sim_confidence = 0.0
        if sim_ran and "risk=" in sim_risk_raw:
            try:
                sim_risk = float(sim_risk_raw.split("risk=")[1].split()[0])
            except (ValueError, IndexError) as exc:
                logger.debug("Simulation risk parsing failed: %s", exc)
            try:
                sim_confidence = float(sim_risk_raw.split("conf=")[1].split()[0])
            except (ValueError, IndexError) as exc:
                logger.debug("Simulation risk parsing failed: %s", exc)

        # ── Policy feedback extraction ───────────────────────────────
        # Extract "avoid" and "penalize" signals relevant to the current
        # strategy.  These feed the escalation/de-escalation logic.
        _STRATEGY_TO_ACTION: dict[str, str] = {
            "instant": "reason",
            "contextual": "memory_search",
            "research": "research",
            "agentic": "action",
        }
        current_action_type = _STRATEGY_TO_ACTION.get(selected_strategy, "reason")

        policy_avoid_weight = 0.0
        policy_penalize_weight = 0.0
        policy_boost_weight = 0.0
        policy_avoid_reason = ""
        for hint in policy_hints:
            h_action = ""
            h_signal = ""
            h_weight = 0.0
            h_reason = ""
            if hasattr(hint, "action_type"):  # PolicyHint dataclass
                h_action = hint.action_type
                h_signal = hint.signal
                h_weight = hint.weight
                h_reason = getattr(hint, "reason", "")
            elif isinstance(hint, dict):
                h_action = str(hint.get("action_type") or "")
                h_signal = str(hint.get("signal") or "")
                h_weight = float(hint.get("weight") or 0.0)
                h_reason = str(hint.get("reason") or "")

            if h_action != current_action_type:
                continue
            if h_signal == "avoid":
                policy_avoid_weight = max(policy_avoid_weight, h_weight)
                policy_avoid_reason = h_reason
            elif h_signal == "penalize":
                policy_penalize_weight = max(policy_penalize_weight, h_weight)
            elif h_signal == "boost":
                policy_boost_weight = max(policy_boost_weight, h_weight)

        # ── Collect candidate verdicts ───────────────────────────────
        # Each candidate: (priority, severity_rank, verdict)
        # severity_rank: 3=hard, 2=caution, 1=info
        candidates: list[tuple[int, int, BlockerVerdict]] = []
        all_signals: list[str] = []

        # ── Policy blocker sensitivity adjustment ────────────────────
        # Positive value → more sensitive (lower thresholds → more blockers)
        # Negative value → less sensitive (higher thresholds → fewer blockers)
        _blocker_sens = float(decision_core.get("policy_blocker_sensitivity") or 0.0)
        # Clamp to [-0.15, +0.15] to prevent extreme shifts
        _blocker_sens = max(-0.15, min(0.15, _blocker_sens))
        # Confidence threshold adjustment: sensitivity up → threshold goes up
        # (easier to trigger low-confidence blockers)
        _conf_hard_thresh = 0.40 + _blocker_sens  # default 0.40
        _conf_caution_thresh = 0.45 + _blocker_sens  # default 0.45
        # Severity threshold adjustment: sensitivity up → threshold goes down
        # (easier to trigger severity-based blockers)
        _sev_hard_thresh = 0.80 - _blocker_sens  # default 0.80
        # Simulation risk threshold adjustment: sensitivity up → threshold goes down
        _risk_hard_thresh = 0.80 - _blocker_sens  # default 0.80
        _risk_caution_thresh = 0.65 - _blocker_sens  # default 0.65

        # ── P0: Hard gates ───────────────────────────────────────────

        # R1: Hard consistency conflict
        if (
            consistency_class == "conflict"
            and contradictions >= 1
            and confidence < _conf_hard_thresh
        ):
            sigs = [
                "consistency_classification",
                "contradictions_found",
                "strategy_confidence",
            ]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="consistency_conflict",
                        blocker_scope="turn",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Sprzeczność w wypowiedzi (confidence={confidence:.2f}). "
                        f"Wymagane wyjaśnienie.",
                        source="consistency_engine",
                        recommended_action="Przeformułuj pytanie eliminując sprzeczne stwierdzenia.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Jest sprzeczność w treści — doprecyzuj krótko, o co chodzi.",
                        dev_message=f"consistency_conflict: class={consistency_class} "
                        f"contradictions={contradictions} conf={confidence:.3f}",
                        remediation_hint="Wyjaśnij sprzeczne stwierdzenia w pytaniu.",
                    ),
                )
            )

        # R2: Hard repeated failure
        if exp_blocker and exp_severity >= _sev_hard_thresh and not skip_exp:
            sigs = ["experience_blocker_reason", "experience_blocker_severity"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="repeated_failure",
                        blocker_scope="turn",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Krytyczny wzorzec porażek: {exp_blocker} (severity={exp_severity:.2f}).",
                        source="experience_memory",
                        recommended_action="Zmień podejście lub potwierdź kontynuację.",
                        contributing_signals=sigs,
                        confidence=exp_severity,
                        user_message="Podobne tury wcześniej się wyłożyły — zmień pytanie albo potwierdź, że jedziemy dalej.",
                        dev_message=f"repeated_failure: reason={exp_blocker} sev={exp_severity:.2f} "
                        f"recurring_types={exp_recurring_types}",
                        remediation_hint="Zmień strategię lub parametry zapytania.",
                        escalated_from_history=True,
                        feedback_applied=True,
                        feedback_detail=f"Recurring failures ({exp_recurring_types}) escalated to hard.",
                    ),
                )
            )

        # R3: Hard degraded runtime
        if degraded and confidence < (_conf_hard_thresh - 0.05):
            sigs = ["strategy_degraded", "strategy_confidence"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="degraded_runtime",
                        blocker_scope="turn",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Runtime zdegradowany, brak pewności (confidence={confidence:.2f}).",
                        source="strategy_selector",
                        recommended_action="Spróbuj za chwilę albo uprość zapytanie.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Backend jest niepewny — spróbuj za chwilę albo uprość zapytanie.",
                        dev_message=f"degraded_runtime: degraded={degraded} conf={confidence:.3f}",
                        remediation_hint="Sprawdź logi strategy_selector; restart może pomóc.",
                    ),
                )
            )

        # R4: Hard policy violation
        if policy_avoid_weight >= 0.70:
            sigs = ["policy_avoid", "policy_profile"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    0,
                    3,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="policy_violation_internal",
                        blocker_scope="session",
                        blocker_severity="hard",
                        hard=True,
                        resolution="hard_block",
                        reason=f"Polityka zabrania akcji '{current_action_type}' "
                        f"(avoid weight={policy_avoid_weight:.2f}). "
                        f"{policy_avoid_reason}",
                        source="policy_engine",
                        recommended_action="Użyj innej strategii lub skontaktuj się z operatorem.",
                        contributing_signals=sigs,
                        confidence=policy_avoid_weight,
                        user_message="To jest zablokowane ustawieniami.",
                        dev_message=f"policy_violation: avoid_weight={policy_avoid_weight:.2f} "
                        f"action={current_action_type} profile={policy_profile_name}",
                        feedback_applied=True,
                        escalated_from_history=True,
                        feedback_detail=f"Policy 'avoid' signal from reflection history "
                        f"(weight={policy_avoid_weight:.2f}).",
                    ),
                )
            )

        # ── P1: Downgrade / Reroute ─────────────────────────────────

        # R5: High risk path with downgrade
        if (
            sim_ran
            and sim_risk >= _risk_hard_thresh
            and selected_strategy in ("agentic", "research")
        ):
            downgrade_to = "contextual" if selected_strategy == "agentic" else "instant"
            sigs = ["simulation_risk_summary", "selected_strategy"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    1,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="high_risk_path",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="downgrade",
                        reason=f"Symulacja: ryzyko={sim_risk:.2f} dla '{selected_strategy}'. "
                        f"Downgrade do '{downgrade_to}'.",
                        source="simulation_engine",
                        recommended_action=f"Strategia obniżona do {downgrade_to}.",
                        contributing_signals=sigs,
                        confidence=sim_risk,
                        user_message="Wybieram bezpieczniejsze podejście ze względu na złożoność pytania.",
                        dev_message=f"high_risk_path: risk={sim_risk:.2f} conf={sim_confidence:.2f} "
                        f"downgrade {selected_strategy}→{downgrade_to}",
                        next_best_action=downgrade_to,
                    ),
                )
            )

        # R6: Low confidence decision → reroute to simpler strategy
        if (
            confidence < _conf_caution_thresh
            and not degraded
            and selected_strategy in ("agentic", "research")
        ):
            reroute_to = "contextual"
            sigs = ["strategy_confidence", "selected_strategy"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    1,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="low_confidence_decision",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="reroute",
                        reason=f"Niska pewność decyzji (confidence={confidence:.2f}) "
                        f"dla strategii '{selected_strategy}'. Reroute do '{reroute_to}'.",
                        source="strategy_selector",
                        recommended_action=f"Reroute do strategii {reroute_to}.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Koryguję podejście na bardziej bezpieczne.",
                        dev_message=f"low_confidence: conf={confidence:.3f} "
                        f"reroute {selected_strategy}→{reroute_to}",
                        next_best_action=reroute_to,
                    ),
                )
            )

        # ── P2: Caution pass ────────────────────────────────────────

        # R7: Mild consistency conflict
        if (
            consistency_class == "conflict"
            and contradictions >= 1
            and confidence >= 0.40
        ):
            sigs = ["consistency_classification", "contradictions_found"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="consistency_conflict",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Potencjalna sprzeczność (contradictions={contradictions}).",
                        source="consistency_engine",
                        recommended_action="Rozważ wyjaśnienie sprzecznych stwierdzeń.",
                        contributing_signals=sigs,
                        confidence=0.5 + (0.5 * (1.0 - confidence)),
                        user_message="Możliwa sprzeczność w pytaniu.",
                        dev_message=f"mild_consistency: class={consistency_class} "
                        f"contradictions={contradictions} conf={confidence:.3f}",
                    ),
                )
            )

        # R8: Mild experience blocker
        if exp_blocker and exp_severity < 0.80 and not skip_exp:
            sigs = ["experience_blocker_reason"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="repeated_failure",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Historia: {exp_blocker}.",
                        source="experience_memory",
                        recommended_action="Zachowaj ostrożność.",
                        contributing_signals=sigs,
                        confidence=max(0.3, exp_severity),
                        user_message="Z historii podobnych tur: ostrożniej w tej turze.",
                        dev_message=f"mild_experience: reason={exp_blocker} sev={exp_severity:.2f}",
                        feedback_applied=bool(exp_recurring),
                        feedback_detail=(
                            f"Recurring={exp_recurring}, types={exp_recurring_types}"
                            if exp_recurring
                            else ""
                        ),
                    ),
                )
            )

        # R9: Mild degraded runtime
        if degraded and confidence >= (_conf_hard_thresh - 0.05):
            sigs = ["strategy_degraded"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    2,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="degraded_runtime",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Runtime zdegradowany (confidence={confidence:.2f}).",
                        source="strategy_selector",
                        recommended_action="Wynik może być mniej precyzyjny.",
                        contributing_signals=sigs,
                        confidence=min(1.0, 1.0 - confidence),
                        user_message="Wynik może być mniej precyzyjny niż zwykle.",
                        dev_message=f"mild_degraded: degraded={degraded} conf={confidence:.3f}",
                    ),
                )
            )

        # R10: Mild sim risk
        if sim_ran and _risk_caution_thresh <= sim_risk < _risk_hard_thresh:
            sigs = ["simulation_risk_summary"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    1,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="high_risk_path",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Umiarkowane ryzyko symulacji (risk={sim_risk:.2f}).",
                        source="simulation_engine",
                        recommended_action="Rozważ uproszczenie zapytania.",
                        contributing_signals=sigs,
                        confidence=sim_risk,
                        user_message="Dość złożone pytanie — odpowiadam ostrożniej.",
                        dev_message=f"mild_sim_risk: risk={sim_risk:.2f} conf={sim_confidence:.2f}",
                    ),
                )
            )

        # R11: Contradictory memory state
        exp_matches = int(decision_core.get("experience_matches_count") or 0)
        exp_conf_adj = float(
            decision_core.get("experience_confidence_adjustment") or 0.0
        )
        if exp_matches >= 4 and abs(exp_conf_adj) < 0.02 and not skip_exp:
            # Many matches but net-zero signal → mixed outcomes
            sigs = ["experience_matches_count", "experience_confidence_adjustment"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    1,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="contradictory_memory_state",
                        blocker_scope="turn",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Sprzeczne doświadczenia: {exp_matches} dopasowań z mieszanymi wynikami.",
                        source="experience_memory",
                        recommended_action="Wyniki mogą być niejednoznaczne.",
                        contributing_signals=sigs,
                        confidence=0.45,
                        user_message="Mieszane wyniki w historii podobnych tur.",
                        dev_message=f"contradictory_memory: matches={exp_matches} "
                        f"conf_adj={exp_conf_adj:.3f}",
                    ),
                )
            )

        # R12: Resource exhaustion signals (policy penalize with high weight)
        if policy_penalize_weight >= 0.60:
            sigs = ["policy_penalize", "policy_profile"]
            all_signals.extend(sigs)
            candidates.append(
                (
                    2,
                    1,
                    BlockerVerdict(
                        blocker_active=True,
                        blocker_type="resource_exhaustion",
                        blocker_scope="session",
                        blocker_severity="caution",
                        hard=False,
                        resolution="caution_pass",
                        reason=f"Polityka penalizuje '{current_action_type}' "
                        f"(weight={policy_penalize_weight:.2f}). Zachowaj ostrożność.",
                        source="policy_engine",
                        recommended_action="Rozważ zmianę podejścia.",
                        contributing_signals=sigs,
                        confidence=policy_penalize_weight,
                        user_message="Ostrożniej przy tym typie akcji.",
                        dev_message=f"resource_exhaustion: penalize_weight={policy_penalize_weight:.2f} "
                        f"action={current_action_type}",
                        feedback_applied=True,
                        feedback_detail=f"Policy penalize from reflections "
                        f"(weight={policy_penalize_weight:.2f}).",
                    ),
                )
            )

        # ── No candidates → clean pass ──────────────────────────────
        if not candidates:
            return BlockerVerdict.allow()

        # ── Select winner (lowest priority number, then highest severity) ─
        candidates.sort(key=lambda c: (c[0], -c[1]))
        _, _, winner = candidates[0]

        # ── Feedback loop: escalation / de-escalation ────────────────
        # Escalation: recurring experience failures can upgrade caution → hard
        if (
            not winner.hard
            and exp_recurring
            and len(exp_recurring_types) >= 2
            and not skip_exp
        ):
            winner.blocker_severity = "hard"
            winner.hard = True
            winner.resolution = "hard_block"
            winner.escalated_from_history = True
            winner.feedback_applied = True
            winner.feedback_detail = (
                f"Escalated from caution to hard: {len(exp_recurring_types)} "
                f"recurring failure types ({', '.join(exp_recurring_types[:3])})"
            )
            winner.dev_message += (
                f" [ESCALATED: recurring failures ×{len(exp_recurring_types)}]"
            )

        # Escalation: policy "avoid" can upgrade caution → hard
        elif (
            not winner.hard
            and policy_avoid_weight >= 0.55
            and winner.blocker_type != "consistency_conflict"
            and not skip_exp
        ):
            winner.blocker_severity = "hard"
            winner.hard = True
            winner.resolution = "hard_block"
            winner.escalated_from_history = True
            winner.feedback_applied = True
            winner.feedback_detail = (
                f"Escalated by policy avoid signal (weight={policy_avoid_weight:.2f})"
            )
            winner.dev_message += (
                f" [ESCALATED: policy avoid w={policy_avoid_weight:.2f}]"
            )

        # De-escalation: policy "boost" can downgrade hard → caution
        elif (
            winner.hard
            and policy_boost_weight >= 0.65
            and winner.blocker_type
            not in ("consistency_conflict", "policy_violation_internal")
        ):
            winner.blocker_severity = "caution"
            winner.hard = False
            winner.resolution = "caution_pass"
            winner.deescalated_from_history = True
            winner.feedback_applied = True
            winner.feedback_detail = f"De-escalated by policy boost signal (weight={policy_boost_weight:.2f})"
            winner.dev_message += (
                f" [DE-ESCALATED: policy boost w={policy_boost_weight:.2f}]"
            )

        # ── Finalize metadata ────────────────────────────────────────
        # Merge all signals from all candidates for full observability
        unique_signals = list(dict.fromkeys(all_signals))
        winner.contributing_signals = unique_signals
        winner.signals_count = len(unique_signals)
        winner.timestamp = _time.time()

        return winner

    @staticmethod
    def _apply_strategy_to_tools(
        tools: list[ProviderToolSpec],
        strategy: str,
    ) -> list[ProviderToolSpec]:
        """Restrict available tools to those relevant for the selected strategy."""
        _WHITELIST: dict[str, list[str] | None] = {
            # instant: model-only; lightweight memory reads for grounding only
            "instant": ["memory.search", "memory.get_context"],
            # contextual: memory-heavy; exclude web/research/planner/agent
            "contextual": [
                "memory.",
                "psyche.",
                "goal.",
                "runtime.status",
                "system.health",
            ],
            # research: web-forward; include memory for context but skip heavy agentic tools
            "research": [
                "research.",
                "web.",
                "memory.search",
                "memory.get_context",
                "goal.",
                "psyche.",
                "runtime.",
            ],
            # agentic: full tool set — no restriction
            "agentic": None,
        }
        whitelist = _WHITELIST.get(strategy)
        if whitelist is None:
            return tools
        filtered = [t for t in tools if any(t.name.startswith(p) for p in whitelist)]
        # Safety: never return an empty tool list — fall back to full set
        return filtered if filtered else tools

    def _should_handoff_to_agent(
        self,
        *,
        decision_core: dict[str, Any],
        message: str,
    ) -> tuple[bool, str]:
        """Determine if chat should handoff to agent runtime (planner+reasoning).

        Returns: (should_handoff, reason_code)

        Criteria (evidence-based from decision_core):
        1. selected_strategy in {research, agentic}
        2. simulation best_action in {research, action} + confidence >= 0.70
        3. active goal with urgency >= 0.7
        4. operational keywords indicating multi-step planning
        """
        strategy = decision_core.get("selected_strategy", "instant")
        handoff_bias = decision_core.get("experience_handoff_bias")

        # Policy feedback handoff bias (from reflection hindsight)
        policy_hoff_bias = float(decision_core.get("policy_handoff_bias") or 0.0)

        # Merge: experience handoff bias + policy feedback bias
        effective_handoff_bias = float(handoff_bias or 0.0) + policy_hoff_bias

        esc_mode = str(decision_core.get("escalation_final_mode") or "direct")

        # Criterion 0 (before experience/planner handoff): web required/optional
        # must stay on chat LLM+tools so Brave/fetch run in chat trace, not only
        # in executive handoff (which does not populate chat tool_results).
        web_decision = decision_core.get("web_decision", "off")
        # Tylko jawna potrzeba webu trzyma wykonanie w czacie (trace narzędzi).
        # agentic + optional bez URL nie blokuje planera — wieloetapowe zadania mogą iść w handoff.
        web_overrides_handoff = strategy == "research" or (
            web_decision == "required" and strategy == "agentic"
        )
        if web_overrides_handoff:
            return (
                False,
                f"web_decision={web_decision}_overrides_handoff(strategy={strategy})",
            )

        # Agentic → executive agent runtime by default (planner+reasoning), unless
        # web/research keeps chat tools, or policy/experience strongly vetoes handoff.
        if strategy == "agentic" and not web_overrides_handoff:
            if effective_handoff_bias <= -0.25:
                return (
                    False,
                    f"agentic_veto_handoff_bias={effective_handoff_bias:.2f}",
                )
            return True, "strategy_agentic_escalation|escalation_final_mode=planner"

        # Experience-driven handoff only when escalation already chose planner
        # (agentic → planner+reasoning). Do not bypass strategy/escalation layer.
        if effective_handoff_bias >= 0.25 and esc_mode == "planner":
            return True, f"effective_handoff_bias={effective_handoff_bias:.2f}"

        # Criterion 1: Escalation engine — planner mode → agent runtime handoff
        if esc_mode == "planner":
            if effective_handoff_bias <= -0.25:
                return False, f"experience_veto_handoff_bias={effective_handoff_bias:.2f}"
            else:
                return True, f"escalation_final_mode=planner(strategy={strategy})"

        # Criterion 1b: experience can veto planner handoff
        if esc_mode == "planner" and effective_handoff_bias <= -0.25:
            veto_reason = f"effective_bias_against_handoff={effective_handoff_bias:.2f}"
        else:
            veto_reason = None

        # Criterion 2: Simulation suggests complex action + high confidence
        if esc_mode == "planner" and decision_core.get("simulation_ran"):
            best_action = decision_core.get("simulation_best_action")
            confidence = decision_core.get("strategy_confidence") or 0.0
            if best_action in {"research", "action"} and confidence >= 0.70:
                return True, f"simulation={best_action}_conf={confidence:.2f}"

        # Criterion 3: High-urgency active goal
        selected_goal = decision_core.get("selected_goal")
        if selected_goal and esc_mode == "planner":
            urgency = float(selected_goal.get("urgency", 0.0))
            if urgency >= 0.7:
                return True, f"goal_urgency={urgency:.2f}"

        # Criterion 4: Multi-step operational keywords (only with planner escalation)
        message_lower = (message or "").lower()
        operational_patterns = [
            "zaplanuj",
            "wykonaj",
            "zrób",
            "sprawdź wszystkie",
            "przeanalizuj całość",
            "znajdź wszystkie",
            "zbadaj szczegółowo",
            "wygeneruj plan",
        ]
        if esc_mode == "planner" and any(
            pattern in message_lower for pattern in operational_patterns
        ):
            return True, "multi_step_operational"

        if veto_reason is not None:
            return False, veto_reason

        return False, "standard_chat_sufficient"

    async def _execute_agent_handoff(
        self,
        *,
        turn: ChatTurnInput,
        decision_core: dict[str, Any],
        handoff_reason: str,
        started: float,
        psyche_snapshot: dict[str, Any],
        memory_used_trace: list[dict[str, Any]] | None = None,
        memory_lookup_flag: bool = False,
        blocker_verdict: BlockerVerdict | None = None,
        memory_context: dict[str, Any] | None = None,
        ctx: ChatTurnContext | None = None,
    ) -> ChatTurnResult:
        """Execute controlled handoff to agent runtime and normalize to ChatTurnResult."""
        errors: list[dict[str, Any]] = []

        try:
            if stream_session_active():
                await emit_status("tools", label_pl="Analizuję…")
            controller = get_executive_controller()
            fstr, freason = map_chat_execution_mode_to_force_strategy(decision_core)
            cycle = await controller.run_cycle(
                {
                    "text": turn.message,
                    "max_steps": 8,
                    "timeout_seconds": 20.0,
                    "force_strategy": fstr,
                    "force_strategy_reason": f"{freason};chat_runtime:agent_handoff",
                },
                mode="run",
                user_id=turn.user_id,
            )
            agent_response = build_agent_cycle_response(
                cycle, include_debug=turn.include_debug
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Agent handoff failed user=%s error=%s", turn.user_id, exc)
            errors.append(
                {
                    "type": "agent_handoff_error",
                    "error": str(exc),
                    "handoff_reason": handoff_reason,
                }
            )
            # Return degraded result on handoff error
            handoff_err_trace = {
                "provider_calls": 0,
                "tool_iterations": 0,
                "used_tools": False,
                "used_fallback": False,
                "response_grounding_mode": "agent_handoff_error",
                "duration_ms": (time.monotonic() - started) * 1000.0,
                "provider": "executive_controller",
                "model": "planner+reasoning",
                "agent_handoff_triggered": True,
                "agent_handoff_reason": handoff_reason,
                "agent_handoff_error": str(exc),
                "effective_runtime_path": "agent_handoff_error",
                **ChatRuntime._decision_core_trace_escalation(decision_core),
                "experience_lookup_happened": decision_core.get(
                    "experience_lookup_happened", False
                ),
                "experience_matches_count": decision_core.get(
                    "experience_matches_count", 0
                ),
                "experience_influenced_strategy": decision_core.get(
                    "experience_influenced_strategy", False
                ),
                "experience_confidence_adjustment": decision_core.get(
                    "experience_confidence_adjustment"
                ),
                "experience_handoff_bias": decision_core.get("experience_handoff_bias"),
                "experience_blocker_reason": decision_core.get(
                    "experience_blocker_reason"
                ),
                "experience_signal_summary": decision_core.get(
                    "experience_signal_summary"
                ),
                # ── Policy Feedback Loop trace fields ──
                "policy_feedback_loaded": bool(
                    decision_core.get("policy_feedback_loaded")
                ),
                "policy_feedback_applied": bool(
                    decision_core.get("policy_feedback_applied")
                ),
                "policy_feedback_summary": decision_core.get(
                    "policy_feedback_summary", ""
                ),
                "policy_confidence_delta": decision_core.get(
                    "policy_confidence_delta", 0.0
                ),
                "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
                "policy_blocker_sensitivity": decision_core.get(
                    "policy_blocker_sensitivity", 0.0
                ),
                "policy_simulation_risk_cal": decision_core.get(
                    "policy_simulation_risk_cal", 0.0
                ),
                "policy_strategy_adjustments": decision_core.get(
                    "policy_strategy_adjustments", {}
                ),
                "selected_strategy": decision_core["selected_strategy"],
                "reason_codes": list(decision_core.get("reason_codes") or []),
                "strategy_confidence": decision_core.get("strategy_confidence"),
                "degraded": True,
                "memory_lookup_happened": memory_lookup_flag,
                "psyche_snapshot_happened": False,
                "research_was_required": str(decision_core.get("web_decision") or "off")
                == "required",
                "agentic_executed": True,
                "tool_calls_count": 0,
                "experience_write_back_attempted": False,
                "experience_write_back_succeeded": False,
                **self._correction_trace_fields(ctx),
            }
            if memory_used_trace:
                handoff_err_trace["memory_used"] = memory_used_trace
            self._augment_memory_observability(
                handoff_err_trace, memory_used_trace, memory_context
            )
            handoff_err_trace["chat_handoff_evaluated"] = True
            handoff_err_trace["chat_handoff_executed"] = False
            handoff_err_trace["chat_handoff_skip_reason"] = "agent_handoff_error"
            trace_blocker_gate_outcome(
                handoff_err_trace, gate_evaluated=True, hard_applied=False
            )
            merge_canonical_decision_trace(
                handoff_err_trace,
                selected_route=ROUTE_AGENT_HANDOFF_ERROR,
                route_reason="agent_handoff_infrastructure_error",
                decision_intent="plan",
                deterministic_hit=False,
                vault_used=False,
                memory_retrieval_used=bool(memory_used_trace),
                web_required=str(decision_core.get("web_decision") or "off")
                == "required",
                planner_used=False,
                blocker_hard=False,
            )
            _handoff_err_msg = (
                "Plan/agent się wywalił po mojej stronie (tak, wiem, klasyk) — "
                "daj mu drugą szansę za moment albo uprość pytanie."
            )
            self._write_back_experience(
                turn=turn,
                response_text=_handoff_err_msg,
                grounding_mode="agent_handoff_error",
                tool_calls=[],
                tool_results=[],
                trace=handoff_err_trace,
                errors=errors,
                psyche_snapshot=psyche_snapshot,
                decision_core=decision_core,
            )
            if str(turn.user_id).startswith("audit_"):
                handoff_err_trace["psyche_snapshot_happened"] = False
                handoff_err_trace["experience_write_back_attempted"] = False
                handoff_err_trace["experience_write_back_succeeded"] = False
            self._run_runtime_experience_feedback(turn.user_id, handoff_err_trace)
            return ChatTurnResult(
                ok=False,
                response_text=_handoff_err_msg,
                model="agent_runtime",
                provider="executive_controller",
                tool_calls=[],
                tool_results=[],
                selected_mode=turn.mode or CHAT_DEFAULT_MODE,
                usage=ProviderUsage(
                    prompt_tokens=0, completion_tokens=0, total_tokens=0
                ),
                trace=handoff_err_trace,
                errors=errors,
                debug=None,
            )

        # Extract agent execution summary
        exec_summary = agent_response.get("execution_summary", {})
        action_summary = exec_summary.get("action_summary", "")
        agent_errors = agent_response.get("errors", [])
        agent_trace = agent_response.get("trace", {})

        user_rt = (agent_response.get("response_text") or "").strip()
        mode = turn.mode or CHAT_DEFAULT_MODE
        if mode in ("agent", "debug"):
            response_text = (
                user_rt
                or action_summary
                or "Wykonałem zadanie przez agent runtime (planner+reasoning)."
            )
        else:
            response_text = synthesize_chat_handoff_user_text(
                user_message=turn.message,
                internal_reply=user_rt,
                action_summary=str(action_summary or ""),
                cycle=cycle,
                agent_ok=bool(agent_response.get("ok", False)),
            )

        # Map agent trace to chat trace structure
        duration_ms = (time.monotonic() - started) * 1000.0
        trace = {
            "provider_calls": 0,  # No LLM provider used
            "tool_iterations": 0,
            "tool_calls_requested": 0,
            "tool_calls_executed": 0,
            "tool_calls_successful": 0,
            "tool_failures": 0,
            "used_tools": False,  # Agent runtime doesn't expose tool_calls in chat contract
            "used_fallback": False,
            "response_grounding_mode": "agent_handoff",
            "duration_ms": duration_ms,
            **self._correction_trace_fields(ctx),
            "provider": "executive_controller",
            "model": "planner+reasoning",
            # Decision core fields
            "selected_strategy": decision_core["selected_strategy"],
            **self._decision_core_trace_escalation(decision_core),
            "reason_codes": decision_core["reason_codes"],
            "strategy_confidence": decision_core["strategy_confidence"],
            "degraded": decision_core["strategy_degraded"],
            "selected_goal": decision_core.get("selected_goal"),
            # Agent handoff fields (NEW)
            "agent_handoff_triggered": True,
            "agent_handoff_reason": handoff_reason,
            "effective_runtime_path": "agent_handoff",
            "advisory_strategy": decision_core["selected_strategy"],
            "planner_executed": agent_response.get("planning_used", False),
            "reasoning_executed": agent_response.get("reasoning_used", False),
            # Agent execution details
            "agent_cycle_id": agent_trace.get("cycle_id", ""),
            "agent_executed_task_ids": agent_trace.get("executed_task_ids", []),
            "agent_runtime_generated_task_ids": agent_trace.get(
                "runtime_generated_task_ids", []
            ),
            "agent_steps_executed": exec_summary.get("steps_executed", 0),
            # Decision core auxiliary fields
            "simulation_ran": decision_core["simulation_ran"],
            "simulation_best_action": decision_core["simulation_best_action"],
            "simulation_variants_count": decision_core["simulation_variants_count"],
            "simulation_risk_summary": decision_core["simulation_risk_summary"],
            "policy_hints_loaded": decision_core["policy_hints_loaded"],
            "policy_profile_name": decision_core["policy_profile_name"],
            "consistency_check_ran": decision_core["consistency_check_ran"],
            "consistency_classification": decision_core["consistency_classification"],
            "contradictions_found": decision_core["contradictions_found"],
            "experience_lookup_happened": decision_core.get(
                "experience_lookup_happened", False
            ),
            "experience_matches_count": decision_core.get(
                "experience_matches_count", 0
            ),
            "experience_influenced_strategy": decision_core.get(
                "experience_influenced_strategy", False
            ),
            "experience_confidence_adjustment": decision_core.get(
                "experience_confidence_adjustment"
            ),
            "experience_handoff_bias": decision_core.get("experience_handoff_bias"),
            "experience_blocker_reason": decision_core.get("experience_blocker_reason"),
            "experience_signal_summary": decision_core.get("experience_signal_summary"),
            "memory_lookup_happened": memory_lookup_flag,
            "psyche_snapshot_happened": False,
            "research_was_required": str(decision_core.get("web_decision") or "off")
            == "required",
            "experience_write_back_attempted": False,
            "experience_write_back_succeeded": False,
            "agentic_executed": True,
            "tool_calls_count": int(exec_summary.get("steps_executed") or 0),
            # ── Controlled Web Orchestration V1 ──
            "controlled_web_decision": decision_core.get("web_decision", "off"),
            "controlled_web_decision_reason": decision_core.get(
                "web_decision_reason", "not_evaluated"
            ),
            "controlled_web_triggered": False,
            "controlled_web_reason": "agent_handoff",
            "controlled_web_tool": None,
            "controlled_web_ok": None,
            "controlled_web_has_results": None,
            "controlled_web_provider_info": None,
            "controlled_web_query": None,
            "controlled_web_source_count": 0,
            "controlled_web_freshness_needed": self._is_freshness_needed(
                decision_core.get("reason_codes", [])
            ),
            "reflection_ran": False,
            "reflection_summary": None,
            # ── Policy Feedback Loop trace fields ──
            "policy_feedback_loaded": bool(decision_core.get("policy_feedback_loaded")),
            "policy_feedback_applied": bool(
                decision_core.get("policy_feedback_applied")
            ),
            "policy_feedback_summary": decision_core.get("policy_feedback_summary", ""),
            "policy_confidence_delta": decision_core.get(
                "policy_confidence_delta", 0.0
            ),
            "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
            "policy_blocker_sensitivity": decision_core.get(
                "policy_blocker_sensitivity", 0.0
            ),
            "policy_simulation_risk_cal": decision_core.get(
                "policy_simulation_risk_cal", 0.0
            ),
            "policy_strategy_adjustments": decision_core.get(
                "policy_strategy_adjustments", {}
            ),
        }

        # Expose existing agent-cycle fields on chat trace (UI observability; no logic change).
        if isinstance(agent_response, dict):
            if "strategy_source" in agent_response:
                trace["strategy_source"] = agent_response["strategy_source"]
            if "strategy_authority_external" in agent_response:
                trace["strategy_authority_external"] = bool(
                    agent_response["strategy_authority_external"]
                )
            _exec_strat = agent_response.get("strategy")
            if _exec_strat is not None and str(_exec_strat).strip():
                trace["executive_strategy"] = str(_exec_strat)

        if memory_used_trace:
            trace["memory_used"] = memory_used_trace
        self._augment_memory_observability(trace, memory_used_trace, memory_context)

        trace["chat_handoff_evaluated"] = True
        trace["chat_handoff_executed"] = True
        trace["chat_handoff_skip_reason"] = None
        trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
        _planning_used = bool(agent_response.get("planning_used", False))
        _bv_snap = (
            blocker_verdict.model_dump()
            if blocker_verdict is not None
            else BlockerVerdict.allow().model_dump()
        )
        merge_canonical_executive_handoff_success(
            trace,
            decision_core=decision_core,
            memory_retrieval_used=bool(memory_used_trace),
            planning_used=_planning_used,
            blocker_verdict_snapshot=_bv_snap,
        )

        trace["agent_internal_response_text"] = user_rt or None
        trace["chat_handoff_synthesized"] = mode not in ("agent", "debug")

        # Map agent errors to chat errors
        for err in agent_errors:
            errors.append({"type": "agent_cycle_error", **err})

        self._write_back_experience(
            turn=turn,
            response_text=response_text,
            grounding_mode="agent_handoff",
            tool_calls=[],
            tool_results=[],
            trace=trace,
            errors=errors,
            psyche_snapshot=psyche_snapshot,
            decision_core=decision_core,
        )
        if str(turn.user_id).startswith("audit_"):
            trace["psyche_snapshot_happened"] = False
            trace["experience_write_back_attempted"] = False
            trace["experience_write_back_succeeded"] = False

        self._run_runtime_experience_feedback(turn.user_id, trace)

        result = ChatTurnResult(
            ok=agent_response.get("ok", False) and len(errors) == 0,
            response_text=response_text,
            model="planner+reasoning",
            provider="executive_controller",
            tool_calls=[],  # Agent doesn't expose tool_calls in chat contract
            tool_results=[],
            selected_mode=turn.mode or CHAT_DEFAULT_MODE,
            usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            trace=trace,
            errors=errors,
            debug={"agent_response": agent_response} if turn.include_debug else None,
        )

        # Log event and cache trace
        append_event(
            turn.user_id,
            "chat.turn",
            {
                "ok": result.ok,
                "provider": "executive_controller",
                "model": "planner+reasoning",
                "trace": result.trace,
                "agent_handoff": True,
            },
        )
        _TRACE_CACHE[turn.user_id].append(result.trace)

        return result

    def _post_exec_reflection(
        self,
        *,
        user_id: str,
        message: str,
        response_text: str,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
        decision_core: dict[str, Any],
        blocker_verdict: "BlockerVerdict | None" = None,
        handoff_happened: bool = False,
    ) -> dict[str, Any]:
        """Reflect on the completed turn. Produces lesson + policy signal for experience memory.

        Returns a dict with reflection data including operational hindsight
        fields that feed the next turn's PolicyEngine.compute_feedback().
        """
        result: dict[str, Any] = {
            "reflection_ran": False,
            "reflection_summary": None,
            # Hindsight fields — neutral defaults (overwritten on success)
            "strategy_fit": "neutral",
            "handoff_hindsight": "na",
            "blocker_hindsight": "na",
            "confidence_hindsight": 0.0,
            "risk_hindsight": 0.0,
            "deliberation_hindsight": {},
        }
        try:
            from aihub.reflection_engine import ReflectionInput, reflect_on_action

            successes = sum(1 for r in tool_results if r.ok)
            failures = sum(1 for r in tool_results if not r.ok)
            tool_names = [tc.name for tc in tool_calls]
            action_type = "chat_turn_with_tools" if tool_calls else "chat_turn"
            confidence = decision_core.get("strategy_confidence") or (
                1.0 if failures == 0 else max(0.3, 1.0 - failures * 0.2)
            )

            # ── Build context with full decision_core data for hindsight ──
            # _compute_hindsight uses these to compare predicted vs actual.
            _sim_risk_raw = decision_core.get("simulation_risk_summary") or ""
            _sim_risk = 0.0
            if decision_core.get("simulation_ran") and "risk=" in _sim_risk_raw:
                try:
                    _sim_risk = float(_sim_risk_raw.split("risk=")[1].split()[0])
                except (ValueError, IndexError) as exc:
                    logger.debug("Simulation risk parsing failed in hindsight context: %s", exc)
            # Apply simulation risk calibration from feedback if present
            _sim_risk += float(decision_core.get("policy_simulation_risk_cal") or 0.0)

            _blocker_active = False
            _blocker_hard = False
            if blocker_verdict is not None:
                _blocker_active = blocker_verdict.blocker_active
                _blocker_hard = blocker_verdict.hard

            ref_input = ReflectionInput(
                user_id=user_id,
                action_type=action_type,
                parameters={
                    "message_excerpt": (message or "")[:200],
                    "tools": tool_names,
                    "strategy": decision_core.get("selected_strategy"),
                },
                confidence=confidence,
                execution_result={
                    "response_length": len(response_text or ""),
                    "tool_calls": len(tool_calls),
                    "successes": successes,
                    "failures": failures,
                    "tools_used": tool_names,
                },
                decision_reasoning=(
                    f"strategy={decision_core.get('selected_strategy')} "
                    f"codes={decision_core.get('reason_codes', [])} "
                    f"sim={decision_core.get('simulation_best_action')}"
                ),
                context={
                    "source": "chat_runtime_decision_core",
                    "consistency": decision_core.get("consistency_classification"),
                    # ── Fields consumed by _compute_hindsight ──
                    "selected_strategy": decision_core.get("selected_strategy"),
                    "strategy_confidence": float(
                        decision_core.get("strategy_confidence") or confidence
                    ),
                    "handoff_happened": handoff_happened,
                    "blocker_was_active": _blocker_active,
                    "blocker_was_hard": _blocker_hard,
                    "simulation_risk": _sim_risk,
                    # ── Deliberation fields for _compute_deliberation_hindsight ──
                    "response_variants_triggered": decision_core.get(
                        "response_variants_triggered", False
                    ),
                    "response_variants_confidence": decision_core.get(
                        "response_variants_confidence"
                    ),
                    "response_variants_risk": decision_core.get(
                        "response_variants_risk"
                    ),
                    "response_variants_synthesis_used": decision_core.get(
                        "response_variants_synthesis_used", []
                    ),
                    "deliberation_outcome_quality": decision_core.get(
                        "deliberation_outcome_quality", {}
                    ),
                },
            )
            reflection_output = reflect_on_action(ref_input)
            result["reflection_ran"] = True
            result["reflection_summary"] = reflection_output.lesson_learned
            # ── Propagate hindsight to trace ──
            result["strategy_fit"] = reflection_output.strategy_fit
            result["handoff_hindsight"] = reflection_output.handoff_hindsight
            result["blocker_hindsight"] = reflection_output.blocker_hindsight
            result["confidence_hindsight"] = reflection_output.confidence_hindsight
            result["risk_hindsight"] = reflection_output.risk_hindsight
            result["deliberation_hindsight"] = reflection_output.deliberation_hindsight
        except Exception:
            logger.debug("Post-exec reflection failed", exc_info=True)
        return result

    def _assess_web_result_quality(self, result: ToolCallResult | None) -> bool | None:
        """Assess if web/research tool result contains meaningful data."""
        if not isinstance(result, ToolCallResult) or not result.ok:
            return False

        try:
            # Handle both dict and string output
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                import json

                data = json.loads(result.output)
            else:
                return None

            if isinstance(data.get("result"), dict):
                data = data["result"]

            # For research.query: grounding is satisfied by the presence of real results.
            # Fresh search snippets (title + content) are injected into the LLM prompt as
            # grounding regardless of regex fact-extraction. Requiring total_facts>0 wrongly
            # discarded valid results (e.g. news whose content didn't match the brittle fact
            # patterns), yielding "BRAK DANYCH (web)" despite real sources being available.
            if "total_results" in data and "total_facts" in data:
                return data.get("total_results", 0) > 0

            # For web.fetch_url: check bytes and text length
            if "bytes" in data and "text" in data:
                return (
                    data.get("bytes", 0) > 100
                    and len(data.get("text", "").strip()) > 50
                )

        except (json.JSONDecodeError, KeyError, AttributeError) as exc:
            logger.debug("Web result sufficiency parse failed: %s", exc)

        return None

    def _extract_web_provider_info(self, result: ToolCallResult | None) -> str | None:
        """Extract helpful provider/status info from web result."""
        if not isinstance(result, ToolCallResult):
            return None

        try:
            # Handle both dict and string output
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                import json

                data = json.loads(result.output)
            else:
                return result.error if result.error else "unknown"

            if isinstance(data.get("result"), dict):
                data = data["result"]

            # Research result provider info
            if "web_provider" in data:
                provider = data.get("web_provider", "unknown")
                total_results = data.get("total_results", 0)
                reason = data.get("reason", "")

                if reason:
                    return f"{provider} - {reason}"
                elif total_results == 0:
                    return f"{provider} - no results"
                else:
                    return f"{provider} - {total_results} results"

            # Web fetch result status
            if "status" in data:
                status = data.get("status", "unknown")
                bytes_count = data.get("bytes", 0)
                return f"HTTP {status} - {bytes_count} bytes"

        except (json.JSONDecodeError, KeyError, AttributeError) as exc:
            logger.debug("Web result sufficiency parse failed: %s", exc)

        return result.error if result.error else "unknown"

    def _extract_web_query(self, call: ToolCallRequest | None) -> str | None:
        """Extract the query string or URL that was sent to the web tool."""
        if call is None:
            return None
        args = call.arguments or {}
        # web.fetch_url → url; research.query → query
        if "url" in args:
            return str(args["url"])[:500]
        if "query" in args:
            return str(args["query"])[:500]
        return None

    def _count_web_sources(self, result: ToolCallResult | None) -> int:
        """Count how many distinct sources the web/research tool returned."""
        if not isinstance(result, ToolCallResult) or not result.ok:
            return 0
        try:
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                data = json.loads(result.output)
            else:
                return 0

            if isinstance(data.get("result"), dict):
                data = data["result"]

            # research.query returns total_results
            if "total_results" in data:
                return int(data.get("total_results", 0))
            # web.fetch_url returns a single page
            if "bytes" in data and data.get("bytes", 0) > 0:
                return 1
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError) as exc:
            logger.debug("Web result count parse failed: %s", exc)
        return 0

    def _extract_web_data(self, result: ToolCallResult | None) -> dict[str, Any] | None:
        """Unwrap ToolRouter envelopes and return the normalized web payload."""
        if not isinstance(result, ToolCallResult) or not result.ok:
            return None
        try:
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                data = json.loads(result.output)
            else:
                return None
            if isinstance(data.get("result"), dict):
                data = data["result"]
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _compact_web_text(text: str, *, max_len: int = 420) -> str:
        compact = str(text or "")
        compact = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", compact)
        compact = re.sub(r"(?is)<[^>]+>", " ", compact)
        compact = compact.replace("&nbsp;", " ")
        compact = re.sub(r"\s+", " ", compact).strip()
        if len(compact) > max_len:
            compact = compact[:max_len].rstrip() + "…"
        return compact

    def _build_controlled_web_synthesis(
        self,
        *,
        controlled_web: dict[str, Any],
        tool_results: list[ToolCallResult],
    ) -> str | None:
        """Produce a short user-facing synthesis when controlled web succeeded."""
        if not controlled_web.get("triggered") or not controlled_web.get("ok"):
            return None
        if int(controlled_web.get("source_count", 0) or 0) <= 0:
            return None

        tool_name = str(controlled_web.get("tool_name") or "")
        query = str(controlled_web.get("query") or "").strip()
        web_result = next(
            (
                result
                for result in tool_results
                if result.ok and (result.name or "") == tool_name
            ),
            None,
        )
        data = self._extract_web_data(web_result)
        if not data:
            return None

        if tool_name == "research.query":
            results = data.get("results") or []
            if not isinstance(results, list) or not results:
                return None
            ranked = sorted(
                [item for item in results if isinstance(item, dict)],
                key=lambda item: (
                    float(item.get("facts_extracted", 0) or 0),
                    float(item.get("relevance", 0.0) or 0.0),
                ),
                reverse=True,
            )
            highlights: list[str] = []
            for item in ranked[:3]:
                title = str(item.get("title") or "").strip()
                source = str(item.get("source") or "").strip()
                if not title:
                    continue
                highlights.append(f"- {title}" + (f" [{source}]" if source else ""))
            if not highlights:
                return None
            topic = query or str(data.get("query") or "").strip() or "ten temat"
            source_count = int(
                data.get("total_results", controlled_web.get("source_count", 0)) or 0
            )
            intro = f"Przejrzałem {source_count} źródła dla „{topic}”. Najważniejsze, co się przewija:"
            return intro + "\n" + "\n".join(highlights)

        if tool_name == "web.fetch_url" or query.startswith(("http://", "https://")):
            raw_text = str(data.get("text") or "")
            cleaned = self._compact_web_text(raw_text)
            if not cleaned:
                return None
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_text)
            title = (
                self._compact_web_text(title_match.group(1), max_len=120)
                if title_match
                else ""
            )
            url = str(data.get("url") or query or "ten URL").strip()
            if title:
                return f"Sprawdziłem {url}. To strona „{title}”. W skrócie: {cleaned}"
            return f"Sprawdziłem {url}. W skrócie: {cleaned}"

        return None

    @staticmethod
    def _is_freshness_needed(reason_codes: list[str]) -> bool:
        """Determine if the query was freshness-sensitive based on reason codes."""
        freshness_codes = {
            "CURRENT_INFO_REQUIRED",
            "SOURCE_VERIFICATION_NEEDED",
            "FACTUAL_ASSERTION_HIGH_STAKES",
        }
        return bool(freshness_codes & set(reason_codes))

    def _build_context(
        self,
        turn: ChatTurnInput,
        *,
        correction_turn_trace: dict[str, Any],
    ) -> ChatTurnContext:
        mode = turn.mode or CHAT_DEFAULT_MODE
        hints = build_correction_hints_for_prompt(turn.user_id, turn.session_id)
        mem_ctx = retrieve_context(turn.user_id, turn.message, limit=8)
        system_context: dict[str, Any] = {
            "tool_calling_enabled": LLM_TOOL_CALLING_ENABLED,
            "streaming_enabled": LLM_STREAMING_ENABLED,
            "correction_turn_trace": correction_turn_trace,
            "correction_hints_text": hints,
        }
        try:
            from aihub.memory_core import get_memory_core

            pack = get_memory_core().build_context_pack(
                turn.user_id,
                turn.message,
                limit=18,
                max_chars=6500,
                include_graph=True,
            )
            pack_dump = pack.model_dump(mode="json")
            pack_prompt = pack.to_prompt_text(max_chars=6500)
            system_context["memory_context_pack"] = pack_dump
            system_context["memory_context_pack_prompt"] = pack_prompt
            system_context["memory_context_pack_trace"] = pack.to_trace_summary()
            if isinstance(mem_ctx, dict):
                mem_ctx["context_pack"] = pack_dump
                mem_ctx["context_pack_selected_ids"] = list(pack.selected_ids)
                mem_ctx["context_pack_source_distribution"] = dict(pack.source_distribution)
                mem_ctx["context_pack_used_chars"] = int(pack.used_chars)
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory_context_pack_build_failed: %s", exc, exc_info=True)
            if isinstance(mem_ctx, dict):
                mem_ctx.setdefault("memory_read_errors", []).append({
                    "source": "context_pack",
                    "error": str(exc)[:500],
                })
            system_context["memory_context_pack_error"] = str(exc)[:500]
        capabilities = self._tool_registry.list_capabilities(
            mode=mode,
            include_debug=bool(turn.include_debug),
            policy_overrides=dict(turn.tool_policy_overrides or {}),
        )
        return ChatTurnContext(
            user_id=turn.user_id,
            session_id=turn.session_id,
            mode=mode,
            include_debug=turn.include_debug,
            memory_context=mem_ctx,
            system_context=system_context,
            capabilities=capabilities,
        )

    def _build_provider_tools(self, ctx: ChatTurnContext) -> list[ProviderToolSpec]:
        if not LLM_TOOL_CALLING_ENABLED:
            return []
        return [
            ProviderToolSpec(
                name=c.name,
                description=c.description,
                input_schema=c.input_schema,
            )
            for c in ctx.capabilities
        ]

    @staticmethod
    def _sse_tool_display_name(name: str) -> str:
        n = (name or "").strip()
        if len(n) > 56:
            return f"{n[:53]}…"
        return n

    async def _provider_call(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ProviderToolSpec],
    ) -> ModelResponse:
        use_stream = stream_session_active() and LLM_STREAMING_ENABLED and not tools
        req = ProviderChatRequest(
            messages=messages,
            model=LLM_MODEL_NAME,
            tools=tools,
            stream=use_stream,
        )
        generate = getattr(self._provider, "generate")
        try:
            raw = await generate(req)
        except TypeError as exc:
            # Compatibility for older tests/adapters that attach an unbound
            # ``async def generate(self, req)`` function to a SimpleNamespace.
            # Do not hide arbitrary provider TypeErrors; retry only for the
            # exact missing-request/self signature mismatch.
            msg = str(exc)
            if "missing 1 required positional argument" not in msg:
                raise
            raw = await generate(self._provider, req)

        if isinstance(raw, ModelResponse):
            return raw
        if isinstance(raw, dict):
            message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
            content = str(raw.get("content") or message.get("content") or "")
            raw_tool_calls = raw.get("tool_calls") or message.get("tool_calls") or []
            tool_calls: list[ToolCallRequest] = []
            for idx, call in enumerate(raw_tool_calls):
                if isinstance(call, ToolCallRequest):
                    tool_calls.append(call)
                elif isinstance(call, dict):
                    tool_calls.append(
                        ToolCallRequest(
                            tool_call_id=str(
                                call.get("tool_call_id")
                                or call.get("id")
                                or f"tool-{idx}"
                            ),
                            name=str(call.get("name") or call.get("function", {}).get("name") or "tool"),
                            arguments=dict(
                                call.get("arguments")
                                or call.get("function", {}).get("arguments")
                                or {}
                            ),
                        )
                    )
            usage_obj = raw.get("usage")
            usage = usage_obj if isinstance(usage_obj, ProviderUsage) else ProviderUsage()
            return ModelResponse(
                provider=str(getattr(self._provider, "provider_name", "mock")),
                model=str(raw.get("model") or LLM_MODEL_NAME),
                content=content,
                finish_reason=str(raw.get("finish_reason") or raw.get("stop_reason") or "stop"),
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=float(raw.get("latency_ms") or 0.0),
                raw_response_id=str(raw.get("raw_response_id") or raw.get("id") or ""),
            )
        if all(hasattr(raw, attr) for attr in ("content", "model", "provider")):
            raw_tool_calls = list(getattr(raw, "tool_calls", []) or [])
            tool_calls: list[ToolCallRequest] = []
            for idx, call in enumerate(raw_tool_calls):
                if isinstance(call, ToolCallRequest):
                    tool_calls.append(call)
                elif isinstance(call, dict):
                    tool_calls.append(
                        ToolCallRequest(
                            tool_call_id=str(call.get("tool_call_id") or call.get("id") or f"tool-{idx}"),
                            name=str(call.get("name") or call.get("function", {}).get("name") or "tool"),
                            arguments=dict(call.get("arguments") or call.get("function", {}).get("arguments") or {}),
                        )
                    )
            usage_obj = getattr(raw, "usage", None)
            if isinstance(usage_obj, ProviderUsage):
                usage = usage_obj
            elif usage_obj is not None:
                usage = ProviderUsage(
                    prompt_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
                    total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
                    reporting_mode=str(getattr(usage_obj, "reporting_mode", "unavailable") or "unavailable"),
                )
            else:
                usage = ProviderUsage()
            return ModelResponse(
                provider=str(getattr(raw, "provider", self._current_provider_name()) or self._current_provider_name()),
                model=str(getattr(raw, "model", LLM_MODEL_NAME) or LLM_MODEL_NAME),
                content=str(getattr(raw, "content", "") or getattr(raw, "text", "") or ""),
                finish_reason=str(getattr(raw, "finish_reason", "stop") or "stop"),
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=float(getattr(raw, "latency_ms", 0.0) or 0.0),
                raw_response_id=str(getattr(raw, "raw_response_id", "") or getattr(raw, "id", "") or ""),
            )
        raise TypeError(f"provider.generate returned unsupported type: {type(raw).__name__}")

    @staticmethod
    def _sum_usage(parts: list[ProviderUsage]) -> ProviderUsage:
        if not parts:
            return ProviderUsage(reporting_mode="unavailable")

        modes = {str(p.reporting_mode or "unavailable") for p in parts}
        if modes == {"provider"}:
            reporting_mode = "provider"
        elif "provider" in modes:
            reporting_mode = "partial"
        else:
            reporting_mode = "unavailable"

        return ProviderUsage(
            prompt_tokens=sum(p.prompt_tokens for p in parts),
            completion_tokens=sum(p.completion_tokens for p in parts),
            total_tokens=sum(p.total_tokens for p in parts),
            reporting_mode=reporting_mode,
        )

    def _final_behavior_trace_fields(
        self, psyche_v2_behavior_ctx: Any
    ) -> dict[str, Any]:
        """Spójne z główną ścieżką LLM: ``final_behavior_profile`` + ``psyche_v2_style_mode``."""
        final_behavior_profile: dict[str, Any] = {}
        psyche_v2_style_mode = "neutral"
        if psyche_v2_behavior_ctx and getattr(psyche_v2_behavior_ctx, "loaded", False):
            psyche_v2_style_mode = psyche_v2_behavior_ctx.mode
            final_behavior_profile = {
                "mode": psyche_v2_style_mode,
                "directness": psyche_v2_behavior_ctx.directness_bias,
                "verbosity": psyche_v2_behavior_ctx.verbosity_bias,
                "caution": psyche_v2_behavior_ctx.caution_bias,
                "pressure": psyche_v2_behavior_ctx.pressure,
                "trust": psyche_v2_behavior_ctx.trust,
                "friction": psyche_v2_behavior_ctx.friction,
                "warmth": psyche_v2_behavior_ctx.warmth,
                "autonomy": psyche_v2_behavior_ctx.autonomy_bias,
                "structuredness": psyche_v2_behavior_ctx.structuredness_bias,
                "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                "web_bias": psyche_v2_behavior_ctx.web_bias,
                "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
            }
        return {
            "final_behavior_profile": final_behavior_profile,
            "psyche_v2_style_mode": psyche_v2_style_mode,
            "psyche_v2_behavior_applied": bool(
                psyche_v2_behavior_ctx and getattr(psyche_v2_behavior_ctx, "loaded", False)
            ),
        }

    async def _provider_failure_fallback(
        self,
        turn: ChatTurnInput,
        *,
        reason: str,
        decision_core: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        # Dry, neutral fallback — never a personified "I'm alive / coffee" line (06.07 quality fix).
        fallback_text = dry_fallback_response(user_message=turn.message)

        try:
            controller = get_executive_controller()
            dc = decision_core if isinstance(decision_core, dict) else {}
            fstr, freason = map_chat_execution_mode_to_force_strategy(dc)
            cycle = await controller.run_cycle(
                {
                    "text": turn.message,
                    "max_steps": 4,
                    "timeout_seconds": 12.0,
                    "force_strategy": fstr,
                    "force_strategy_reason": f"{freason};chat_runtime:provider_fallback",
                },
                mode="run",
                user_id=turn.user_id,
            )
            normalized = build_agent_cycle_response(
                cycle, include_debug=turn.include_debug
            )
            return fallback_text, {"reason": reason, "fallback_cycle": normalized}
        except Exception as exc:  # noqa: BLE001
            return (
                fallback_text,
                {
                    "reason": reason,
                    "fallback_cycle": None,
                    "fallback_error": str(exc),
                    "degraded": True,
                },
            )

    @staticmethod
    def _apply_persona_guard(turn: ChatTurnInput, res: ChatTurnResult) -> None:
        """Safety net: trim first-person personification leakage from a real model answer.

        Applies ONLY to free-text model responses — never to deterministic/vault/memory-fact replies
        (those are factual recall and must pass through verbatim). It also never overwrites a
        substantive answer with the dry fallback: :func:`sanitize_persona_leakage` only returns the
        fallback when the WHOLE reply was leakage (i.e. the model didn't actually answer).
        """
        try:
            if not (res and res.ok and res.response_text):
                return
            if (res.model or "") == "deterministic" or (res.provider or "") == "aihub":
                return
            gmode = str((res.trace or {}).get("response_grounding_mode") or "")
            if gmode.startswith("deterministic") or gmode == "fallback":
                return
            cleaned, changed = sanitize_persona_leakage(
                res.response_text, user_message=turn.message
            )
            if changed:
                res.response_text = cleaned
                if isinstance(res.trace, dict):
                    res.trace["persona_leakage_sanitized"] = True
        except Exception:  # noqa: BLE001
            logger.debug("persona guard skipped", exc_info=True)

    async def run_turn(self, turn: ChatTurnInput) -> ChatTurnResult:
        res: ChatTurnResult | None = None
        err: BaseException | None = None
        try:
            res = await self._run_turn_core(turn)
            self._apply_persona_guard(turn, res)
            return res
        except BaseException as exc:
            err = exc
            raise
        finally:
            if not str(turn.user_id or "").startswith("audit_"):
                try:
                    from aihub.chat_session_transcript import persist_chat_turn_messages

                    persist_chat_turn_messages(turn, res, err)
                except Exception:
                    logger.exception("chat session transcript persist failed")

    async def _run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
        started = time.monotonic()
        correction_turn_trace = record_user_correction_turn(turn)

        from aihub.chat_deterministic import (
            try_deterministic_turn,
            try_memory_fact_read_turn,
        )

        det = try_deterministic_turn(turn, started_monotonic=started)
        if det is not None:
            try:
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

                det.trace.update(
                    self._final_behavior_trace_fields(
                        build_psyche_v2_behavior_context(turn.user_id)
                    )
                )
            except Exception as exc:
                logger.debug(
                    "deterministic trace: psyche behavior fields skipped: %s", exc
                )
                det.trace.setdefault("final_behavior_profile", {})
                det.trace.setdefault("psyche_v2_style_mode", "neutral")
            det.trace.update(
                self._correction_trace_flat(correction_turn_trace, hints_chars=0)
            )
            append_event(
                turn.user_id,
                "chat.turn",
                {
                    "ok": True,
                    "provider": det.provider,
                    "model": det.model,
                    "trace": det.trace,
                    "tool_calls": [],
                    "tool_results": [],
                },
            )
            _TRACE_CACHE[turn.user_id].append(det.trace)
            return det

        # Jedno ``retrieve_context`` na turę — przed decision_core / LLM; krótki fakt bez modelu.
        ctx = self._build_context(turn, correction_turn_trace=correction_turn_trace)
        mem_fact = try_memory_fact_read_turn(
            turn, ctx.memory_context, started_monotonic=started
        )
        if mem_fact is not None:
            try:
                from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

                mem_fact.trace.update(
                    self._final_behavior_trace_fields(
                        build_psyche_v2_behavior_context(turn.user_id)
                    )
                )
            except Exception as exc:
                logger.debug(
                    "memory_fact trace: psyche behavior fields skipped: %s", exc
                )
                mem_fact.trace.setdefault("final_behavior_profile", {})
                mem_fact.trace.setdefault("psyche_v2_style_mode", "neutral")
            mem_fact.trace.update(
                self._correction_trace_flat(correction_turn_trace, hints_chars=0)
            )
            append_event(
                turn.user_id,
                "chat.turn",
                {
                    "ok": True,
                    "provider": mem_fact.provider,
                    "model": mem_fact.model,
                    "trace": mem_fact.trace,
                    "tool_calls": [],
                    "tool_results": [],
                },
            )
            _TRACE_CACHE[turn.user_id].append(mem_fact.trace)
            return mem_fact

        psyche_snapshot = copy.deepcopy(
            get_psyche_core().ensure_user(turn.user_id) or {}
        )

        # V2 Bridge Snapshots (read-only foundation)
        memory_v2_snapshot: dict[str, Any] = {}
        psyche_v2_snapshot: dict[str, Any] = {}
        identity_bridge_snapshot = None
        memory_v2_runtime_ctx = None
        psyche_v2_behavior_ctx = None
        try:
            from aihub.runtime_identity_bridge import (
                build_identity_bridge_snapshot as build_identity_snapshot,
            )
            from aihub.runtime_memory_bridge import (
                build_memory_v2_runtime_context,
                build_memory_v2_runtime_snapshot,
            )
            from aihub.runtime_psyche_bridge import (
                build_psyche_v2_behavior_context,
                build_psyche_v2_runtime_snapshot,
            )

            memory_v2_snapshot = build_memory_v2_runtime_snapshot(
                turn.user_id, turn.message
            )
            psyche_v2_snapshot = build_psyche_v2_runtime_snapshot(turn.user_id)
            identity_bridge_snapshot = build_identity_snapshot(
                turn.user_id, turn.message
            )

            # Production runtime contexts for behavior injection
            memory_v2_runtime_ctx = build_memory_v2_runtime_context(
                turn.user_id, turn.message
            )
            psyche_v2_behavior_ctx = build_psyche_v2_behavior_context(turn.user_id)
        except Exception as bridge_error:
            logger.warning(f"Failed to load V2 bridges: {bridge_error}")

        try:
            if memory_v2_runtime_ctx is not None or psyche_v2_behavior_ctx is not None:
                from aihub.psyche_v2_repository import ensure_psyche_profile
                from aihub.runtime_psyche_bridge import apply_consistency_to_contexts

                _prof = ensure_psyche_profile(turn.user_id)
                memory_v2_runtime_ctx, psyche_v2_behavior_ctx, _consistency = (
                    apply_consistency_to_contexts(
                        memory_v2_runtime_ctx,
                        psyche_v2_behavior_ctx,
                        _prof.core_caution,
                    )
                )
        except Exception as consistency_error:
            logger.debug(
                "Self-consistency pass skipped: %s", consistency_error, exc_info=True
            )

        mem_truth = memory_truth_for_prompt(ctx.memory_context)
        memory_lookup_flag = bool(mem_truth["memory_retrieval_has_rows"])
        memory_substantive_flag = bool(mem_truth["memory_substantive_in_prompt"])
        include_stm_in_memory_brief = len(turn.history or []) == 0
        memory_brief = self._build_memory_brief(
            ctx.memory_context,
            include_stm=include_stm_in_memory_brief,
        )
        memory_used_trace = self._build_memory_used_trace(
            ctx.memory_context,
            include_stm=include_stm_in_memory_brief,
        )
        if stream_session_active():
            await emit_status("thinking", label_pl="Analizuję…")
            await emit_status("memory", label_pl="Sprawdzam kontekst…")
            mem_total = memory_results_count_for_trace(ctx.memory_context)
            if memory_lookup_flag and mem_total > 0:
                await emit_memory_used(count=mem_total)
        psyche_brief = self._build_psyche_brief(psyche_snapshot)
        tools = self._build_provider_tools(ctx)
        tool_results: list[ToolCallResult] = []
        tool_calls: list[ToolCallRequest] = []
        provider_usages: list[ProviderUsage] = []
        errors: list[dict[str, Any]] = []

        controlled_web: dict[str, Any] = {
            "triggered": False,
            "reason": "not_required",
            "tool_name": None,
            "ok": None,
            "has_results": None,
            "provider_info": None,
            "query": None,
            "source_count": 0,
            "freshness_needed": False,
        }

        # ── Decision Core (pre-execution): strategy, simulation, policy, consistency ──
        # Runs BEFORE web prefetch so that web_decision drives execution.
        decision_core = self._pre_exec_decision_core(
            turn=turn,
            ctx=ctx,
            psyche_snapshot=psyche_snapshot,
            memory_v2_runtime_ctx=memory_v2_runtime_ctx,
            psyche_v2_behavior_ctx=psyche_v2_behavior_ctx,
        )
        tools = self._apply_strategy_to_tools(tools, decision_core["selected_strategy"])
        if not decision_core.get("escalation_use_tools"):
            tools = []

        # ── Blocker Verdict Gate ────────────────────────────────────────────
        blocker_verdict = self._evaluate_blocker_verdict(decision_core)

        if blocker_verdict.hard:
            # Hard blocker: return early, NO provider call.
            duration_ms = (time.monotonic() - started) * 1000.0
            blocker_trace = {
                "provider_calls": 0,
                "tool_iterations": 0,
                "used_tools": False,
                "used_fallback": False,
                "response_grounding_mode": "blocker_hard_gate",
                "duration_ms": duration_ms,
                **self._correction_trace_fields(ctx),
                "selected_strategy": decision_core["selected_strategy"],
                **self._decision_core_trace_escalation(decision_core),
                "reason_codes": decision_core["reason_codes"] + ["BLOCKER_HARD_GATE"],
                "strategy_confidence": decision_core["strategy_confidence"],
                "degraded": decision_core.get("strategy_degraded", False),
                "memory_lookup_happened": memory_lookup_flag,
                "psyche_snapshot_happened": bool(psyche_snapshot),
                "research_was_required": False,
                "agentic_executed": False,
                "tool_calls_count": 0,
                "experience_write_back_attempted": False,
                "experience_write_back_succeeded": False,
                "blocker_verdict": blocker_verdict.model_dump(),
                # ── Controlled Web Orchestration V1 ──
                "controlled_web_decision": decision_core.get("web_decision", "off"),
                "controlled_web_decision_reason": decision_core.get(
                    "web_decision_reason", "not_evaluated"
                ),
                "controlled_web_triggered": False,
                "controlled_web_reason": "blocker_hard_gate",
                "controlled_web_tool": None,
                "controlled_web_ok": None,
                "controlled_web_has_results": None,
                "controlled_web_provider_info": None,
                "controlled_web_query": None,
                "controlled_web_source_count": 0,
                "controlled_web_freshness_needed": self._is_freshness_needed(
                    decision_core.get("reason_codes", [])
                ),
                "experience_lookup_happened": decision_core.get(
                    "experience_lookup_happened", False
                ),
                "experience_matches_count": decision_core.get(
                    "experience_matches_count", 0
                ),
                "experience_influenced_strategy": decision_core.get(
                    "experience_influenced_strategy", False
                ),
                "experience_blocker_reason": decision_core.get(
                    "experience_blocker_reason"
                ),
                "experience_signal_summary": decision_core.get(
                    "experience_signal_summary"
                ),
                "consistency_check_ran": decision_core["consistency_check_ran"],
                "consistency_classification": decision_core[
                    "consistency_classification"
                ],
                "contradictions_found": decision_core["contradictions_found"],
                "simulation_ran": decision_core["simulation_ran"],
                "simulation_best_action": decision_core["simulation_best_action"],
                "selected_goal": decision_core.get("selected_goal"),
                # ── Policy Feedback Loop trace fields ──
                "policy_feedback_loaded": bool(
                    decision_core.get("policy_feedback_loaded")
                ),
                "policy_feedback_applied": bool(
                    decision_core.get("policy_feedback_applied")
                ),
                "policy_feedback_summary": decision_core.get(
                    "policy_feedback_summary", ""
                ),
                "policy_confidence_delta": decision_core.get(
                    "policy_confidence_delta", 0.0
                ),
                "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
                "policy_blocker_sensitivity": decision_core.get(
                    "policy_blocker_sensitivity", 0.0
                ),
                "policy_simulation_risk_cal": decision_core.get(
                    "policy_simulation_risk_cal", 0.0
                ),
                "policy_strategy_adjustments": decision_core.get(
                    "policy_strategy_adjustments", {}
                ),
            }
            if memory_used_trace:
                blocker_trace["memory_used"] = memory_used_trace
            trace_blocker_gate_outcome(
                blocker_trace, gate_evaluated=True, hard_applied=True
            )
            blocker_trace["chat_handoff_evaluated"] = False
            _bt = blocker_verdict.blocker_type
            _bsrc = blocker_verdict.source or "unknown"
            merge_canonical_decision_trace(
                blocker_trace,
                selected_route=ROUTE_BLOCKED_HARD,
                route_reason=(
                    f"blocker_hard_gate|type={_bt}|source={_bsrc}|"
                    f"resolution={blocker_verdict.resolution}"
                ),
                decision_intent="blocked",
                deterministic_hit=False,
                vault_used=False,
                memory_retrieval_used=bool(memory_lookup_flag),
                web_required=str(decision_core.get("web_decision") or "off")
                == "required",
                planner_used=False,
                blocker_hard=True,
            )
            blocker_trace["memory_substantive_in_prompt"] = memory_substantive_flag
            blocker_trace["memory_stm_brief_included"] = include_stm_in_memory_brief
            augment_trace_context_truth(
                blocker_trace,
                mem_truth=memory_truth_for_prompt(ctx.memory_context),
                controlled_web={
                    "triggered": False,
                    "ok": None,
                    "has_results": None,
                },
                decision_core=decision_core,
                force_no_web_verified=True,
            )
            self._run_runtime_experience_feedback(turn.user_id, blocker_trace)
            append_event(
                turn.user_id,
                "chat.turn.blocked",
                {
                    "ok": False,
                    "blocker_type": blocker_verdict.blocker_type,
                    "blocker_reason": blocker_verdict.reason,
                    "blocker_source": blocker_verdict.source,
                    "blocker_resolution": blocker_verdict.resolution,
                    "user_message": blocker_verdict.user_message,
                    "trace": blocker_trace,
                },
            )
            result = ChatTurnResult(
                ok=False,
                response_text=blocker_verdict.user_message or blocker_verdict.reason,
                model="blocker_gate",
                provider="decision_core",
                tool_calls=[],
                tool_results=[],
                selected_mode=ctx.mode,
                usage=ProviderUsage(
                    prompt_tokens=0, completion_tokens=0, total_tokens=0
                ),
                trace=blocker_trace,
                errors=[
                    {
                        "type": "blocker_hard_gate",
                        "blocker_type": blocker_verdict.blocker_type,
                        "reason": blocker_verdict.reason,
                        "source": blocker_verdict.source,
                        "recommended_action": blocker_verdict.recommended_action,
                        "resolution": blocker_verdict.resolution,
                        "user_message": blocker_verdict.user_message,
                        "dev_message": blocker_verdict.dev_message,
                    }
                ],
            )
            _TRACE_CACHE[turn.user_id].append(result.trace)
            return result

        # ── Blocker Resolution: downgrade / reroute ─────────────────────────
        # These resolutions proceed with execution but modify the strategy.
        if blocker_verdict.blocker_active and blocker_verdict.resolution in (
            "downgrade",
            "reroute",
        ):
            new_strategy = blocker_verdict.next_best_action or "contextual"
            old_strategy = decision_core["selected_strategy"]
            if new_strategy != old_strategy:
                decision_core["selected_strategy"] = new_strategy
                decision_core["reason_codes"].append(
                    f"BLOCKER_{blocker_verdict.resolution.upper()}_"
                    f"{old_strategy.upper()}_TO_{new_strategy.upper()}"
                )
                logger.info(
                    "Blocker %s: strategy %s→%s for user=%s (type=%s)",
                    blocker_verdict.resolution,
                    old_strategy,
                    new_strategy,
                    turn.user_id,
                    blocker_verdict.blocker_type,
                )
                self._finalize_escalation(decision_core)
                tools = self._apply_strategy_to_tools(
                    self._build_provider_tools(ctx),
                    decision_core["selected_strategy"],
                )
                if not decision_core.get("escalation_use_tools"):
                    tools = []

        # ── Agent Handoff Gate ──────────────────────────────────────────────
        should_handoff, handoff_reason = self._should_handoff_to_agent(
            decision_core=decision_core,
            message=turn.message,
        )
        decision_core["chat_handoff_evaluated"] = True
        if should_handoff:
            decision_core.pop("chat_handoff_executed", None)
            decision_core.pop("chat_handoff_skip_reason", None)
        else:
            decision_core["chat_handoff_executed"] = False
            decision_core["chat_handoff_skip_reason"] = handoff_reason
        if should_handoff:
            if stream_session_active():
                await emit_status("tools", label_pl="Wykonuję kroki…")
            return await self._execute_agent_handoff(
                turn=turn,
                decision_core=decision_core,
                handoff_reason=handoff_reason,
                started=started,
                psyche_snapshot=psyche_snapshot,
                memory_used_trace=memory_used_trace,
                memory_lookup_flag=memory_lookup_flag,
                blocker_verdict=blocker_verdict,
                memory_context=ctx.memory_context,
                ctx=ctx,
            )

        # ── Controlled Web Prefetch (driven by web_decision) ───────────────
        # Runs AFTER decision_core and handoff gate, so web only fires for
        # the active chat path when strategy says it should.
        web_prefetch = await self._run_controlled_web_prefetch(
            turn=turn,
            ctx=ctx,
            web_decision=decision_core.get("web_decision", "off"),
        )
        if web_prefetch.get("triggered"):
            call_obj = web_prefetch.get("tool_call")
            result_obj = web_prefetch.get("tool_result")
            if isinstance(call_obj, ToolCallRequest):
                tool_calls.append(call_obj)
            if isinstance(result_obj, ToolCallResult):
                tool_results.append(result_obj)
                if not result_obj.ok:
                    errors.append(
                        {
                            "type": "controlled_web_error",
                            "error": result_obj.error or "unknown",
                            "tool": web_prefetch.get("tool_name"),
                        }
                    )
            controlled_web = {
                "triggered": True,
                "reason": web_prefetch.get("reason"),
                "tool_name": web_prefetch.get("tool_name"),
                "ok": result_obj.ok if isinstance(result_obj, ToolCallResult) else None,
                "has_results": self._assess_web_result_quality(result_obj),
                "provider_info": self._extract_web_provider_info(result_obj),
                "query": self._extract_web_query(
                    call_obj if isinstance(call_obj, ToolCallRequest) else None
                ),
                "source_count": self._count_web_sources(
                    result_obj if isinstance(result_obj, ToolCallResult) else None
                ),
                "freshness_needed": self._is_freshness_needed(
                    decision_core.get("reason_codes", [])
                ),
            }

        pre_messages = web_prefetch.get("messages") or []

        from aihub.chat_attachment_vision import enrich_image_attachments_for_turn

        effective_attached_ids = self._effective_attached_file_ids(turn)

        await enrich_image_attachments_for_turn(
            user_id=turn.user_id,
            session_id=turn.session_id,
            file_ids=list(effective_attached_ids),
        )

        attachment_block, attachment_meta = build_attachment_prompt_block(
            user_id=turn.user_id,
            session_id=turn.session_id,
            file_ids=list(effective_attached_ids),
        )
        attachments_summary = summarize_attachments_for_user(attachment_meta)

        first_turn_in_thread = len(turn.history or []) == 0
        history_rollup, hist_for_prompt = smart_clip_chat_history(turn.history)
        hist_smart_trim = {
            "chat_history_smart_trim_applied": bool(history_rollup),
            "chat_history_raw_tail_kept": len(hist_for_prompt),
            "chat_history_rollup_chars": len(history_rollup or ""),
        }
        user_llm_text, vault_user_redacted = sanitize_user_message_for_llm(turn.message)
        if (
            effective_attached_ids
            and int(attachment_meta.get("attachments_usable_count") or 0) == 0
        ):
            user_llm_text = (
                "[Priorytet: załączniki nie dostarczyły czytelnej treści do modelu. "
                "Odpowiedz krótko, co poszło nie tak (per plik), bez zgadywania treści "
                "ani formuł w stylu „może chodziło o…”.]\n\n" + user_llm_text
            )

        if self._web_required_grounding_unsatisfied(decision_core, controlled_web):
            return await self._finish_turn_web_required_ungrounded(
                turn=turn,
                ctx=ctx,
                started=started,
                decision_core=decision_core,
                blocker_verdict=blocker_verdict,
                controlled_web=controlled_web,
                tool_calls=tool_calls,
                tool_results=tool_results,
                errors=list(errors),
                memory_lookup_flag=memory_lookup_flag,
                memory_used_trace=memory_used_trace,
                include_stm_in_memory_brief=include_stm_in_memory_brief,
                psyche_snapshot=psyche_snapshot,
                attachment_meta=attachment_meta,
                attachments_summary=attachments_summary,
                hist_for_prompt_len=len(hist_for_prompt),
                vault_user_redacted=vault_user_redacted,
                hist_smart_trim=hist_smart_trim,
            )

        messages: list[ChatMessage] = [
            ChatMessage(
                role="system",
                content=self._build_system_prompt(
                    ctx,
                    memory_brief=memory_brief,
                    psyche_brief=psyche_brief,
                    decision_hints=decision_core["strategy_hints"],
                    correction_hints=str(
                        ctx.system_context.get("correction_hints_text") or ""
                    ),
                    memory_v2_context=memory_v2_runtime_ctx,
                    psyche_v2_context=psyche_v2_behavior_ctx,
                    files_context=attachment_block,
                    first_turn_in_thread=first_turn_in_thread,
                    history_rollup=history_rollup,
                    listing_sales_boost=listing_copy_no_web_intent(turn.message),
                ),
            ),
            *hist_for_prompt,
            *pre_messages,
            ChatMessage(role="user", content=user_llm_text),
        ]

        response_text = ""
        final_model = LLM_MODEL_NAME
        final_provider = self._current_provider_name()
        provider_call_count = 0
        usage_summary = self._sum_usage(provider_usages)

        for iteration in range(max(1, int(CHAT_MAX_TOOL_ITERATIONS)) + 1):
            provider_call_count += 1
            if stream_session_active():
                await emit_status("thinking", label_pl="Składam odpowiedź…")
            try:
                model_response = await self._provider_call(
                    messages=messages, tools=tools
                )
            except ProviderError as exc:
                errors.append({"type": "provider_error", **exc.to_dict()})
                fallback_text, fallback_trace = await self._provider_failure_fallback(
                    turn,
                    reason=exc.message,
                    decision_core=decision_core,
                )
                if (
                    str(decision_core.get("web_decision") or "off") == "required"
                    and not llm_path_verified_research_grounding(
                        web_grounding_in_prompt(controlled_web), tool_results
                    )
                ):
                    fallback_text = "BRAK DANYCH (web)"

                # Diagnostic context (raw memory/psyche brief) must stay OUT of the user-facing
                # fallback text — dumping internal state read as low-quality/personified output
                # (06.07 response-quality fix). Keep it available only in debug mode.
                if turn.include_debug:
                    if memory_lookup_flag:
                        fallback_text += f"\n\n[Kontekst pamięci] {memory_brief[:900]}"
                    if psyche_brief != "BRAK DANYCH":
                        fallback_text += f"\n[Kontekst psyche] {psyche_brief}"
                web_any = next(
                    (
                        result
                        for result in tool_results
                        if any(
                            k in (result.name or "").lower()
                            for k in ("web", "research")
                        )
                    ),
                    None,
                )
                if web_any is not None:
                    web_payload = (
                        self._safe_preview(web_any.output, max_chars=700)
                        if web_any.ok
                        else f"błąd wykonania: {web_any.error or 'BRAK DANYCH'}"
                    )
                    fallback_text += f"\n\n[Controlled web] {web_payload}"

                # ── Fallback path: reflection (fail-soft) ──
                fallback_reflection = self._post_exec_reflection(
                    user_id=turn.user_id,
                    message=turn.message,
                    response_text=fallback_text,
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    decision_core=decision_core,
                    blocker_verdict=blocker_verdict,
                    handoff_happened=False,
                )

                duration_ms = (time.monotonic() - started) * 1000.0
                usage_summary = self._sum_usage(provider_usages)
                trace = {
                    "provider_calls": provider_call_count,
                    "tool_iterations": iteration,
                    "fallback": fallback_trace,
                    "used_tools": len(tool_results) > 0,
                    "used_fallback": True,
                    "response_grounding_mode": "fallback",
                    "duration_ms": duration_ms,
                    **self._correction_trace_fields(ctx),
                    "provider": self._current_provider_name(),
                    "model": LLM_MODEL_NAME,
                    "usage_reporting_mode": usage_summary.reporting_mode,
                    "usage_total_tokens": usage_summary.total_tokens,
                    "selected_strategy": decision_core["selected_strategy"],
                    **self._decision_core_trace_escalation(decision_core),
                    "reason_codes": decision_core["reason_codes"],
                    "strategy_confidence": decision_core["strategy_confidence"],
                    "degraded": decision_core["strategy_degraded"],
                    "memory_lookup_happened": memory_lookup_flag,
                    "memory_results_count": memory_results_count_for_trace(
                        ctx.memory_context
                    ),
                    "psyche_snapshot_happened": False,
                    "research_was_required": self._has_research_tool(tool_calls),
                    "agentic_executed": False,
                    "tool_calls_count": len(tool_calls),
                    "experience_write_back_attempted": False,
                    "experience_write_back_succeeded": False,
                    # ── Controlled Web Orchestration V1 ──
                    "controlled_web_decision": decision_core.get("web_decision", "off"),
                    "controlled_web_decision_reason": decision_core.get(
                        "web_decision_reason", "not_evaluated"
                    ),
                    "controlled_web_triggered": bool(controlled_web.get("triggered")),
                    "controlled_web_reason": controlled_web.get("reason"),
                    "controlled_web_tool": controlled_web.get("tool_name"),
                    "controlled_web_ok": controlled_web.get("ok"),
                    "controlled_web_has_results": controlled_web.get("has_results"),
                    "controlled_web_provider_info": controlled_web.get("provider_info"),
                    "controlled_web_query": controlled_web.get("query"),
                    "controlled_web_source_count": controlled_web.get(
                        "source_count", 0
                    ),
                    "controlled_web_freshness_needed": controlled_web.get(
                        "freshness_needed", False
                    ),
                    "consistency_check_ran": decision_core["consistency_check_ran"],
                    "consistency_classification": decision_core[
                        "consistency_classification"
                    ],
                    # informational: count of detected contradictions, not execution-driving
                    "contradictions_found": decision_core["contradictions_found"],
                    "policy_hints_loaded": decision_core["policy_hints_loaded"],
                    "policy_profile_name": decision_core["policy_profile_name"],
                    "simulation_ran": decision_core["simulation_ran"],
                    "simulation_best_action": decision_core["simulation_best_action"],
                    "simulation_variants_count": decision_core[
                        "simulation_variants_count"
                    ],
                    # informational: human-readable risk string, not execution-driving
                    "simulation_risk_summary": decision_core["simulation_risk_summary"],
                    "experience_lookup_happened": decision_core.get(
                        "experience_lookup_happened", False
                    ),
                    "experience_matches_count": decision_core.get(
                        "experience_matches_count", 0
                    ),
                    "experience_influenced_strategy": decision_core.get(
                        "experience_influenced_strategy", False
                    ),
                    "experience_confidence_adjustment": decision_core.get(
                        "experience_confidence_adjustment"
                    ),
                    "experience_handoff_bias": decision_core.get(
                        "experience_handoff_bias"
                    ),
                    "experience_blocker_reason": decision_core.get(
                        "experience_blocker_reason"
                    ),
                    "experience_signal_summary": decision_core.get(
                        "experience_signal_summary"
                    ),
                    "reflection_ran": fallback_reflection["reflection_ran"],
                    "reflection_summary": fallback_reflection["reflection_summary"],
                    "selected_goal": decision_core.get("selected_goal"),
                    # ── Policy Feedback Loop trace fields ──
                    "policy_feedback_loaded": bool(
                        decision_core.get("policy_feedback_loaded")
                    ),
                    "policy_feedback_applied": bool(
                        decision_core.get("policy_feedback_applied")
                    ),
                    "policy_feedback_summary": decision_core.get(
                        "policy_feedback_summary", ""
                    ),
                    "policy_confidence_delta": decision_core.get(
                        "policy_confidence_delta", 0.0
                    ),
                    "policy_handoff_bias": decision_core.get(
                        "policy_handoff_bias", 0.0
                    ),
                    "policy_blocker_sensitivity": decision_core.get(
                        "policy_blocker_sensitivity", 0.0
                    ),
                    "policy_simulation_risk_cal": decision_core.get(
                        "policy_simulation_risk_cal", 0.0
                    ),
                    "policy_strategy_adjustments": decision_core.get(
                        "policy_strategy_adjustments", {}
                    ),
                    # ── Reflection hindsight fields ──
                    "reflection_strategy_fit": fallback_reflection.get(
                        "strategy_fit", "neutral"
                    ),
                    "reflection_handoff_hindsight": fallback_reflection.get(
                        "handoff_hindsight", "na"
                    ),
                    "reflection_blocker_hindsight": fallback_reflection.get(
                        "blocker_hindsight", "na"
                    ),
                    "reflection_confidence_hindsight": fallback_reflection.get(
                        "confidence_hindsight", 0.0
                    ),
                    "reflection_risk_hindsight": fallback_reflection.get(
                        "risk_hindsight", 0.0
                    ),
                    "attached_files": attachment_meta,
                    "attachments_summary": attachments_summary,
                    "blocker_verdict": blocker_verdict.model_dump(),
                }
                if memory_used_trace:
                    trace["memory_used"] = memory_used_trace
                self._augment_memory_observability(
                    trace, memory_used_trace, ctx.memory_context
                )
                trace_blocker_gate_outcome(
                    trace, gate_evaluated=True, hard_applied=False
                )
                merge_canonical_for_llm_path(
                    trace,
                    decision_core=decision_core,
                    grounding_mode="fallback",
                    memory_lookup_happened=memory_lookup_flag,
                    research_was_required=self._has_research_tool(tool_calls),
                    tool_calls=tool_calls,
                    web_verified_grounding_in_prompt=web_grounding_in_prompt(
                        controlled_web
                    ),
                    tool_results=tool_results,
                    used_fallback=True,
                    blocker_verdict_snapshot=blocker_verdict.model_dump(),
                )
                self._attach_web_observability_trace(
                    trace,
                    controlled_web=controlled_web,
                    tool_results=tool_results,
                    web_verified_in_prompt=web_grounding_in_prompt(controlled_web),
                )
                trace["memory_substantive_in_prompt"] = memory_substantive_flag
                trace.update(self._final_behavior_trace_fields(psyche_v2_behavior_ctx))
                trace.update({
                    "memory_v2_loaded": memory_v2_snapshot.get("loaded", False),
                    "memory_v2_match_count": memory_v2_snapshot.get("match_count", 0),
                    "memory_v2_reinforced_count": memory_v2_snapshot.get("reinforced_count", 0),
                    "memory_v2_suppressed_count": memory_v2_snapshot.get("suppressed_count", 0),
                    "memory_v2_contradictions_count": memory_v2_snapshot.get("contradictions_count", 0),
                    "memory_v2_actionable_contradictions_count": memory_v2_snapshot.get("actionable_contradictions_count", 0),
                    "memory_v2_transient_contradiction_count": memory_v2_snapshot.get("transient_contradiction_count", 0),
                    "memory_v2_procedures_count": memory_v2_snapshot.get("procedures_count", 0),
                    "memory_v2_top_reason_codes": memory_v2_snapshot.get("top_reason_codes", []),
                    "memory_v2_retrieval_explanation": memory_v2_snapshot.get("retrieval_strategy", ""),
                    "memory_v2_stability_tier_counts": (
                        dict(memory_v2_runtime_ctx.stability_tier_counts)
                        if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                        else {}
                    ),
                    "memory_v2_procedure_confidence_raw": (
                        memory_v2_runtime_ctx.confidence_modifier_raw
                        if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                        else 0.0
                    ),
                    "memory_v2_context_injected": bool(memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded),
                    "memory_v2_context_item_count": (
                        len(memory_v2_runtime_ctx.top_facts) + len(memory_v2_runtime_ctx.top_preferences)
                        if memory_v2_runtime_ctx
                        else 0
                    ),
                    "memory_v2_procedure_bias_applied": bool(
                        memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded and memory_v2_runtime_ctx.confidence_modifier > 0.6
                    ),
                    "memory_v2_contradiction_guard_applied": bool(
                        memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded and memory_v2_runtime_ctx.contradiction_alerts
                    ),
                    "psyche_v2_loaded": psyche_v2_snapshot.get("loaded", False),
                    "psyche_v2_mode": psyche_v2_snapshot.get("mode", "neutral"),
                    "psyche_v2_relation_trust": psyche_v2_snapshot.get("relation_trust", 0.5),
                    "psyche_v2_relation_friction": psyche_v2_snapshot.get("relation_friction", 0.0),
                    "psyche_v2_habit_biases": psyche_v2_snapshot.get("habit_biases", []),
                    "psyche_v2_behavior_style": psyche_v2_snapshot.get("behavior_policy", {}).get("directness", 0.5),
                    "psyche_v2_behavior_applied": bool(psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded),
                    "psyche_v2_style_mode": (
                        getattr(psyche_v2_behavior_ctx, "mode", "neutral")
                        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                        else "neutral"
                    ),
                    "psyche_v2_pressure_applied": bool(
                        psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded and getattr(psyche_v2_behavior_ctx, "pressure", 0.0) > 0.05
                    ),
                    "psyche_v2_relation_tone_applied": bool(
                        psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded and (getattr(psyche_v2_behavior_ctx, "warmth", 0.0) > 0.6 or getattr(psyche_v2_behavior_ctx, "friction", 0.0) > 0.4)
                    ),
                    "final_behavior_profile": (
                        {
                            "mode": getattr(psyche_v2_behavior_ctx, "mode", "neutral"),
                            "directness": psyche_v2_behavior_ctx.directness_bias,
                            "caution": psyche_v2_behavior_ctx.caution_bias,
                            "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                            "web_bias": psyche_v2_behavior_ctx.web_bias,
                            "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
                        }
                        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                        else {}
                    ),
                    "memory_v2_writeback_attempted": False,
                    "memory_v2_writeback_succeeded": False,
                    "memory_v2_new_items_count": 0,
                    "memory_v2_new_lessons_count": 0,
                    "psyche_v2_writeback_attempted": False,
                    "psyche_v2_writeback_succeeded": False,
                    "psyche_v2_event_applied": None,
                    "response_outcome_quality": "fallback",
                })
                trace["memory_stm_brief_included"] = include_stm_in_memory_brief
                trace["context_history_messages_attached"] = len(hist_for_prompt)
                trace["vault_user_message_redacted"] = vault_user_redacted
                trace.update(hist_smart_trim)
                augment_trace_context_truth(
                    trace,
                    mem_truth=mem_truth,
                    controlled_web=controlled_web,
                    decision_core=decision_core,
                )
                self._write_back_experience(
                    turn=turn,
                    response_text=fallback_text,
                    grounding_mode="fallback",
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    trace=trace,
                    errors=errors,
                    psyche_snapshot=psyche_snapshot,
                    decision_core=decision_core,
                )
                if str(turn.user_id).startswith("audit_"):
                    trace["psyche_snapshot_happened"] = False
                    trace["experience_write_back_attempted"] = False
                    trace["experience_write_back_succeeded"] = False
                self._run_runtime_experience_feedback(turn.user_id, trace)
                append_event(
                    turn.user_id,
                    "chat.turn",
                    {
                        "ok": False,
                        "provider": self._current_provider_name(),
                        "model": LLM_MODEL_NAME,
                        "errors": errors,
                        "trace": trace,
                    },
                )
                result = ChatTurnResult(
                    ok=False,
                    response_text=fallback_text,
                    model=LLM_MODEL_NAME,
                    provider=self._current_provider_name(),
                    tool_calls=tool_calls,
                    tool_results=tool_results,
                    selected_mode=ctx.mode,
                    usage=self._sum_usage(provider_usages),
                    trace=trace,
                    errors=errors,
                    debug={"context": ctx.model_dump()} if turn.include_debug else None,
                    attachments_summary=attachments_summary,
                )
                _TRACE_CACHE[turn.user_id].append(result.trace)
                return result

            final_model = model_response.model
            final_provider = model_response.provider
            provider_usages.append(model_response.usage)
            usage_summary = self._sum_usage(provider_usages)

            if model_response.tool_calls and iteration < max(
                1, int(CHAT_MAX_TOOL_ITERATIONS)
            ):
                messages.append(
                    ChatMessage(
                        role="assistant",
                        content=model_response.content,
                        tool_calls=model_response.tool_calls,
                    )
                )

                exec_ctx = ToolExecutionContext(
                    user_id=turn.user_id,
                    session_id=turn.session_id,
                    mode=ctx.mode,
                    include_debug=turn.include_debug,
                    policy_overrides=dict(turn.tool_policy_overrides or {}),
                )

                if stream_session_active():
                    await emit_status("tools", label_pl="Wykonuję kroki…")
                for call in model_response.tool_calls:
                    tool_calls.append(call)
                    tlabel = self._sse_tool_display_name(call.name)
                    if stream_session_active():
                        await emit_tool_event(tlabel, "start")
                    res = await self._tool_router.execute(call, exec_ctx)
                    if stream_session_active():
                        await emit_tool_event(tlabel, "done")
                    tool_results.append(res)
                    tool_payload = {
                        "ok": res.ok,
                        "output": res.output,
                        "error": res.error,
                    }
                    messages.append(
                        ChatMessage(
                            role="tool",
                            name=call.name,
                            tool_call_id=call.tool_call_id,
                            content=json.dumps(tool_payload, ensure_ascii=False),
                        )
                    )
                continue

            response_text = model_response.content or ""
            break

        if not response_text and tool_results:
            ok_results = [r for r in tool_results if r.ok]
            if ok_results:
                response_text = (
                    self._build_controlled_web_synthesis(
                        controlled_web=controlled_web,
                        tool_results=tool_results,
                    )
                    or "Narzędzia poszły, wyniki są — powiedz, jak je ułożyć w odpowiedź."
                )
            else:
                response_text = (
                    "Narzędzia w tej turze się potknęły — doprecyzuj, co dokładnie odpalić, "
                    "albo spróbuj jeszcze raz bez dramatu."
                )

        grounding_mode = self._classify_grounding_mode(
            used_fallback=False,
            tool_calls=tool_calls,
            tool_results=tool_results,
        )

        # ── Response Variants Deliberation ────────────────────────────
        # Conditionally generates up to 3 structurally distinct response
        # candidates, evaluates them, and synthesizes the final response.
        # Triggered only when the decision core signals uncertainty.
        deliberation_metadata: dict[str, Any] = {}
        if stream_session_active():
            await emit_status("finalizing", label_pl="Kończę odpowiedź…")
        try:
            # Build original messages as plain dicts for the engine
            original_msgs = [
                {
                    "role": m.role,
                    "content": m.content,
                    "name": m.name,
                    "tool_call_id": m.tool_call_id,
                }
                for m in messages
            ]
            (
                deliberated_text,
                deliberation_metadata,
            ) = await ResponseVariantsEngine.run_deliberation(
                decision_core=decision_core,
                blocker_verdict=blocker_verdict,
                original_response=response_text,
                original_messages=original_msgs,
                provider_call_fn=self._provider_call,
                deliberation_history=decision_core.get("deliberation_history"),
            )
            if deliberation_metadata.get("response_variants_triggered"):
                response_text = deliberated_text
                logger.info(
                    "Deliberation replaced response_text: winner=%s confidence=%.2f",
                    deliberation_metadata.get("response_variants_winner_type", "?"),
                    deliberation_metadata.get("response_variants_confidence", 0.0),
                )
        except Exception:
            logger.warning(
                "Deliberation engine failed — using original response", exc_info=True
            )
            deliberation_metadata = {
                "response_variants_triggered": False,
                "response_variants_count": 0,
                "response_variants_reason_codes": [],
                "response_variants_error": True,
            }

        anti_hallucination_trace: dict[str, Any] = {}
        response_text = self._shape_response_text(
            turn=turn,
            ctx=ctx,
            response_text=response_text,
            grounding_mode=grounding_mode,
            used_fallback=False,
            memory_v2_context=memory_v2_runtime_ctx,
            psyche_v2_context=psyche_v2_behavior_ctx,
            anti_hallucination_trace=anti_hallucination_trace,
        )

        # ── Decision Core (post-execution): reflection on completed turn ──
        # Merge deliberation metadata into decision_core so _compute_deliberation_hindsight
        # sees the actual trigger/confidence/risk/winner data from this turn.
        for _dk in (
            "response_variants_triggered",
            "response_variants_confidence",
            "response_variants_risk",
            "response_variants_synthesis_used",
            "response_variants_winner_type",
        ):
            if _dk in deliberation_metadata:
                decision_core[_dk] = deliberation_metadata[_dk]
        # Compute and attach deliberation outcome quality for hindsight
        decision_core["deliberation_outcome_quality"] = (
            self._compute_deliberation_outcome_quality(deliberation_metadata)
        )

        post_reflection = self._post_exec_reflection(
            user_id=turn.user_id,
            message=turn.message,
            response_text=response_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            decision_core=decision_core,
            blocker_verdict=blocker_verdict,
            handoff_happened=False,
        )

        # Drugi przebieg kształtowania + refleksji na finalnym tekście (zachowanie produkcyjne;
        # pierwsza refleksja widzi wynik po 1. shape, druga — po ewentualnej korekcie stylu/guardów).
        response_text = self._shape_response_text(
            turn=turn,
            ctx=ctx,
            response_text=response_text,
            grounding_mode=grounding_mode,
            used_fallback=False,
            memory_v2_context=memory_v2_runtime_ctx,
            psyche_v2_context=psyche_v2_behavior_ctx,
            anti_hallucination_trace=anti_hallucination_trace,
        )
        for _dk in (
            "response_variants_triggered",
            "response_variants_confidence",
            "response_variants_risk",
            "response_variants_synthesis_used",
            "response_variants_winner_type",
        ):
            if _dk in deliberation_metadata:
                decision_core[_dk] = deliberation_metadata[_dk]
        decision_core["deliberation_outcome_quality"] = (
            self._compute_deliberation_outcome_quality(deliberation_metadata)
        )
        post_reflection = self._post_exec_reflection(
            user_id=turn.user_id,
            message=turn.message,
            response_text=response_text,
            tool_calls=tool_calls,
            tool_results=tool_results,
            decision_core=decision_core,
            blocker_verdict=blocker_verdict,
            handoff_happened=False,
        )

        duration_ms = (time.monotonic() - started) * 1000.0
        research_required = self._has_research_tool(tool_calls)
        usage_summary = self._sum_usage(provider_usages)

        # Build behavior injection trace
        memory_v2_context_injected = bool(
            memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
        )
        memory_v2_context_item_count = (
            len(memory_v2_runtime_ctx.top_facts)
            + len(memory_v2_runtime_ctx.top_preferences)
            if memory_v2_runtime_ctx
            else 0
        )
        memory_v2_procedure_bias_applied = bool(
            memory_v2_runtime_ctx
            and memory_v2_runtime_ctx.loaded
            and memory_v2_runtime_ctx.confidence_modifier > 0.6
        )
        memory_v2_contradiction_guard_applied = bool(
            memory_v2_runtime_ctx
            and memory_v2_runtime_ctx.loaded
            and memory_v2_runtime_ctx.contradiction_alerts
            and psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.caution_bias
            > 0.5  # Lowered from 0.6 for real triggering
        )

        psyche_v2_behavior_applied = bool(
            psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
        )
        psyche_v2_style_mode = (
            psyche_v2_behavior_ctx.mode if psyche_v2_behavior_ctx else "neutral"
        )
        psyche_v2_pressure_applied = bool(
            psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.loaded
            and psyche_v2_behavior_ctx.pressure > 0.5
        )
        psyche_v2_relation_tone_applied = bool(
            psyche_v2_behavior_ctx
            and psyche_v2_behavior_ctx.loaded
            and (
                psyche_v2_behavior_ctx.friction > 0.5
                or psyche_v2_behavior_ctx.warmth > 0.7
            )
        )

        final_behavior_profile = {}
        if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded:
            final_behavior_profile = {
                "mode": psyche_v2_style_mode,
                "directness": psyche_v2_behavior_ctx.directness_bias,
                "verbosity": psyche_v2_behavior_ctx.verbosity_bias,
                "caution": psyche_v2_behavior_ctx.caution_bias,
                "pressure": psyche_v2_behavior_ctx.pressure,
                "trust": psyche_v2_behavior_ctx.trust,
                "friction": psyche_v2_behavior_ctx.friction,
                "warmth": psyche_v2_behavior_ctx.warmth,
                "autonomy": psyche_v2_behavior_ctx.autonomy_bias,
                "structuredness": psyche_v2_behavior_ctx.structuredness_bias,
                "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                "web_bias": psyche_v2_behavior_ctx.web_bias,
                "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
            }

        trace = {
            "provider_calls": provider_call_count,
            "tool_iterations": min(
                provider_call_count, max(1, int(CHAT_MAX_TOOL_ITERATIONS))
            ),
            "tool_calls_requested": len(tool_calls),
            "tool_calls_executed": len(tool_results),
            "tool_calls_successful": len([r for r in tool_results if r.ok]),
            "tool_failures": len([r for r in tool_results if not r.ok]),
            "used_tools": len(tool_results) > 0,
            "used_fallback": False,
            **self._correction_trace_fields(ctx),
            "anti_hallucination_clamp_applied": bool(
                anti_hallucination_trace.get("applied")
            ),
            "anti_hallucination_clamp_reason": anti_hallucination_trace.get("reason"),
            "response_grounding_mode": grounding_mode,
            "chat_thread_first_turn": first_turn_in_thread,
            "chat_history_message_count": len(turn.history or []),
            **build_history_trace(turn),
            "duration_ms": duration_ms,
            "provider": final_provider,
            "model": final_model,
            "usage_reporting_mode": usage_summary.reporting_mode,
            "usage_total_tokens": usage_summary.total_tokens,
            # ── Decision Core trace fields ──
            "selected_strategy": decision_core["selected_strategy"],
            **self._decision_core_trace_escalation(decision_core),
            "reason_codes": decision_core["reason_codes"],
            "strategy_confidence": decision_core["strategy_confidence"],
            "degraded": decision_core["strategy_degraded"],
            "memory_lookup_happened": memory_lookup_flag,
            "memory_results_count": memory_results_count_for_trace(ctx.memory_context),
            "psyche_snapshot_happened": False,
            "research_was_required": research_required,
            "agentic_executed": False,
            "tool_calls_count": len(tool_calls),
            "experience_write_back_attempted": False,
            "experience_write_back_succeeded": False,
            # ── Controlled Web Orchestration V1 ──
            "controlled_web_decision": decision_core.get("web_decision", "off"),
            "controlled_web_decision_reason": decision_core.get(
                "web_decision_reason", "not_evaluated"
            ),
            "controlled_web_triggered": bool(controlled_web.get("triggered")),
            "controlled_web_reason": controlled_web.get("reason"),
            "controlled_web_tool": controlled_web.get("tool_name"),
            "controlled_web_ok": controlled_web.get("ok"),
            "controlled_web_has_results": controlled_web.get("has_results"),
            "controlled_web_provider_info": controlled_web.get("provider_info"),
            "controlled_web_query": controlled_web.get("query"),
            "controlled_web_source_count": controlled_web.get("source_count", 0),
            "controlled_web_freshness_needed": controlled_web.get(
                "freshness_needed", False
            ),
            **self._web_stage_trace_fields(
                decision_core, controlled_web, explicit_fail_applied=False
            ),
            # Decision Core: consistency / policy / simulation / reflection
            "consistency_check_ran": decision_core["consistency_check_ran"],
            "consistency_classification": decision_core["consistency_classification"],
            # informational: count of detected contradictions, not execution-driving
            "contradictions_found": decision_core["contradictions_found"],
            "policy_hints_loaded": decision_core["policy_hints_loaded"],
            "policy_profile_name": decision_core["policy_profile_name"],
            "simulation_ran": decision_core["simulation_ran"],
            "simulation_best_action": decision_core["simulation_best_action"],
            "simulation_variants_count": decision_core["simulation_variants_count"],
            # informational: human-readable risk string, not execution-driving
            "simulation_risk_summary": decision_core["simulation_risk_summary"],
            "experience_lookup_happened": decision_core.get(
                "experience_lookup_happened", False
            ),
            "experience_matches_count": decision_core.get(
                "experience_matches_count", 0
            ),
            "experience_influenced_strategy": decision_core.get(
                "experience_influenced_strategy", False
            ),
            "experience_confidence_adjustment": decision_core.get(
                "experience_confidence_adjustment"
            ),
            "experience_handoff_bias": decision_core.get("experience_handoff_bias"),
            "experience_blocker_reason": decision_core.get("experience_blocker_reason"),
            "experience_signal_summary": decision_core.get("experience_signal_summary"),
            "reflection_ran": post_reflection["reflection_ran"],
            "reflection_summary": post_reflection["reflection_summary"],
            "selected_goal": decision_core.get("selected_goal"),
            # ── Policy Feedback Loop trace fields ──
            "policy_feedback_loaded": bool(decision_core.get("policy_feedback_loaded")),
            "policy_feedback_applied": bool(
                decision_core.get("policy_feedback_applied")
            ),
            "policy_feedback_summary": decision_core.get("policy_feedback_summary", ""),
            "policy_confidence_delta": decision_core.get(
                "policy_confidence_delta", 0.0
            ),
            "policy_handoff_bias": decision_core.get("policy_handoff_bias", 0.0),
            "policy_blocker_sensitivity": decision_core.get(
                "policy_blocker_sensitivity", 0.0
            ),
            "policy_simulation_risk_cal": decision_core.get(
                "policy_simulation_risk_cal", 0.0
            ),
            "policy_strategy_adjustments": decision_core.get(
                "policy_strategy_adjustments", {}
            ),
            # ── Reflection hindsight fields ──
            "reflection_strategy_fit": post_reflection.get("strategy_fit", "neutral"),
            "reflection_handoff_hindsight": post_reflection.get(
                "handoff_hindsight", "na"
            ),
            "reflection_blocker_hindsight": post_reflection.get(
                "blocker_hindsight", "na"
            ),
            # ── Memory V2 + Psyche V2 + Identity Bridge (foundation) ──
            "memory_v2_loaded": memory_v2_snapshot.get("loaded", False),
            "memory_v2_match_count": memory_v2_snapshot.get("match_count", 0),
            "memory_v2_reinforced_count": memory_v2_snapshot.get("reinforced_count", 0),
            "memory_v2_suppressed_count": memory_v2_snapshot.get("suppressed_count", 0),
            "memory_v2_contradictions_count": memory_v2_snapshot.get(
                "contradictions_count", 0
            ),
            "memory_v2_actionable_contradictions_count": memory_v2_snapshot.get(
                "actionable_contradictions_count", 0
            ),
            "memory_v2_transient_contradiction_count": memory_v2_snapshot.get(
                "transient_contradiction_count", 0
            ),
            "memory_v2_stability_tier_counts": (
                dict(memory_v2_runtime_ctx.stability_tier_counts)
                if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                else {}
            ),
            "memory_v2_procedure_confidence_raw": (
                memory_v2_runtime_ctx.confidence_modifier_raw
                if memory_v2_runtime_ctx and memory_v2_runtime_ctx.loaded
                else 0.0
            ),
            "self_consistency_decision": (
                psyche_v2_behavior_ctx.consistency_decision
                if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                else "allow"
            ),
            "self_consistency_reasons": (
                list(psyche_v2_behavior_ctx.consistency_reasons)
                if psyche_v2_behavior_ctx and psyche_v2_behavior_ctx.loaded
                else []
            ),
            "memory_v2_procedures_count": memory_v2_snapshot.get("procedures_count", 0),
            "memory_v2_top_reason_codes": memory_v2_snapshot.get(
                "top_reason_codes", []
            ),
            "memory_v2_retrieval_explanation": memory_v2_snapshot.get(
                "retrieval_strategy", ""
            ),
            "psyche_v2_loaded": psyche_v2_snapshot.get("loaded", False),
            "psyche_v2_mode": psyche_v2_snapshot.get("mode", "neutral"),
            "psyche_v2_relation_trust": psyche_v2_snapshot.get("relation_trust", 0.5),
            "psyche_v2_relation_friction": psyche_v2_snapshot.get(
                "relation_friction", 0.0
            ),
            "psyche_v2_habit_biases": psyche_v2_snapshot.get("habit_biases", []),
            "psyche_v2_behavior_style": psyche_v2_snapshot.get(
                "behavior_policy", {}
            ).get("directness", 0.5),
            "identity_bridge_loaded": identity_bridge_snapshot is not None,
            # V2 Real Influence (decision impact)
            "memory_influenced_strategy": decision_core.get(
                "memory_influenced_strategy_chat", False
            ),
            "psyche_influenced_strategy": decision_core.get(
                "psyche_influenced_strategy_chat", False
            ),
            # ── V2 Behavior Injection (real runtime influence) ──
            "memory_v2_context_injected": memory_v2_context_injected,
            "memory_v2_context_item_count": memory_v2_context_item_count,
            "memory_v2_procedure_bias_applied": memory_v2_procedure_bias_applied,
            "memory_v2_contradiction_guard_applied": memory_v2_contradiction_guard_applied,
            "psyche_v2_behavior_applied": psyche_v2_behavior_applied,
            "psyche_v2_style_mode": psyche_v2_style_mode,
            "psyche_v2_pressure_applied": psyche_v2_pressure_applied,
            "psyche_v2_relation_tone_applied": psyche_v2_relation_tone_applied,
            "final_behavior_profile": final_behavior_profile,
            "reflection_confidence_hindsight": post_reflection.get(
                "confidence_hindsight", 0.0
            ),
            "reflection_risk_hindsight": post_reflection.get("risk_hindsight", 0.0),
            "reflection_deliberation_hindsight": post_reflection.get(
                "deliberation_hindsight", {}
            ),
            "attached_files": attachment_meta,
            "attachments_summary": attachments_summary,
            "blocker_verdict": blocker_verdict.model_dump(),
            # ── Response Variants Deliberation trace fields ──
            "response_variants_triggered": deliberation_metadata.get(
                "response_variants_triggered", False
            ),
            "response_variants_count": deliberation_metadata.get(
                "response_variants_count", 0
            ),
            "response_variants_reason_codes": deliberation_metadata.get(
                "response_variants_reason_codes", []
            ),
            "response_variants_winner": deliberation_metadata.get(
                "response_variants_winner"
            ),
            "response_variants_winner_type": deliberation_metadata.get(
                "response_variants_winner_type"
            ),
            "response_variants_synthesis_used": deliberation_metadata.get(
                "response_variants_synthesis_used", []
            ),
            "response_variants_dropped": deliberation_metadata.get(
                "response_variants_dropped", []
            ),
            "response_variants_confidence": deliberation_metadata.get(
                "response_variants_confidence"
            ),
            "response_variants_risk": deliberation_metadata.get(
                "response_variants_risk"
            ),
            "response_variants_summary": deliberation_metadata.get(
                "response_variants_summary"
            ),
            "response_variants_duration_ms": deliberation_metadata.get(
                "response_variants_duration_ms"
            ),
            "response_variants_scores": deliberation_metadata.get(
                "response_variants_scores", []
            ),
            "response_variants_error": deliberation_metadata.get(
                "response_variants_error", False
            ),
        }

        if memory_used_trace:
            trace["memory_used"] = memory_used_trace
        self._augment_memory_observability(trace, memory_used_trace, ctx.memory_context)

        trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
        merge_canonical_for_llm_path(
            trace,
            decision_core=decision_core,
            grounding_mode=grounding_mode,
            memory_lookup_happened=memory_lookup_flag,
            research_was_required=research_required,
            tool_calls=tool_calls,
            web_verified_grounding_in_prompt=web_grounding_in_prompt(controlled_web),
            tool_results=tool_results,
            used_fallback=False,
            blocker_verdict_snapshot=blocker_verdict.model_dump(),
        )
        self._attach_web_observability_trace(
            trace,
            controlled_web=controlled_web,
            tool_results=tool_results,
            web_verified_in_prompt=web_grounding_in_prompt(controlled_web),
        )
        trace["memory_substantive_in_prompt"] = memory_substantive_flag
        trace["memory_stm_brief_included"] = include_stm_in_memory_brief
        trace["context_history_messages_attached"] = len(hist_for_prompt)
        trace["vault_user_message_redacted"] = vault_user_redacted
        trace.update(hist_smart_trim)
        augment_trace_context_truth(
            trace,
            mem_truth=mem_truth,
            controlled_web=controlled_web,
            decision_core=decision_core,
        )

        # ── V2 POST-RESPONSE WRITE-BACK: outcome → Memory V2 + Psyche V2 ──
        trace["memory_v2_writeback_attempted"] = False
        trace["memory_v2_writeback_succeeded"] = False
        trace["memory_v2_new_items_count"] = 0
        trace["memory_v2_new_lessons_count"] = 0
        trace["psyche_v2_writeback_attempted"] = False
        trace["psyche_v2_writeback_succeeded"] = False
        trace["psyche_v2_event_applied"] = None
        trace["response_outcome_quality"] = "success"

        if not str(turn.user_id).startswith("audit_"):
            try:
                from aihub.memory_core import get_memory_core

                _psy_svc = get_psyche_core().v2_service

                # Determine outcome quality
                degraded = bool(decision_core.get("strategy_degraded", False))
                fallback = False
                if len(errors) > 0:
                    trace["response_outcome_quality"] = (
                        "blocked"
                        if any(e.get("blocker", False) for e in errors)
                        else "fallback"
                    )
                    fallback = True
                elif degraded:
                    trace["response_outcome_quality"] = "degraded"
                elif not response_text or len(response_text.strip()) < 10:
                    trace["response_outcome_quality"] = "fallback"
                    fallback = True

                # Memory V2 write-back
                turn_id_for_wb = str(uuid.uuid4())
                memory_wb = get_memory_core().record_chat_outcome(
                    user_id=turn.user_id,
                    turn_id=turn_id_for_wb,
                    query_text=turn.message or "",
                    response_text=response_text,
                    strategy=decision_core["selected_strategy"],
                    grounding_mode=grounding_mode,
                    tool_calls_count=len(tool_calls),
                    tool_successes=len([r for r in tool_results if r.ok]),
                    tool_failures=len([r for r in tool_results if not r.ok]),
                    contradictions_present=memory_v2_snapshot.get(
                        "contradictions_count", 0
                    ),
                    memory_matches=memory_v2_snapshot.get("match_count", 0),
                    degraded=degraded,
                    fallback=fallback,
                )
                trace["memory_v2_writeback_attempted"] = memory_wb.get(
                    "attempted", False
                )
                trace["memory_v2_writeback_succeeded"] = memory_wb.get(
                    "succeeded", False
                )
                trace["memory_v2_new_items_count"] = memory_wb.get("new_items_count", 0)
                trace["memory_v2_new_lessons_count"] = memory_wb.get(
                    "new_lessons_count", 0
                )

                # Psyche V2 write-back
                outcome_kind = trace["response_outcome_quality"]
                psyche_wb = _psy_svc.apply_outcome_event(
                    user_id=turn.user_id,
                    outcome_kind=(
                        outcome_kind if outcome_kind != "blocked" else "failure"
                    ),
                    source_ref=turn_id_for_wb,
                    context={
                        "contradictions_present": memory_v2_snapshot.get(
                            "contradictions_count", 0
                        ),
                        "grounding_mode": grounding_mode,
                        "tool_calls_count": len(tool_calls),
                    },
                )
                trace["psyche_v2_writeback_attempted"] = psyche_wb.get(
                    "attempted", False
                )
                trace["psyche_v2_writeback_succeeded"] = psyche_wb.get(
                    "succeeded", False
                )
                trace["psyche_v2_event_applied"] = psyche_wb.get("event_applied")

                logger.info(
                    f"V2 chat write-back: memory={memory_wb.get('succeeded')} psyche={psyche_wb.get('succeeded')} user={turn.user_id}"
                )

            except Exception as v2_wb_error:
                logger.warning(
                    f"V2 chat write-back failed: {v2_wb_error}", exc_info=True
                )
                trace["memory_v2_writeback_attempted"] = True
                trace["psyche_v2_writeback_attempted"] = True

        self._write_back_experience(
            turn=turn,
            response_text=response_text,
            grounding_mode=grounding_mode,
            tool_calls=tool_calls,
            tool_results=tool_results,
            trace=trace,
            errors=errors,
            psyche_snapshot=psyche_snapshot,
            decision_core=decision_core,
        )

        if str(turn.user_id).startswith("audit_"):
            trace["psyche_snapshot_happened"] = False
            trace["experience_write_back_attempted"] = False
            trace["experience_write_back_succeeded"] = False

        self._run_runtime_experience_feedback(turn.user_id, trace)

        append_event(
            turn.user_id,
            "chat.turn",
            {
                "ok": len(errors) == 0,
                "provider": final_provider,
                "model": final_model,
                "trace": trace,
                "tool_calls": [tc.model_dump() for tc in tool_calls],
                "tool_results": [tr.model_dump() for tr in tool_results],
            },
        )

        result = ChatTurnResult(
            ok=len(errors) == 0,
            response_text=response_text,
            model=final_model,
            provider=final_provider,
            tool_calls=tool_calls,
            tool_results=tool_results,
            selected_mode=ctx.mode,
            usage=self._sum_usage(provider_usages),
            trace=trace,
            errors=errors,
            debug={"context": ctx.model_dump()} if turn.include_debug else None,
            attachments_summary=attachments_summary,
        )
        _TRACE_CACHE[turn.user_id].append(result.trace)

        return result


_RUNTIME: ChatRuntime | None = None


def get_chat_runtime() -> ChatRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = ChatRuntime()
    else:
        try:
            fresh_provider = get_default_provider()
        except Exception:
            fresh_provider = None
        if fresh_provider is not None:
            current = getattr(_RUNTIME, "_provider", None)
            current_key = (type(current), getattr(current, "provider_name", None), getattr(current, "name", None))
            fresh_key = (type(fresh_provider), getattr(fresh_provider, "provider_name", None), getattr(fresh_provider, "name", None))
            if current is None or current_key != fresh_key:
                _RUNTIME._provider = fresh_provider
    return _RUNTIME


def get_cached_chat_traces(user_id: str, limit: int = 5) -> list[dict[str, Any]]:
    traces = list(_TRACE_CACHE.get(user_id, []))
    return traces[-max(1, int(limit)) :]
