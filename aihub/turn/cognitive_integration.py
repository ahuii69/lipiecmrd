"""Cognitive Integration V2 — one coherent mind across modules.

Builds ConversationState + UserModel, packs cross-module influence into
prompt / strategy / critic / research / calibration. Persistence via event_log
(durable per user_id). No public API changes.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from aihub.db import append_event, fetch_recent_events_by_type

log = logging.getLogger(__name__)

CONV_STATE_EVENT = "cognitive.conversation_state"
USER_MODEL_EVENT = "cognitive.user_model"
CALIBRATION_EVENT = "cognitive.calibration"

PreferredLength = Literal["short", "medium", "long"]
PreferredStructure = Literal["free", "bullets", "steps", "sections"]


class IntentRankItem(BaseModel):
    label: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""
    ambiguity_contribution: float = Field(default=0.0, ge=0.0, le=1.0)


class UserPreferenceModel(BaseModel):
    preferred_tone: str = "natural"
    preferred_detail_level: float = Field(default=0.55, ge=0.0, le=1.0)
    preferred_answer_length: PreferredLength = "medium"
    preferred_humour: float = Field(default=0.45, ge=0.0, le=1.0)
    preferred_technical_depth: float = Field(default=0.55, ge=0.0, le=1.0)
    preferred_structure: PreferredStructure = "free"
    preferred_correction_style: str = "direct"
    preferred_planning_style: str = "light"
    preferred_examples: float = Field(default=0.4, ge=0.0, le=1.0)
    preferred_interaction_style: str = "partner"
    confidence: float = Field(default=0.35, ge=0.0, le=1.0)
    sample_count: int = 0
    updated_at: float = 0.0


class ConversationStateModel(BaseModel):
    session_id: str = ""
    primary_topic: str = ""
    side_topics: list[str] = Field(default_factory=list)
    decided: list[str] = Field(default_factory=list)
    plan_steps: list[str] = Field(default_factory=list)
    executed: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    active_refs: list[str] = Field(default_factory=list)
    expired_refs: list[str] = Field(default_factory=list)
    last_intent: str = ""
    last_speech_act: str = ""
    turn_count: int = 0
    open_questions: list[str] = Field(default_factory=list)
    updated_at: float = 0.0


class CognitiveInfluencePack(BaseModel):
    conversation: ConversationStateModel = Field(default_factory=ConversationStateModel)
    user_model: UserPreferenceModel = Field(default_factory=UserPreferenceModel)
    intent_ranking: list[IntentRankItem] = Field(default_factory=list)
    primary_intent: str = "unknown"
    intent_confidence: float = 0.5
    ambiguity: float = 0.0
    memory_influence_reason: str = ""
    psyche_influence_reason: str = ""
    identity_influence_reason: str = ""
    goals_influence_reason: str = ""
    planner_influence_reason: str = ""
    experience_influence_reason: str = ""
    correction_influence_reason: str = ""
    reflection_influence_reason: str = ""
    style_directives: list[str] = Field(default_factory=list)
    length_directive: str = ""
    strategy_bias_codes: list[str] = Field(default_factory=list)
    force_reasoning: bool = False
    force_planner_analysis: bool = False
    research_query_variants: list[str] = Field(default_factory=list)
    research_query_scores: list[dict[str, Any]] = Field(default_factory=list)
    tool_order_hint: list[str] = Field(default_factory=list)
    influence_reason_codes: list[str] = Field(default_factory=list)
    # World knowledge / Evidence KG (bounded — no full graph dump)
    relevant_claims: list[dict[str, Any]] = Field(default_factory=list)
    relevant_entities: list[str] = Field(default_factory=list)
    relevant_relations: list[str] = Field(default_factory=list)
    disputed_claims: list[str] = Field(default_factory=list)
    stale_claims: list[str] = Field(default_factory=list)
    evidence_quality: float = 0.5
    evidence_gaps: list[str] = Field(default_factory=list)
    verification_required: bool = False
    graph_path_hints: list[str] = Field(default_factory=list)
    knowledge_reason_codes: list[str] = Field(default_factory=list)
    degraded: bool = False
    timing_ms: float = 0.0


def _safe_events(user_id: str, event_type: str, limit: int = 8) -> list[dict[str, Any]]:
    if not user_id:
        return []
    try:
        rows = fetch_recent_events_by_type(user_id, event_type, limit=limit)
    except Exception as exc:
        log.debug("cognitive events unavailable %s: %s", event_type, exc)
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        data = row.get("data") if isinstance(row, dict) else {}
        if isinstance(data, dict):
            out.append(data)
    return out


def load_conversation_state(*, user_id: str, session_id: str) -> ConversationStateModel:
    for data in _safe_events(user_id, CONV_STATE_EVENT, limit=12):
        if not session_id or str(data.get("session_id") or "") == session_id or data.get("durable"):
            try:
                return ConversationStateModel.model_validate(data.get("state") or data)
            except Exception:
                continue
    return ConversationStateModel(session_id=session_id)


def load_user_model(*, user_id: str) -> UserPreferenceModel:
    for data in _safe_events(user_id, USER_MODEL_EVENT, limit=6):
        try:
            return UserPreferenceModel.model_validate(data.get("model") or data)
        except Exception:
            continue
    return UserPreferenceModel()


def save_conversation_state(*, user_id: str, state: ConversationStateModel) -> bool:
    if not user_id or str(user_id).startswith("audit"):
        return False
    state.updated_at = time.time()
    try:
        append_event(
            user_id,
            CONV_STATE_EVENT,
            {
                "session_id": state.session_id,
                "durable": False,
                "state": state.model_dump(),
            },
        )
        return True
    except Exception as exc:
        log.debug("conversation state write failed: %s", exc)
        return False


def save_user_model(*, user_id: str, model: UserPreferenceModel) -> bool:
    if not user_id or str(user_id).startswith("audit"):
        return False
    model.updated_at = time.time()
    try:
        append_event(
            user_id,
            USER_MODEL_EVENT,
            {"durable": True, "model": model.model_dump()},
        )
        return True
    except Exception as exc:
        log.debug("user model write failed: %s", exc)
        return False


def _clip_topic(text: str, n: int = 80) -> str:
    t = re.sub(r"\s+", " ", str(text or "").strip())
    return t[:n]


def _rank_intents_from_pragmatics(pragmatics: Any) -> list[IntentRankItem]:
    ranking: list[IntentRankItem] = []
    if pragmatics is None:
        return ranking
    cands = list(getattr(pragmatics, "candidate_intents", None) or [])
    for c in cands[:5]:
        ranking.append(
            IntentRankItem(
                label=str(getattr(c, "label", "") or "unknown"),
                confidence=float(getattr(c, "confidence", 0.0) or 0.0),
                reason=";".join(list(getattr(c, "evidence", None) or [])[:3]),
                ambiguity_contribution=0.0,
            )
        )
    if len(ranking) >= 2:
        gap = abs(ranking[0].confidence - ranking[1].confidence)
        ranking[0].ambiguity_contribution = max(0.0, 0.55 - gap)
        ranking[1].ambiguity_contribution = max(0.0, 0.45 - gap)
    if not ranking:
        ranking.append(
            IntentRankItem(
                label=str(getattr(pragmatics, "primary_intent", "unknown") or "unknown"),
                confidence=float(getattr(pragmatics, "confidence", 0.5) or 0.5),
                reason="fallback_primary",
            )
        )
    return ranking


class ScoredResearchQuery(BaseModel):
    query: str
    score: float = 0.0
    confidence: float = 0.0
    reason: str = ""


def rank_research_queries(
    *,
    rewritten: str,
    raw: str,
    conversation: ConversationStateModel | None = None,
) -> list[ScoredResearchQuery]:
    """Build scored, deduped research query variants (best first)."""
    candidates: list[ScoredResearchQuery] = []
    base = (rewritten or "").strip() or (raw or "").strip()
    if base:
        # Rewritten / explicit tooling query ranks highest
        conf = 0.92 if (rewritten or "").strip() else 0.72
        candidates.append(
            ScoredResearchQuery(
                query=base[:280],
                score=1.0 if (rewritten or "").strip() else 0.82,
                confidence=conf,
                reason="primary_rewritten" if (rewritten or "").strip() else "raw_fallback",
            )
        )
    if conversation and conversation.primary_topic and base:
        topic_q = f"{conversation.primary_topic} {base}".strip()[:280]
        candidates.append(
            ScoredResearchQuery(
                query=topic_q,
                score=0.78,
                confidence=0.7,
                reason="topic_expansion",
            )
        )
    cleaned = re.sub(r"(?iu)\b(gramy|gracie|proszę|prosze|eli|no to)\b", " ", base)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
    if cleaned and cleaned.lower() != (base or "").lower():
        candidates.append(
            ScoredResearchQuery(
                query=cleaned[:280],
                score=0.74,
                confidence=0.68,
                reason="noise_stripped",
            )
        )
    if cleaned and "wynik" not in cleaned.lower() and re.search(r"(?iu)\b(mecz|liga|mundial|final)\b", cleaned):
        candidates.append(
            ScoredResearchQuery(
                query=f"wynik {cleaned}"[:280],
                score=0.88,
                confidence=0.8,
                reason="sport_result_boost",
            )
        )
    if cleaned and re.search(r"(?iu)\b(202\d|dzi[sś]|wczoraj|jutro)\b", cleaned):
        candidates.append(
            ScoredResearchQuery(
                query=cleaned[:280],
                score=max(0.8, next((c.score for c in candidates if c.query == cleaned[:280]), 0.8)),
                confidence=0.75,
                reason="temporal_anchor",
            )
        )
    # Dedup by lowercase query, keep highest score
    best: dict[str, ScoredResearchQuery] = {}
    for c in candidates:
        key = c.query.lower().strip()
        if not key:
            continue
        prev = best.get(key)
        if prev is None or c.score > prev.score:
            best[key] = c
    ranked = sorted(best.values(), key=lambda x: (-x.score, -x.confidence, x.query))
    return ranked[:5]


def expand_research_queries(
    *,
    rewritten: str,
    raw: str,
    conversation: ConversationStateModel | None = None,
) -> list[str]:
    """Multiple ranked query variants for research (deduped, best-first strings)."""
    return [
        s.query
        for s in rank_research_queries(
            rewritten=rewritten, raw=raw, conversation=conversation
        )
    ]


def build_cognitive_influence_pack(
    *,
    user_id: str,
    session_id: str,
    message: str,
    history: list[Any] | None,
    pragmatics: Any = None,
    memory_brief: str = "",
    psyche_brief: str = "",
    memory_v2_ctx: Any = None,
    psyche_v2_ctx: Any = None,
    identity_snapshot: Any = None,
    selected_goal: dict[str, Any] | None = None,
    experience_signal_summary: str = "",
    correction_hints: str = "",
    reflection_summary: str = "",
) -> CognitiveInfluencePack:
    t0 = time.monotonic()
    pack = CognitiveInfluencePack()
    try:
        pack.conversation = load_conversation_state(user_id=user_id, session_id=session_id)
        pack.user_model = load_user_model(user_id=user_id)
        if not pack.conversation.session_id:
            pack.conversation.session_id = session_id

        ranking = _rank_intents_from_pragmatics(pragmatics)
        pack.intent_ranking = ranking
        if ranking:
            pack.primary_intent = ranking[0].label
            pack.intent_confidence = ranking[0].confidence
            pack.ambiguity = float(getattr(pragmatics, "ambiguity_score", 0.0) or 0.0)
            if ranking[0].ambiguity_contribution:
                pack.ambiguity = max(pack.ambiguity, ranking[0].ambiguity_contribution)

        # Memory → style / planner / reasoning
        mem_loaded = bool(memory_v2_ctx and getattr(memory_v2_ctx, "loaded", False))
        if mem_loaded or (memory_brief and "BRAK" not in memory_brief[:20]):
            pack.memory_influence_reason = "memory_context_injected_into_prompt_and_bias"
            pack.influence_reason_codes.append("COG_MEMORY_INFLUENCE")
            if mem_loaded and getattr(memory_v2_ctx, "contradiction_alerts", None):
                pack.force_reasoning = True
                pack.influence_reason_codes.append("COG_MEMORY_CONTRADICTION_REASONING")
                pack.style_directives.append(
                    "Uwzględnij sprzeczności z pamięci — nie udawaj pewności."
                )

        # Psyche → style / confidence / critic sensitivity
        psy_loaded = bool(psyche_v2_ctx and getattr(psyche_v2_ctx, "loaded", False))
        if psy_loaded:
            pack.psyche_influence_reason = (
                f"mode={getattr(psyche_v2_ctx, 'mode', 'neutral')};"
                f"directness={float(getattr(psyche_v2_ctx, 'directness_bias', 0.5)):.2f};"
                f"verbosity={float(getattr(psyche_v2_ctx, 'verbosity_bias', 0.5)):.2f}"
            )
            pack.influence_reason_codes.append("COG_PSYCHE_STYLE")
            vb = float(getattr(psyche_v2_ctx, "verbosity_bias", 0.5) or 0.5)
            if vb < 0.35:
                pack.length_directive = "short"
                pack.style_directives.append("Trzymaj odpowiedź krótko.")
            elif vb > 0.7:
                pack.length_directive = "long"
                pack.style_directives.append("Możesz rozwinąć szczegóły tam, gdzie pomagają.")
            if float(getattr(psyche_v2_ctx, "humour_bias", 0.0) or 0) > 0.55 or pack.user_model.preferred_humour > 0.6:
                pack.style_directives.append("Humor OK, jeśli naturalnie pasuje — nie kosztem treści.")
            if float(getattr(psyche_v2_ctx, "friction", 0.0) or 0) > 0.55:
                pack.style_directives.append("Napięcie relacji: precyzja, bez luźnych interpretacji.")
                pack.force_reasoning = True
        elif psyche_brief and "BRAK" not in psyche_brief[:20]:
            pack.psyche_influence_reason = "legacy_psyche_brief"
            pack.influence_reason_codes.append("COG_PSYCHE_BRIEF")

        # Identity (was mostly zombie — now prompt+style)
        if identity_snapshot is not None:
            prefs = list(getattr(identity_snapshot, "top_preferences", None) or [])
            habits = list(getattr(identity_snapshot, "active_habits", None) or [])
            if prefs or habits or getattr(identity_snapshot, "autobio_summary", ""):
                pack.identity_influence_reason = (
                    f"prefs={len(prefs)} habits={len(habits)} "
                    f"trust={float(getattr(identity_snapshot, 'relation_trust', 0.5) or 0.5):.2f}"
                )
                pack.influence_reason_codes.append("COG_IDENTITY_INFLUENCE")
                if prefs:
                    titles = []
                    for p in prefs[:3]:
                        if isinstance(p, dict):
                            titles.append(str(p.get("title") or p.get("name") or "")[:60])
                        else:
                            titles.append(str(p)[:60])
                    titles = [t for t in titles if t]
                    if titles:
                        pack.style_directives.append(
                            "Preferencje tożsamości użytkownika: " + "; ".join(titles)
                        )

        # Goals → planner / tool order (only high urgency + substantive message)
        if selected_goal and selected_goal.get("title"):
            pack.goals_influence_reason = f"active_goal={selected_goal.get('title')}"
            pack.influence_reason_codes.append("COG_GOALS_INFLUENCE")
            urg = float(selected_goal.get("urgency") or 0.0)
            words = [w for w in re.split(r"\s+", str(message or "").strip()) if w]
            if urg >= 0.7 and len(words) >= 5:
                pack.force_planner_analysis = True
                pack.influence_reason_codes.append("COG_GOALS_PLANNER")
                pack.tool_order_hint = ["memory", "planner", "research", "code"]
            pack.style_directives.append(
                f"Aktywny cel: {selected_goal.get('title')} — nie zgub go w odpowiedzi."
            )

        # Experience → strategy / tool / provider confidence bias signals
        if experience_signal_summary and experience_signal_summary not in (
            "",
            "not_evaluated",
            "none",
        ):
            pack.experience_influence_reason = experience_signal_summary[:180]
            pack.influence_reason_codes.append("COG_EXPERIENCE_BIAS")
            low = experience_signal_summary.lower()
            if any(k in low for k in ("fail", "miss", "blocker", "penalt", "error")):
                pack.force_reasoning = True
                pack.strategy_bias_codes.append("COG_EXPERIENCE_CAUTION")
                pack.style_directives.append(
                    "Doświadczenie sygnałuje ryzyko powtórki błędu — weryfikuj zanim zamkniesz."
                )
            if any(k in low for k in ("research", "web", "tool")):
                pack.tool_order_hint = pack.tool_order_hint or [
                    "research",
                    "memory",
                    "reasoning",
                ]
                pack.influence_reason_codes.append("COG_EXPERIENCE_TOOL_ORDER")

        # Correction
        if correction_hints and correction_hints.strip():
            pack.correction_influence_reason = "user_correction_hints_binding"
            pack.influence_reason_codes.append("COG_CORRECTION_BINDING")
            pack.style_directives.append(
                "Korekty użytkownika są wiążące dla stylu/faktów w tej i kolejnych podobnych turach."
            )
            if pack.user_model.preferred_correction_style == "direct":
                pack.style_directives.append("Przyznaj błąd wprost i popraw — bez bronienia pomyłki.")
            pack.force_reasoning = True
            pack.strategy_bias_codes.append("COG_CORRECTION_PLANNER_BIAS")

        # Reflection (prior) → next-turn bias note
        if not reflection_summary:
            try:
                prior_ev = _safe_events(user_id, "cognitive.reflection_prior", limit=3)
                for pev in prior_ev:
                    if not session_id or str(pev.get("session_id") or "") == session_id:
                        reflection_summary = str(pev.get("summary") or "")
                        break
            except Exception:
                reflection_summary = reflection_summary or ""
        if reflection_summary and reflection_summary.strip():
            pack.reflection_influence_reason = reflection_summary[:160]
            pack.influence_reason_codes.append("COG_REFLECTION_NEXT_TURN")
            rl = reflection_summary.lower()
            if any(k in rl for k in ("weak", "miss", "under", "mismatch", "poor", "zły", "słab")):
                pack.force_reasoning = True
                pack.strategy_bias_codes.append("COG_REFLECTION_STRATEGY_BIAS")
                pack.style_directives.append(
                    "Reflection prior: poprzednia tura była słaba — tym razem precyzyjniej i konkretniej."
                )

        # User model directives
        um = pack.user_model
        if um.sample_count >= 2 or um.confidence >= 0.4:
            pack.influence_reason_codes.append("COG_USER_MODEL_APPLIED")
            if um.preferred_answer_length == "short" and not pack.length_directive:
                pack.length_directive = "short"
                pack.style_directives.append("Preferowana długość: krótko.")
            elif um.preferred_answer_length == "long" and not pack.length_directive:
                pack.length_directive = "long"
            if um.preferred_structure == "bullets":
                pack.style_directives.append("Struktura: punkty/bullets gdy to pomaga.")
            elif um.preferred_structure == "steps":
                pack.style_directives.append("Struktura: kroki, gdy zadanie wykonawcze.")
            if um.preferred_technical_depth > 0.7:
                pack.style_directives.append("Głębia techniczna: konkretny kod/terminologia OK.")
            elif um.preferred_technical_depth < 0.35:
                pack.style_directives.append("Trzymaj język prostszy; unikaj żargonu bez potrzeby.")
            if um.preferred_humour < 0.25:
                pack.style_directives.append("Humor ograniczony — rzeczowo.")
            pack.style_directives.append(
                f"Ton preferowany: {um.preferred_tone}; interakcja: {um.preferred_interaction_style}."
            )

        # Conversation state continuity (beyond retrieval)
        cs = pack.conversation
        if cs.primary_topic:
            pack.style_directives.append(f"Główny temat rozmowy: {cs.primary_topic}.")
            pack.influence_reason_codes.append("COG_CONV_PRIMARY_TOPIC")
        if cs.decided:
            pack.style_directives.append(
                "Decyzje już zapadłe (nie otwieraj ponownie bez powodu): "
                + "; ".join(cs.decided[-4:])
            )
            pack.influence_reason_codes.append("COG_CONV_DECIDED")
        if cs.plan_steps:
            pack.style_directives.append("Plan: " + " → ".join(cs.plan_steps[-5:]))
            pack.force_planner_analysis = pack.force_planner_analysis or len(cs.plan_steps) >= 2
            pack.influence_reason_codes.append("COG_CONV_PLAN")
        if cs.executed:
            pack.style_directives.append("Już wykonane: " + "; ".join(cs.executed[-4:]))
        if cs.rejected:
            pack.style_directives.append(
                "Odrzucone wcześniej (nie proponuj ponownie): " + "; ".join(cs.rejected[-3:])
            )
            pack.influence_reason_codes.append("COG_CONV_REJECTED")
        if cs.open_questions:
            pack.style_directives.append(
                "Otwarte pytania z rozmowy: " + "; ".join(cs.open_questions[-3:])
            )

        # Low intent confidence → reasoning; planner only for long ambiguous multi-step
        words_n = len([w for w in re.split(r"\s+", str(message or "").strip()) if w])
        low_conf = pack.intent_confidence < 0.55 or pack.ambiguity >= 0.55
        try:
            from aihub.strategy_selector import is_assistant_meta_ask

            meta_ask = is_assistant_meta_ask(message or "")
        except Exception:
            meta_ask = False
        skip_escalate = (
            pack.primary_intent
            in (
                "greeting",
                "simple_technical_question",
                "literal_food_or_recipe",
                "meta_audit_request",
                "identity_question",
                "self_description",
            )
            or meta_ask
            or (words_n <= 8 and pack.ambiguity < 0.55)
            or (words_n <= 2 and pack.ambiguity < 0.45 and pack.intent_confidence >= 0.45)
        )
        if low_conf and not skip_escalate:
            pack.force_reasoning = True
            if words_n >= 18 and pack.ambiguity >= 0.6:
                pack.force_planner_analysis = True
            pack.strategy_bias_codes.append("COG_LOW_INTENT_CONFIDENCE_ESCALATE")
            pack.influence_reason_codes.append("COG_INTENT_RANKING_ESCALATION")
        elif meta_ask:
            pack.force_planner_analysis = False
            pack.influence_reason_codes.append("COG_META_ASK_NO_PLANNER")

        # Research variants from pragmatics rewrite + conversation
        rw = str(getattr(pragmatics, "rewritten_query_for_tools", "") or "")
        if rw or bool(getattr(pragmatics, "needs_web", False)):
            scored = rank_research_queries(
                rewritten=rw, raw=message, conversation=cs
            )
            pack.research_query_variants = [s.query for s in scored]
            pack.research_query_scores = [s.model_dump() for s in scored]
            if pack.research_query_variants:
                pack.influence_reason_codes.append("COG_RESEARCH_MULTI_QUERY")
                pack.tool_order_hint = pack.tool_order_hint or [
                    "research",
                    "memory",
                    "reasoning",
                ]

        # Pragmatics conversation_state label into durable model seeds
        if pragmatics is not None and not cs.primary_topic and message:
            cs.primary_topic = _clip_topic(message)
        if pragmatics is not None:
            cs.last_intent = str(getattr(pragmatics, "primary_intent", "") or "")
            cs.last_speech_act = str(getattr(pragmatics, "speech_act", "") or "")

        hist_n = len(history or [])
        if hist_n >= 1:
            pack.influence_reason_codes.append("COG_HISTORY_CONTINUITY")

    except Exception as exc:
        log.warning("cognitive pack build failed: %s", exc, exc_info=True)
        pack.degraded = True
        pack.influence_reason_codes.append("COG_DEGRADED_FALLBACK")

    pack.timing_ms = (time.monotonic() - t0) * 1000.0
    return pack


def apply_cognitive_to_decision(
    *,
    decision_core: dict[str, Any],
    pack: CognitiveInfluencePack,
) -> dict[str, Any]:
    """Mutate decision_core so cognitive pack changes strategy/confidence/tools path."""
    codes = list(decision_core.get("reason_codes") or [])
    strategy = str(decision_core.get("selected_strategy") or "contextual")
    conf = float(decision_core.get("strategy_confidence") or 0.7)

    for c in pack.strategy_bias_codes:
        if c not in codes:
            codes.append(c)

    if pack.force_reasoning:
        decision_core["escalation_use_reasoning"] = True
        if strategy == "instant" and pack.ambiguity >= 0.55:
            strategy = "contextual"
            codes.append("COG_REASONING_BLOCKED_INSTANT")

    if pack.force_planner_analysis:
        decision_core["planner_recommended"] = True
        decision_core["escalation_use_reasoning"] = True
        if strategy == "instant" and (
            pack.ambiguity >= 0.55 or pack.intent_confidence < 0.5
        ):
            strategy = "contextual"
            codes.append("COG_PLANNER_ESCALATION")
        hint = "[Cognitive: niska pewność intencji lub aktywny plan — pogłęb analizę przed odpowiedzią.]"
        existing = str(decision_core.get("strategy_hints") or "")
        if "Cognitive:" not in existing and (pack.ambiguity >= 0.55 or pack.force_planner_analysis):
            decision_core["strategy_hints"] = (existing + " " + hint).strip()

    # Psyche cautious → lower confidence (real effect already partially there; reinforce with code)
    if "COG_PSYCHE_STYLE" in pack.influence_reason_codes and "cautious" in (
        pack.psyche_influence_reason or ""
    ):
        conf = max(0.3, conf - 0.05)
        codes.append("COG_PSYCHE_CONFIDENCE")

    if "COG_EXPERIENCE_CAUTION" in pack.strategy_bias_codes:
        conf = max(0.28, conf - 0.06)
        codes.append("COG_EXPERIENCE_CONFIDENCE")
        if strategy == "instant":
            strategy = "contextual"
            codes.append("COG_EXPERIENCE_BLOCKED_INSTANT")

    if "COG_REFLECTION_STRATEGY_BIAS" in pack.strategy_bias_codes:
        conf = max(0.3, conf - 0.04)
        codes.append("COG_REFLECTION_CONFIDENCE")

    if "COG_CORRECTION_PLANNER_BIAS" in pack.strategy_bias_codes:
        conf = max(0.28, conf - 0.05)
        decision_core["planner_recommended"] = True
        codes.append("COG_CORRECTION_CONFIDENCE")

    if pack.user_model.preferred_planning_style == "deep" and strategy == "instant":
        strategy = "contextual"
        codes.append("COG_USER_PLANNING_PREFERENCE")

    if pack.research_query_variants and decision_core.get("web_decision") == "required":
        decision_core["research_query_variants"] = list(pack.research_query_variants)
        decision_core["research_query_scores"] = list(pack.research_query_scores)
        codes.append("COG_RESEARCH_VARIANTS_ATTACHED")

    if pack.tool_order_hint:
        decision_core["tool_order_hint"] = list(pack.tool_order_hint)
        codes.append("COG_TOOL_ORDER_HINT")

    if pack.verification_required and str(decision_core.get("web_decision") or "off") == "off":
        decision_core["web_decision"] = "optional"
        codes.append("COG_WK_VERIFICATION_OPTIONAL_WEB")
    if pack.disputed_claims:
        conf = max(0.25, conf - 0.06)
        codes.append("COG_WK_DISPUTED_CONFIDENCE")
        if strategy == "instant":
            strategy = "contextual"
            codes.append("COG_WK_DISPUTED_BLOCK_INSTANT")
    if pack.stale_claims:
        codes.append("COG_WK_STALE_PRESENT")

    decision_core["selected_strategy"] = strategy
    decision_core["strategy_confidence"] = round(conf, 3)
    decision_core["reason_codes"] = codes
    decision_core["cognitive_influence_codes"] = list(pack.influence_reason_codes)
    decision_core["intent_ranking"] = [i.model_dump() for i in pack.intent_ranking[:5]]
    decision_core["intent_confidence"] = pack.intent_confidence
    decision_core["cognitive_ambiguity"] = pack.ambiguity
    decision_core["provider_confidence_bias"] = round(
        (-0.05 if "COG_EXPERIENCE_CAUTION" in pack.strategy_bias_codes else 0.0)
        + (-0.03 if "COG_REFLECTION_STRATEGY_BIAS" in pack.strategy_bias_codes else 0.0)
        + (-0.04 if "COG_CORRECTION_PLANNER_BIAS" in pack.strategy_bias_codes else 0.0),
        3,
    )
    return decision_core


def cognitive_prompt_block(pack: CognitiveInfluencePack) -> str:
    lines = [
        "INTEGRACJA POZNAWCZA (wiążące — jedna spójna inteligencja):",
        f"- primary_intent: {pack.primary_intent} (confidence={pack.intent_confidence:.2f}, ambiguity={pack.ambiguity:.2f})",
    ]
    if pack.intent_ranking:
        ranks = ", ".join(
            f"{i.label}:{i.confidence:.2f}" for i in pack.intent_ranking[:3]
        )
        lines.append(f"- intent_ranking: {ranks}")
    cs = pack.conversation
    lines.append(
        f"- conversation: topic={cs.primary_topic or '—'}; turns={cs.turn_count}; "
        f"decided={len(cs.decided)}; plan={len(cs.plan_steps)}; executed={len(cs.executed)}"
    )
    if pack.length_directive:
        lines.append(f"- length_directive: {pack.length_directive}")
    for d in pack.style_directives[:10]:
        lines.append(f"- {d}")
    if pack.memory_influence_reason:
        lines.append(f"- memory: {pack.memory_influence_reason}")
    if pack.psyche_influence_reason:
        lines.append(f"- psyche: {pack.psyche_influence_reason}")
    if pack.identity_influence_reason:
        lines.append(f"- identity: {pack.identity_influence_reason}")
    if pack.goals_influence_reason:
        lines.append(f"- goals: {pack.goals_influence_reason}")
    if pack.experience_influence_reason:
        lines.append(f"- experience: {pack.experience_influence_reason}")
    if pack.correction_influence_reason:
        lines.append(f"- correction: {pack.correction_influence_reason}")
    if pack.reflection_influence_reason:
        lines.append(f"- reflection: {pack.reflection_influence_reason}")
    if pack.research_query_variants:
        lines.append(
            "- research_variants: " + " | ".join(pack.research_query_variants[:3])
        )
    if pack.force_planner_analysis:
        lines.append("- Jeżeli intencja jest niepewna: rozważ opcje, nie zgaduj na ślepo.")
    return "\n".join(lines)


def critique_response_v2(
    *,
    response_text: str,
    pragmatics: Any = None,
    pack: CognitiveInfluencePack | None = None,
    memory_used: bool = False,
    psyche_used: bool = False,
    planner_recommended: bool = False,
    web_used: bool = False,
    web_was_required: bool = False,
) -> Any:
    """Richer critic: style/length/humour/memory/psyche/planner/web fit + pragmatics."""
    from aihub.turn.pragmatics import ResponseCriticResult, critique_response

    if pragmatics is not None:
        base = critique_response(response_text=response_text, pragmatics=pragmatics)
        score = int(base.score)
        codes = list(base.reason_codes)
        revision = base.revision_instruction
    else:
        score = 100
        codes = []
        revision = ""

    text = str(response_text or "")
    lower = text.lower()
    n_chars = len(text.strip())

    # Mechanical / helpdesk
    if re.search(
        r"(?iu)\b(jak mogę pomóc|w czym mogę pomóc|co dziś potrzebujesz|jestem gotowy)\b",
        text,
    ):
        score -= 35
        codes.append("CRITIC_MECHANICAL_HELPDESK")
        revision = revision or "Usuń helpdesk; odpowiadaj jak partner rozmowy."

    # Length vs directive
    length_dir = (pack.length_directive if pack else "") or ""
    if pack and pack.user_model.preferred_answer_length == "short":
        length_dir = length_dir or "short"
    if length_dir == "short" and n_chars > 900:
        score -= 20
        codes.append("CRITIC_TOO_LONG_VS_USER_MODEL")
        revision = (
            revision
            or "Skróć odpowiedź — użytkownik preferuje zwięzłość."
        )
    if length_dir == "long" and 0 < n_chars < 80:
        score -= 15
        codes.append("CRITIC_TOO_SHORT_VS_USER_MODEL")

    # Humour mismatch on sarcastic/frustrated
    if pragmatics is not None and getattr(pragmatics, "frustration_detected", False):
        if re.search(r"(?iu)(haha|lol|😂|😅)", text) and n_chars < 200:
            score -= 20
            codes.append("CRITIC_HUMOUR_MISMATCH_FRUSTRATION")
            revision = revision or "Bez żartów przy frustracji — konkret i diagnoza."

    # Memory/psyche unused when pack expected them
    if pack and "COG_MEMORY_INFLUENCE" in pack.influence_reason_codes and not memory_used:
        # Soft: only penalize if response contradicts known preference cue in directives
        if any("Preferencje" in d or "preferenc" in d.lower() for d in pack.style_directives):
            score -= 10
            codes.append("CRITIC_MEMORY_UNDERUSED")
    if pack and "COG_PSYCHE_STYLE" in pack.influence_reason_codes and not psyche_used:
        score -= 5
        codes.append("CRITIC_PSYCHE_SIGNAL_WEAK")

    # Planner expected but answer is empty shrug
    if planner_recommended and n_chars < 40:
        score -= 15
        codes.append("CRITIC_PLANNER_UNDERDELIVERED")

    # Web required but answer pretends success without tools
    if web_was_required and not web_used:
        if re.search(r"(?iu)\b(sprawdziłem|według źródeł|aktualny wynik)\b", text):
            score -= 40
            codes.append("CRITIC_FAKE_WEB_CLAIM")
            revision = (
                revision
                or "Nie twierdź, że sprawdziłeś web, jeśli nie było narzędzia."
            )

    # Boredom / filler
    if re.search(
        r"(?iu)^(oczywiście[!.,]|jasne[!.,]|super[!.,]|w porządku[.!])\s*$",
        text.strip(),
    ):
        score -= 25
        codes.append("CRITIC_EMPTY_FILLER")
        revision = revision or "Dodaj treść merytoryczną — unikaj pustych wypełniaczy."

    # Missed primary intent entirely (very short vs complex ranking)
    if pack and pack.intent_confidence >= 0.7 and pack.primary_intent not in (
        "greeting",
        "statement_or_chat",
        "unknown",
    ):
        if n_chars < 15 and pack.primary_intent not in ("greeting",):
            score -= 20
            codes.append("CRITIC_INTENT_NOT_ADDRESSED")

    passed = score >= 70
    if not passed and not revision:
        revision = (
            f"Popraw pod intent={pack.primary_intent if pack else 'unknown'}, "
            f"długość={length_dir or 'fit'}, bez helpdesku i bez pomijania kontekstu rozmowy."
        )
    return ResponseCriticResult(
        score=max(0, score),
        passed=passed,
        reason_codes=codes,
        revision_instruction=revision,
    )


def update_conversation_after_turn(
    *,
    user_id: str,
    session_id: str,
    message: str,
    response_text: str,
    pragmatics: Any = None,
    pack: CognitiveInfluencePack | None = None,
    ok: bool = True,
) -> ConversationStateModel:
    state = (
        pack.conversation
        if pack is not None
        else load_conversation_state(user_id=user_id, session_id=session_id)
    )
    state.session_id = session_id or state.session_id
    state.turn_count = int(state.turn_count or 0) + 1
    msg = _clip_topic(message, 100)
    if msg:
        if not state.primary_topic:
            state.primary_topic = msg
        elif msg.lower() not in state.primary_topic.lower():
            # Side topic if clearly different and short follow-up not deixis-only
            if len(msg.split()) >= 4 and msg[:40].lower() not in state.primary_topic.lower():
                if msg not in state.side_topics:
                    state.side_topics = (state.side_topics + [msg])[-8:]

    if pragmatics is not None:
        state.last_intent = str(getattr(pragmatics, "primary_intent", "") or state.last_intent)
        state.last_speech_act = str(getattr(pragmatics, "speech_act", "") or state.last_speech_act)
        act = state.last_speech_act
        if act == "task_instruction" and msg:
            step = msg
            if step not in state.plan_steps:
                state.plan_steps = (state.plan_steps + [step])[-10:]
            if ok and step not in state.executed:
                state.executed = (state.executed + [step])[-10:]
        if act == "correction" and msg:
            if msg not in state.rejected:
                # Prior wrong interp marked rejected lightly
                state.rejected = (state.rejected + [f"misread→{msg}"])[-8:]
        if getattr(pragmatics, "needs_recent_history", False):
            ref = msg
            if ref and ref not in state.active_refs:
                state.active_refs = (state.active_refs + [ref])[-10:]

    # Explicit decisions / rejections in natural language
    msg_l = (message or "").lower()
    if re.search(r"(?iu)\b(zostajemy przy|decydujemy|wybieramy|nie zmieniamy|ok, robimy)\b", msg_l):
        d = msg
        if d and d not in state.decided:
            state.decided = (state.decided + [d])[-10:]
    if re.search(r"(?iu)\b(odrzucam|nie chcę|bez\s+\w+|nie używamy|skip)\b", msg_l):
        r = msg
        if r and r not in state.rejected:
            state.rejected = (state.rejected + [r])[-8:]
    # Assistant commitment phrases → decided
    resp_l = (response_text or "").lower()
    if re.search(r"(?iu)\b(zostajemy przy|ustalone:|decyzja:|robimy tak)\b", resp_l):
        d2 = _clip_topic(response_text, 80)
        if d2 and d2 not in state.decided:
            state.decided = (state.decided + [d2])[-10:]
    # Expire old active refs every 40 turns
    if state.turn_count > 0 and state.turn_count % 40 == 0 and state.active_refs:
        state.expired_refs = (state.expired_refs + state.active_refs[:2])[-10:]
        state.active_refs = state.active_refs[2:]

    # Open questions: user asks and we hedged
    if "?" in (message or "") and re.search(r"(?iu)\b(nie mam|podaj|doprecyzuj|brak danych)\b", response_text or ""):
        q = msg
        if q and q not in state.open_questions:
            state.open_questions = (state.open_questions + [q])[-6:]

    save_conversation_state(user_id=user_id, state=state)
    return state


def update_user_model_from_turn(
    *,
    user_id: str,
    message: str,
    response_text: str,
    pragmatics: Any = None,
    critic_score: int | None = None,
    revision_happened: bool = False,
    pack: CognitiveInfluencePack | None = None,
) -> UserPreferenceModel:
    model = pack.user_model if pack is not None else load_user_model(user_id=user_id)
    msg = (message or "").lower()
    changed = False

    def _blend(cur: float, target: float, w: float = 0.2) -> float:
        return max(0.0, min(1.0, cur * (1 - w) + target * w))

    if re.search(r"(?iu)\b(krócej|zwięźlej|za długo|skr[oó]ć)\b", msg):
        model.preferred_answer_length = "short"
        model.preferred_detail_level = _blend(model.preferred_detail_level, 0.3)
        changed = True
    if re.search(r"(?iu)\b(dłużej|rozwi[nń]|więcej szczeg[oó]ł|bardziej szczeg[oó]łow)\b", msg):
        model.preferred_answer_length = "long"
        model.preferred_detail_level = _blend(model.preferred_detail_level, 0.8)
        changed = True
    if re.search(r"(?iu)\b(punktami|w punktach|list[aą]|krokami|krok po kroku)\b", msg):
        model.preferred_structure = "steps" if "krok" in msg else "bullets"
        changed = True
    if re.search(r"(?iu)\b(bez żart[oó]w|powa[zż]nie|bez humoru)\b", msg):
        model.preferred_humour = _blend(model.preferred_humour, 0.15)
        changed = True
    if re.search(r"(?iu)\b(z humorem|luzniej|bardziej na luzie)\b", msg):
        model.preferred_humour = _blend(model.preferred_humour, 0.75)
        changed = True
    if re.search(r"(?iu)\b(bardziej techniczn|z kodem|głębiej techniczn)\b", msg):
        model.preferred_technical_depth = _blend(model.preferred_technical_depth, 0.85)
        changed = True
    if pragmatics is not None and getattr(pragmatics, "speech_act", "") == "correction":
        model.preferred_correction_style = "direct"
        model.preferred_interaction_style = "partner"
        changed = True
        # Correction = dissatisfaction signal
        model.preferred_detail_level = _blend(model.preferred_detail_level, 0.5)

    if revision_happened or (critic_score is not None and critic_score < 70):
        # Soft: slightly prefer shorter, less mechanical tone
        model.preferred_tone = "natural"
        changed = True

    if changed:
        model.sample_count = int(model.sample_count or 0) + 1
        model.confidence = min(0.95, 0.25 + 0.07 * model.sample_count)
        save_user_model(user_id=user_id, model=model)
    return model


def calibrate_from_outcome(
    *,
    user_id: str,
    decision_core: dict[str, Any],
    ok: bool,
    critic_score: int | None,
    revision_happened: bool,
    web_used: bool,
    web_required: bool,
    tool_successes: int,
    tool_failures: int,
    correction_this_turn: bool,
) -> dict[str, Any]:
    """Self-calibration → strategy_decision_bias adjustments + event."""
    from aihub.db import get_strategy_decision_bias, save_strategy_decision_bias

    bias = dict(get_strategy_decision_bias(user_id) or {})
    strategy = str(decision_core.get("selected_strategy") or "contextual")
    signals: list[str] = []

    def _bump(key: str, delta: float) -> None:
        bias[key] = round(max(-0.25, min(0.25, float(bias.get(key, 0.0)) + delta)), 4)

    if correction_this_turn:
        _bump(strategy, -0.04)
        _bump("contextual", 0.02)
        signals.append("CAL_CORRECTION_PENALIZE_STRATEGY")
    if critic_score is not None and critic_score < 70:
        _bump(strategy, -0.03)
        signals.append("CAL_CRITIC_FAIL")
    if revision_happened and ok:
        signals.append("CAL_REVISION_RECOVERED")
    if web_required and web_used and tool_successes > 0:
        _bump("research", 0.02)
        signals.append("CAL_RESEARCH_HIT")
    if web_required and (tool_failures > 0 or not web_used):
        _bump("research", -0.02)
        signals.append("CAL_RESEARCH_MISS")
    if ok and not revision_happened and (critic_score is None or critic_score >= 80):
        _bump(strategy, 0.015)
        signals.append("CAL_SUCCESS_REINFORCE")

    try:
        save_strategy_decision_bias(user_id, bias, metrics_snapshot={"signals": signals[:8]})
    except Exception as exc:
        log.debug("calibration bias save failed: %s", exc)

    payload = {
        "strategy": strategy,
        "bias": bias,
        "signals": signals,
        "ok": ok,
        "critic_score": critic_score,
        "revision_happened": revision_happened,
        "durable": False,
    }
    try:
        if user_id and not str(user_id).startswith("audit"):
            append_event(user_id, CALIBRATION_EVENT, payload)
    except Exception as cal_exc:
        log.debug("calibration event skipped: %s", cal_exc)
    return payload


def cognitive_trace_fields(pack: CognitiveInfluencePack) -> dict[str, Any]:
    return {
        "cognitive_integration_happened": True,
        "cognitive_degraded": pack.degraded,
        "cognitive_influence_reason_codes": list(pack.influence_reason_codes),
        "cognitive_timing_ms": pack.timing_ms,
        "intent_ranking": [i.model_dump() for i in pack.intent_ranking[:5]],
        "intent_confidence": pack.intent_confidence,
        "cognitive_ambiguity": pack.ambiguity,
        "conversation_primary_topic": pack.conversation.primary_topic[:120],
        "conversation_turn_count": pack.conversation.turn_count,
        "conversation_decided_count": len(pack.conversation.decided),
        "conversation_plan_count": len(pack.conversation.plan_steps),
        "conversation_executed_count": len(pack.conversation.executed),
        "conversation_rejected_count": len(pack.conversation.rejected),
        "user_model_confidence": pack.user_model.confidence,
        "user_model_length": pack.user_model.preferred_answer_length,
        "user_model_humour": pack.user_model.preferred_humour,
        "user_model_tech_depth": pack.user_model.preferred_technical_depth,
        "cognitive_length_directive": pack.length_directive,
        "cognitive_style_directive_count": len(pack.style_directives),
        "cognitive_force_reasoning": pack.force_reasoning,
        "cognitive_force_planner": pack.force_planner_analysis,
        "research_query_variant_count": len(pack.research_query_variants),
        "research_query_variants": pack.research_query_variants[:5],
        "research_query_scores": list(pack.research_query_scores[:5]),
        "memory_influence_reason": pack.memory_influence_reason[:160],
        "psyche_influence_reason": pack.psyche_influence_reason[:160],
        "identity_influence_reason": pack.identity_influence_reason[:160],
        "goals_influence_reason": pack.goals_influence_reason[:160],
        "experience_influence_reason": pack.experience_influence_reason[:160],
        "correction_influence_reason": pack.correction_influence_reason[:160],
        "reflection_influence_reason": pack.reflection_influence_reason[:160],
        "tool_order_hint": list(pack.tool_order_hint),
        "knowledge_verification_required": pack.verification_required,
        "knowledge_evidence_quality": pack.evidence_quality,
        "knowledge_stale_count": len(pack.stale_claims),
        "knowledge_disputed_count": len(pack.disputed_claims),
        "knowledge_reason_codes": list(pack.knowledge_reason_codes)[:12],
    }
