"""Persistence helpers for adaptive learning tables."""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

from aihub.adaptive_learning.models import (
    CausalAttribution,
    FailurePattern,
    LearnedLesson,
    LongHorizonTask,
    RuntimeSelfModel,
    SuccessPattern,
    TurnOutcomeEvaluation,
    UserModelV2,
)
from aihub.adaptive_learning.schema import ensure_adaptive_learning_schema
from aihub.db import exec_one, fetch_all, fetch_one, json_dumps, json_loads

log = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


def _hash_text(*parts: str) -> str:
    blob = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _j(obj: Any) -> str:
    return json_dumps(obj if obj is not None else {})


def _jl(raw: Any, default: Any = None) -> Any:
    if default is None:
        default = []
    if raw is None or raw == "":
        return default
    try:
        return json_loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return default


def ensure_ready() -> None:
    ensure_adaptive_learning_schema()


def upsert_turn_outcome(outcome: TurnOutcomeEvaluation) -> None:
    ensure_ready()
    ts = outcome.updated_at or _now()
    outcome.updated_at = ts
    if not outcome.created_at:
        outcome.created_at = ts
    exec_one(
        """
        INSERT INTO turn_outcomes(
            turn_id, user_id, session_id, primary_intent, selected_strategy,
            planner_used, reasoning_used, web_used, tools_used,
            response_critic_score, final_response_quality,
            user_satisfaction_signal, correction_signal, rejection_signal,
            continuation_signal, acceptance_signal, task_completion_signal,
            factual_grounding_score, style_match_score, intent_match_score,
            verbosity_match_score, tool_success_score, research_quality_score,
            latency_score, cost_score, overall_reward, confidence,
            reason_codes, degraded, delayed_feedback_applied,
            message_preview, response_preview, metadata_json, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(turn_id) DO UPDATE SET
            primary_intent=excluded.primary_intent,
            selected_strategy=excluded.selected_strategy,
            planner_used=excluded.planner_used,
            reasoning_used=excluded.reasoning_used,
            web_used=excluded.web_used,
            tools_used=excluded.tools_used,
            response_critic_score=excluded.response_critic_score,
            final_response_quality=excluded.final_response_quality,
            user_satisfaction_signal=excluded.user_satisfaction_signal,
            correction_signal=excluded.correction_signal,
            rejection_signal=excluded.rejection_signal,
            continuation_signal=excluded.continuation_signal,
            acceptance_signal=excluded.acceptance_signal,
            task_completion_signal=excluded.task_completion_signal,
            factual_grounding_score=excluded.factual_grounding_score,
            style_match_score=excluded.style_match_score,
            intent_match_score=excluded.intent_match_score,
            verbosity_match_score=excluded.verbosity_match_score,
            tool_success_score=excluded.tool_success_score,
            research_quality_score=excluded.research_quality_score,
            latency_score=excluded.latency_score,
            cost_score=excluded.cost_score,
            overall_reward=excluded.overall_reward,
            confidence=excluded.confidence,
            reason_codes=excluded.reason_codes,
            degraded=excluded.degraded,
            delayed_feedback_applied=excluded.delayed_feedback_applied,
            message_preview=excluded.message_preview,
            response_preview=excluded.response_preview,
            metadata_json=excluded.metadata_json,
            updated_at=excluded.updated_at
        """,
        (
            outcome.turn_id,
            outcome.user_id,
            outcome.session_id,
            outcome.primary_intent,
            outcome.selected_strategy,
            int(outcome.planner_used),
            int(outcome.reasoning_used),
            int(outcome.web_used),
            int(outcome.tools_used),
            outcome.response_critic_score,
            outcome.final_response_quality,
            outcome.user_satisfaction_signal,
            outcome.correction_signal,
            outcome.rejection_signal,
            outcome.continuation_signal,
            outcome.acceptance_signal,
            outcome.task_completion_signal,
            outcome.factual_grounding_score,
            outcome.style_match_score,
            outcome.intent_match_score,
            outcome.verbosity_match_score,
            outcome.tool_success_score,
            outcome.research_quality_score,
            outcome.latency_score,
            outcome.cost_score,
            outcome.overall_reward,
            outcome.confidence,
            _j(outcome.reason_codes),
            int(outcome.degraded),
            int(outcome.delayed_feedback_applied),
            outcome.message_preview[:240],
            outcome.response_preview[:240],
            _j(outcome.metadata),
            outcome.created_at,
            outcome.updated_at,
        ),
    )


def get_turn_outcome(turn_id: str) -> TurnOutcomeEvaluation | None:
    ensure_ready()
    row = fetch_one("SELECT * FROM turn_outcomes WHERE turn_id=?", (turn_id,))
    if not row:
        return None
    return _row_to_outcome(row)


def list_recent_outcomes(
    *, user_id: str, session_id: str = "", limit: int = 12
) -> list[TurnOutcomeEvaluation]:
    ensure_ready()
    if session_id:
        rows = fetch_all(
            """
            SELECT * FROM turn_outcomes
            WHERE user_id=? AND session_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, session_id, limit),
        )
    else:
        rows = fetch_all(
            """
            SELECT * FROM turn_outcomes
            WHERE user_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )
    return [_row_to_outcome(r) for r in rows]


def _row_to_outcome(row: Any) -> TurnOutcomeEvaluation:
    d = dict(row)
    return TurnOutcomeEvaluation(
        turn_id=d["turn_id"],
        user_id=d["user_id"],
        session_id=d.get("session_id") or "",
        primary_intent=d.get("primary_intent") or "unknown",
        selected_strategy=d.get("selected_strategy") or "contextual",
        planner_used=bool(d.get("planner_used")),
        reasoning_used=bool(d.get("reasoning_used")),
        web_used=bool(d.get("web_used")),
        tools_used=bool(d.get("tools_used")),
        response_critic_score=d.get("response_critic_score"),
        final_response_quality=float(d.get("final_response_quality") or 0.5),
        user_satisfaction_signal=float(d.get("user_satisfaction_signal") or 0),
        correction_signal=float(d.get("correction_signal") or 0),
        rejection_signal=float(d.get("rejection_signal") or 0),
        continuation_signal=float(d.get("continuation_signal") or 0),
        acceptance_signal=float(d.get("acceptance_signal") or 0),
        task_completion_signal=float(d.get("task_completion_signal") or 0),
        factual_grounding_score=float(d.get("factual_grounding_score") or 0.5),
        style_match_score=float(d.get("style_match_score") or 0.5),
        intent_match_score=float(d.get("intent_match_score") or 0.5),
        verbosity_match_score=float(d.get("verbosity_match_score") or 0.5),
        tool_success_score=float(d.get("tool_success_score") or 0.5),
        research_quality_score=float(d.get("research_quality_score") or 0.5),
        latency_score=float(d.get("latency_score") or 0.5),
        cost_score=float(d.get("cost_score") or 0.5),
        overall_reward=float(d.get("overall_reward") or 0),
        confidence=float(d.get("confidence") or 0.4),
        reason_codes=list(_jl(d.get("reason_codes"), [])),
        degraded=bool(d.get("degraded")),
        delayed_feedback_applied=bool(d.get("delayed_feedback_applied")),
        message_preview=d.get("message_preview") or "",
        response_preview=d.get("response_preview") or "",
        metadata=dict(_jl(d.get("metadata_json"), {})),
        created_at=float(d.get("created_at") or 0),
        updated_at=float(d.get("updated_at") or 0),
    )


def insert_causal(turn_id: str, user_id: str, items: list[CausalAttribution]) -> int:
    ensure_ready()
    n = 0
    ts = _now()
    for item in items[:12]:
        exec_one(
            """
            INSERT INTO causal_attributions(
                id, turn_id, user_id, factor, contribution_score, confidence,
                evidence, evidence_kind, positive_or_negative, corrective_action,
                scope, expiry, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(uuid.uuid4()),
                turn_id,
                user_id,
                item.factor[:120],
                float(item.contribution_score),
                float(item.confidence),
                item.evidence[:400],
                item.evidence_kind,
                item.positive_or_negative,
                item.corrective_action[:240],
                item.scope,
                item.expiry,
                ts,
            ),
        )
        n += 1
    return n


def list_causal(turn_id: str) -> list[CausalAttribution]:
    ensure_ready()
    rows = fetch_all(
        "SELECT * FROM causal_attributions WHERE turn_id=? ORDER BY ABS(contribution_score) DESC",
        (turn_id,),
    )
    out: list[CausalAttribution] = []
    for r in rows:
        d = dict(r)
        out.append(
            CausalAttribution(
                factor=d["factor"],
                contribution_score=float(d["contribution_score"]),
                confidence=float(d["confidence"]),
                evidence=d.get("evidence") or "",
                evidence_kind=d.get("evidence_kind") or "inferred",
                positive_or_negative=d.get("positive_or_negative") or "neutral",
                corrective_action=d.get("corrective_action") or "",
                scope=d.get("scope") or "user",
                expiry=d.get("expiry"),
            )
        )
    return out


def upsert_lesson(lesson: LearnedLesson) -> tuple[bool, str]:
    """Returns (persisted, reason). Dedup by content_hash+scope+user."""
    ensure_ready()
    if not lesson.content_hash:
        lesson.content_hash = _hash_text(
            lesson.scope,
            lesson.user_id,
            lesson.machine_action or "",
            lesson.category,
            lesson.statement,
        )
    ts = _now()
    existing = fetch_one(
        """
        SELECT * FROM learned_lessons
        WHERE content_hash=? AND scope=? AND user_id=?
        """,
        (lesson.content_hash, lesson.scope, lesson.user_id or ""),
    )
    if existing:
        d = dict(existing)
        if bool(d.get("archived")):
            return False, "archived"
        conf = min(0.95, float(d.get("confidence") or 0.4) + 0.05)
        rein = int(d.get("reinforcement_count") or 1) + 1
        ev = int(d.get("evidence_count") or 1) + 1
        sr = float(d.get("success_rate") or 0.5)
        if lesson.success_rate >= 0.55:
            sr = min(0.99, sr * 0.85 + 0.15)
        else:
            sr = max(0.01, sr * 0.85 + lesson.success_rate * 0.15)
        exec_one(
            """
            UPDATE learned_lessons SET
                confidence=?, reinforcement_count=?, evidence_count=?,
                success_rate=?, updated_at=?, suppressed=?,
                machine_action=COALESCE(NULLIF(?, ''), machine_action)
            WHERE lesson_id=?
            """,
            (
                conf,
                rein,
                ev,
                sr,
                ts,
                int(bool(d.get("suppressed")) and float(d.get("contradiction_count") or 0) >= 3),
                getattr(lesson, "machine_action", "") or "",
                d["lesson_id"],
            ),
        )
        return True, "reinforced"
    if lesson.confidence < 0.45 and lesson.scope == "global":
        return False, "global_threshold"
    if lesson.confidence < 0.35:
        return False, "low_confidence"
    if not (lesson.machine_action or "").strip() and lesson.confidence < 0.55:
        return False, "missing_machine_action"
    lesson.lesson_id = lesson.lesson_id or str(uuid.uuid4())
    lesson.created_at = lesson.created_at or ts
    lesson.updated_at = ts
    exec_one(
        """
        INSERT INTO learned_lessons(
            lesson_id, user_id, scope, trigger_turn_id, category, statement,
            machine_action, machine_action_payload,
            confidence, evidence_count, positive_examples, negative_examples,
            applicable_intents, applicable_strategies, applicable_tools,
            applicable_conversation_states, reinforcement_count, contradiction_count,
            success_rate, created_at, updated_at, expires_at, suppressed, archived, content_hash
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            lesson.lesson_id,
            lesson.user_id or "",
            lesson.scope,
            lesson.trigger_turn_id,
            lesson.category,
            lesson.statement[:480],
            (lesson.machine_action or "")[:80],
            _j(getattr(lesson, "machine_action_payload", {}) or {}),
            lesson.confidence,
            lesson.evidence_count,
            _j(lesson.positive_examples[:5]),
            _j(lesson.negative_examples[:5]),
            _j(lesson.applicable_intents[:8]),
            _j(lesson.applicable_strategies[:8]),
            _j(lesson.applicable_tools[:8]),
            _j(lesson.applicable_conversation_states[:8]),
            lesson.reinforcement_count,
            lesson.contradiction_count,
            lesson.success_rate,
            lesson.created_at,
            lesson.updated_at,
            lesson.expires_at,
            int(lesson.suppressed),
            int(lesson.archived),
            lesson.content_hash,
        ),
    )
    return True, "created"


def contradict_lesson(lesson_id: str) -> None:
    ensure_ready()
    row = fetch_one("SELECT * FROM learned_lessons WHERE lesson_id=?", (lesson_id,))
    if not row:
        return
    d = dict(row)
    c = int(d.get("contradiction_count") or 0) + 1
    conf = max(0.05, float(d.get("confidence") or 0.4) - 0.12)
    suppressed = 1 if c >= 2 or conf < 0.25 else int(d.get("suppressed") or 0)
    exec_one(
        """
        UPDATE learned_lessons
        SET contradiction_count=?, confidence=?, suppressed=?, updated_at=?
        WHERE lesson_id=?
        """,
        (c, conf, suppressed, _now(), lesson_id),
    )


def decay_lessons(*, now: float | None = None, limit: int = 200) -> int:
    ensure_ready()
    ts = now or _now()
    rows = fetch_all(
        """
        SELECT lesson_id, confidence, updated_at, expires_at, reinforcement_count
        FROM learned_lessons
        WHERE suppressed=0 AND archived=0
        ORDER BY updated_at ASC LIMIT ?
        """,
        (limit,),
    )
    n = 0
    for r in rows:
        d = dict(r)
        age_days = max(0.0, (ts - float(d.get("updated_at") or ts)) / 86400.0)
        if d.get("expires_at") and float(d["expires_at"]) < ts:
            exec_one(
                "UPDATE learned_lessons SET archived=1, updated_at=? WHERE lesson_id=?",
                (ts, d["lesson_id"]),
            )
            n += 1
            continue
        if age_days < 7:
            continue
        decay = min(0.25, 0.015 * age_days)
        conf = max(0.05, float(d.get("confidence") or 0.4) - decay)
        suppressed = 1 if conf < 0.2 and int(d.get("reinforcement_count") or 1) < 3 else 0
        exec_one(
            "UPDATE learned_lessons SET confidence=?, suppressed=?, updated_at=? WHERE lesson_id=?",
            (conf, suppressed, ts, d["lesson_id"]),
        )
        n += 1
    return n


def list_active_lessons(
    *, user_id: str, limit: int = 12, include_global: bool = True
) -> list[LearnedLesson]:
    ensure_ready()
    ts = _now()
    if include_global:
        rows = fetch_all(
            """
            SELECT * FROM learned_lessons
            WHERE suppressed=0 AND archived=0
              AND (user_id=? OR (scope='global' AND user_id=''))
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY confidence DESC, reinforcement_count DESC
            LIMIT ?
            """,
            (user_id, ts, limit),
        )
    else:
        rows = fetch_all(
            """
            SELECT * FROM learned_lessons
            WHERE suppressed=0 AND archived=0 AND user_id=?
              AND (expires_at IS NULL OR expires_at > ?)
            ORDER BY confidence DESC LIMIT ?
            """,
            (user_id, ts, limit),
        )
    out: list[LearnedLesson] = []
    for r in rows:
        d = dict(r)
        out.append(
            LearnedLesson(
                lesson_id=d["lesson_id"],
                user_id=d.get("user_id") or "",
                scope=d.get("scope") or "user",
                trigger_turn_id=d.get("trigger_turn_id") or "",
                category=d.get("category") or "general",
                statement=d.get("statement") or "",
                machine_action=d.get("machine_action") or "",
                machine_action_payload=dict(_jl(d.get("machine_action_payload"), {})),
                confidence=float(d.get("confidence") or 0),
                evidence_count=int(d.get("evidence_count") or 0),
                positive_examples=list(_jl(d.get("positive_examples"), [])),
                negative_examples=list(_jl(d.get("negative_examples"), [])),
                applicable_intents=list(_jl(d.get("applicable_intents"), [])),
                applicable_strategies=list(_jl(d.get("applicable_strategies"), [])),
                applicable_tools=list(_jl(d.get("applicable_tools"), [])),
                applicable_conversation_states=list(
                    _jl(d.get("applicable_conversation_states"), [])
                ),
                reinforcement_count=int(d.get("reinforcement_count") or 1),
                contradiction_count=int(d.get("contradiction_count") or 0),
                success_rate=float(d.get("success_rate") or 0.5),
                created_at=float(d.get("created_at") or 0),
                updated_at=float(d.get("updated_at") or 0),
                expires_at=d.get("expires_at"),
                suppressed=bool(d.get("suppressed")),
                archived=bool(d.get("archived")),
                content_hash=d.get("content_hash") or "",
            )
        )
    return out


def insert_delayed_feedback(event: "DelayedFeedbackEvent") -> bool:
    """Persist delayed feedback (idempotent on feedback_turn_id+target)."""
    from aihub.adaptive_learning.models import DelayedFeedbackEvent as _Ev

    ensure_ready()
    if not isinstance(event, _Ev):
        return False
    fid = event.feedback_id or str(uuid.uuid4())
    ts = event.created_at or _now()
    try:
        exec_one(
            """
            INSERT INTO delayed_feedback(
                feedback_id, feedback_turn_id, target_turn_id, user_id, session_id,
                feedback_type, polarity, confidence, evidence, affected_dimensions,
                explicit_or_inferred, reason_codes, text_preview, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                fid,
                event.feedback_turn_id,
                event.target_turn_id,
                event.user_id or "",
                event.session_id or "",
                event.feedback_type or event.kind or "generic",
                event.polarity,
                event.confidence,
                event.evidence or "",
                _j(list(event.affected_dimensions or [])),
                event.explicit_or_inferred,
                _j(list(event.reason_codes or [])),
                (event.text_preview or "")[:240],
                ts,
            ),
        )
        return True
    except Exception:
        # Unique conflict — already stored
        return False


def get_delayed_feedback_for_target(target_turn_id: str) -> list[dict[str, Any]]:
    ensure_ready()
    rows = fetch_all(
        "SELECT * FROM delayed_feedback WHERE target_turn_id=? ORDER BY created_at DESC",
        (target_turn_id,),
    )
    return [dict(r) for r in rows]


def load_self_model(deployment_id: str = "default") -> RuntimeSelfModel:
    ensure_ready()
    row = fetch_one(
        "SELECT * FROM runtime_self_model WHERE deployment_id=?", (deployment_id,)
    )
    if not row:
        return RuntimeSelfModel(deployment_id=deployment_id)
    d = dict(row)
    payload = dict(_jl(d.get("payload_json"), {}))
    model = RuntimeSelfModel.model_validate(
        {**payload, "deployment_id": deployment_id, "version": int(d.get("version") or 1)}
    )
    model.updated_at = float(d.get("updated_at") or 0)
    return model


def save_self_model(model: RuntimeSelfModel) -> None:
    ensure_ready()
    model.updated_at = _now()
    payload = model.model_dump()
    payload.pop("deployment_id", None)
    exec_one(
        """
        INSERT INTO runtime_self_model(deployment_id, version, payload_json, updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(deployment_id) DO UPDATE SET
            version=excluded.version,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (model.deployment_id, model.version, _j(payload), model.updated_at),
    )


def load_user_model_v2(user_id: str) -> UserModelV2:
    ensure_ready()
    row = fetch_one("SELECT * FROM user_model_traits WHERE user_id=?", (user_id,))
    if not row:
        return UserModelV2(user_id=user_id)
    d = dict(row)
    payload = dict(_jl(d.get("payload_json"), {}))
    payload["user_id"] = user_id
    payload["version"] = int(d.get("version") or 1)
    payload["updated_at"] = float(d.get("updated_at") or 0)
    return UserModelV2.model_validate(payload)


def save_user_model_v2(model: UserModelV2) -> None:
    ensure_ready()
    model.updated_at = _now()
    payload = model.model_dump()
    exec_one(
        """
        INSERT INTO user_model_traits(user_id, version, payload_json, updated_at)
        VALUES(?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            version=excluded.version,
            payload_json=excluded.payload_json,
            updated_at=excluded.updated_at
        """,
        (model.user_id, model.version, _j(payload), model.updated_at),
    )


def bump_metric(
    table: str,
    metric_key: str,
    *,
    columns: dict[str, float | int | str],
) -> None:
    """Generic bounded metric upsert. columns must include updated_at."""
    ensure_ready()
    allowed = {
        "strategy_metrics",
        "planner_metrics",
        "tool_metrics",
        "provider_metrics",
        "research_metrics",
    }
    if table not in allowed:
        raise ValueError(f"unsupported metrics table: {table}")
    cols = dict(columns)
    cols["updated_at"] = float(cols.get("updated_at") or _now())
    existing = fetch_one(f"SELECT * FROM {table} WHERE metric_key=?", (metric_key,))
    if not existing:
        keys = ["metric_key", *cols.keys()]
        placeholders = ",".join("?" for _ in keys)
        exec_one(
            f"INSERT INTO {table}({','.join(keys)}) VALUES({placeholders})",
            (metric_key, *cols.values()),
        )
        return
    # Increment numeric sums / samples
    d = dict(existing)
    sets = []
    vals: list[Any] = []
    for k, v in cols.items():
        if k in ("user_id", "strategy", "intent", "domain", "tool_name", "provider", "query_hash", "raw_query", "rewritten_query"):
            sets.append(f"{k}=?")
            vals.append(v)
            continue
        if isinstance(v, (int, float)) and k != "updated_at":
            sets.append(f"{k}=?")
            vals.append(float(d.get(k) or 0) + float(v))
        else:
            sets.append(f"{k}=?")
            vals.append(v)
    vals.append(metric_key)
    exec_one(f"UPDATE {table} SET {', '.join(sets)} WHERE metric_key=?", tuple(vals))


def get_strategy_metric_rows(*, user_id: str, limit: int = 40) -> list[dict[str, Any]]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM strategy_metrics
        WHERE user_id=? OR user_id=''
        ORDER BY samples DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def get_tool_metric_rows(*, user_id: str = "", limit: int = 40) -> list[dict[str, Any]]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM tool_metrics
        WHERE user_id=? OR user_id=''
        ORDER BY samples DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def get_provider_metric_rows(*, user_id: str = "", limit: int = 20) -> list[dict[str, Any]]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM provider_metrics
        WHERE user_id=? OR user_id=''
        ORDER BY samples DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [dict(r) for r in rows]


def upsert_failure(fp: FailurePattern) -> str:
    ensure_ready()
    if not fp.content_hash:
        fp.content_hash = _hash_text(fp.user_id, fp.category, fp.trigger)
    existing = fetch_one(
        "SELECT * FROM failure_patterns WHERE content_hash=? AND user_id=?",
        (fp.content_hash, fp.user_id or ""),
    )
    ts = _now()
    if existing:
        d = dict(existing)
        exec_one(
            """
            UPDATE failure_patterns SET recurrence_count=?, last_seen=?, confidence=?,
                evidence=?, corrective_action=?, resolved=0
            WHERE failure_id=?
            """,
            (
                int(d.get("recurrence_count") or 1) + 1,
                ts,
                min(0.95, float(d.get("confidence") or 0.5) + 0.05),
                (fp.evidence or d.get("evidence") or "")[:400],
                (fp.corrective_action or d.get("corrective_action") or "")[:240],
                d["failure_id"],
            ),
        )
        return str(d["failure_id"])
    fp.failure_id = fp.failure_id or str(uuid.uuid4())
    fp.last_seen = ts
    exec_one(
        """
        INSERT INTO failure_patterns(
            failure_id, user_id, category, trigger_text, context_text, root_cause,
            evidence, affected_module, corrective_action, recurrence_count,
            last_seen, resolved, resolution_turn_id, confidence, content_hash
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            fp.failure_id,
            fp.user_id or "",
            fp.category,
            fp.trigger[:280],
            fp.context[:280],
            fp.root_cause[:280],
            fp.evidence[:400],
            fp.affected_module[:80],
            fp.corrective_action[:240],
            fp.recurrence_count,
            fp.last_seen,
            int(fp.resolved),
            fp.resolution_turn_id,
            fp.confidence,
            fp.content_hash,
        ),
    )
    return fp.failure_id


def list_relevant_failures(
    *, user_id: str, trigger_hint: str, limit: int = 8
) -> list[FailurePattern]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM failure_patterns
        WHERE resolved=0 AND (user_id=? OR user_id='')
        ORDER BY last_seen DESC LIMIT ?
        """,
        (user_id, max(limit * 3, 12)),
    )
    hint = (trigger_hint or "").lower()
    out: list[FailurePattern] = []
    for r in rows:
        d = dict(r)
        trig = str(d.get("trigger_text") or "").lower()
        if hint and hint[:40] not in trig and not any(
            tok and tok in trig for tok in hint.split()[:6]
        ):
            # keep high recurrence anyway
            if int(d.get("recurrence_count") or 0) < 2:
                continue
        out.append(
            FailurePattern(
                failure_id=d["failure_id"],
                user_id=d.get("user_id") or "",
                category=d.get("category") or "",
                trigger=d.get("trigger_text") or "",
                context=d.get("context_text") or "",
                root_cause=d.get("root_cause") or "",
                evidence=d.get("evidence") or "",
                affected_module=d.get("affected_module") or "",
                corrective_action=d.get("corrective_action") or "",
                recurrence_count=int(d.get("recurrence_count") or 1),
                last_seen=float(d.get("last_seen") or 0),
                resolved=bool(d.get("resolved")),
                resolution_turn_id=d.get("resolution_turn_id") or "",
                confidence=float(d.get("confidence") or 0.5),
                content_hash=d.get("content_hash") or "",
            )
        )
        if len(out) >= limit:
            break
    return out


def upsert_success(sp: SuccessPattern) -> str:
    ensure_ready()
    if not sp.content_hash:
        sp.content_hash = _hash_text(sp.user_id, sp.category, sp.pattern)
    existing = fetch_one(
        "SELECT * FROM success_patterns WHERE content_hash=? AND user_id=?",
        (sp.content_hash, sp.user_id or ""),
    )
    ts = _now()
    if existing:
        d = dict(existing)
        exec_one(
            """
            UPDATE success_patterns SET reinforcement_count=?, last_seen=?,
                confidence=?, success_rate=?
            WHERE success_id=?
            """,
            (
                int(d.get("reinforcement_count") or 1) + 1,
                ts,
                min(0.95, float(d.get("confidence") or 0.5) + 0.04),
                min(0.99, float(d.get("success_rate") or 0.7) * 0.9 + 0.1),
                d["success_id"],
            ),
        )
        return str(d["success_id"])
    sp.success_id = sp.success_id or str(uuid.uuid4())
    sp.last_seen = ts
    exec_one(
        """
        INSERT INTO success_patterns(
            success_id, user_id, category, pattern_text, context_text, evidence,
            reinforcement_count, success_rate, last_seen, confidence, content_hash
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            sp.success_id,
            sp.user_id or "",
            sp.category,
            sp.pattern[:320],
            sp.context[:280],
            sp.evidence[:400],
            sp.reinforcement_count,
            sp.success_rate,
            sp.last_seen,
            sp.confidence,
            sp.content_hash,
        ),
    )
    return sp.success_id


def list_success_patterns(*, user_id: str, limit: int = 8) -> list[SuccessPattern]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM success_patterns
        WHERE user_id=? OR user_id=''
        ORDER BY reinforcement_count DESC, last_seen DESC LIMIT ?
        """,
        (user_id, limit),
    )
    out: list[SuccessPattern] = []
    for r in rows:
        d = dict(r)
        out.append(
            SuccessPattern(
                success_id=d["success_id"],
                user_id=d.get("user_id") or "",
                category=d.get("category") or "",
                pattern=d.get("pattern_text") or "",
                context=d.get("context_text") or "",
                evidence=d.get("evidence") or "",
                reinforcement_count=int(d.get("reinforcement_count") or 1),
                success_rate=float(d.get("success_rate") or 0.7),
                last_seen=float(d.get("last_seen") or 0),
                confidence=float(d.get("confidence") or 0.5),
                content_hash=d.get("content_hash") or "",
            )
        )
    return out


def save_long_horizon_task(task: LongHorizonTask) -> None:
    ensure_ready()
    ts = _now()
    task.updated_at = ts
    if not task.created_at:
        task.created_at = ts
    exec_one(
        """
        INSERT INTO long_horizon_tasks(
            task_id, user_id, session_id, title, objective, constraints_json,
            accepted_decisions_json, rejected_decisions_json, current_stage,
            completed_steps_json, pending_steps_json, blockers_json, artifacts_json,
            dependencies_json, last_action, next_best_action, status, confidence,
            created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(task_id) DO UPDATE SET
            title=excluded.title,
            objective=excluded.objective,
            constraints_json=excluded.constraints_json,
            accepted_decisions_json=excluded.accepted_decisions_json,
            rejected_decisions_json=excluded.rejected_decisions_json,
            current_stage=excluded.current_stage,
            completed_steps_json=excluded.completed_steps_json,
            pending_steps_json=excluded.pending_steps_json,
            blockers_json=excluded.blockers_json,
            artifacts_json=excluded.artifacts_json,
            dependencies_json=excluded.dependencies_json,
            last_action=excluded.last_action,
            next_best_action=excluded.next_best_action,
            status=excluded.status,
            confidence=excluded.confidence,
            updated_at=excluded.updated_at
        """,
        (
            task.task_id,
            task.user_id,
            task.session_id,
            task.title[:200],
            task.objective[:400],
            _j(task.constraints[:12]),
            _j(task.accepted_decisions[-20:]),
            _j(task.rejected_decisions[-20:]),
            task.current_stage[:80],
            _j(task.completed_steps[-30:]),
            _j(task.pending_steps[:30]),
            _j(task.blockers[:12]),
            _j(task.artifacts[:12]),
            _j(task.dependencies[:12]),
            task.last_action[:200],
            task.next_best_action[:200],
            task.status,
            task.confidence,
            task.created_at,
            task.updated_at,
        ),
    )


def get_active_long_horizon_task(
    *, user_id: str, session_id: str = "", allow_cross_session: bool = True
) -> LongHorizonTask | None:
    """Return the newest active long-horizon task for the user.

    Prefer the current session when present, but fall back to any active
    user-level task so status/next-step questions work across new sessions.
    """
    ensure_ready()
    if session_id:
        row = fetch_one(
            """
            SELECT * FROM long_horizon_tasks
            WHERE user_id=? AND session_id=? AND status IN ('active','blocked','paused')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (user_id, session_id),
        )
        if row:
            return _row_to_task(row)
        if not allow_cross_session:
            return None
    row = fetch_one(
        """
        SELECT * FROM long_horizon_tasks
        WHERE user_id=? AND status IN ('active','blocked','paused')
        ORDER BY updated_at DESC LIMIT 1
        """,
        (user_id,),
    )
    if not row:
        return None
    return _row_to_task(row)


def find_long_horizon_task_by_marker(
    *, user_id: str, marker: str
) -> LongHorizonTask | None:
    """Locate an active task whose title/objective mentions a distinctive marker."""
    ensure_ready()
    marker = (marker or "").strip()
    if len(marker) < 4:
        return None
    rows = fetch_all(
        """
        SELECT * FROM long_horizon_tasks
        WHERE user_id=? AND status IN ('active','blocked','paused')
        ORDER BY updated_at DESC LIMIT 25
        """,
        (user_id,),
    )
    needle = marker.lower()
    for row in rows:
        blob = f"{row['title'] or ''} {row['objective'] or ''}".lower()
        if needle in blob:
            return _row_to_task(row)
    return None


def format_long_horizon_brief(task: LongHorizonTask | None, *, max_chars: int = 900) -> str:
    """Canonical prompt brief for an active long-horizon task (not just an opaque id)."""
    if task is None:
        return ""
    lines = [
        f"ZADANIE DŁUGOTERMINOWE [{task.task_id}]",
        f"- title: {task.title}",
        f"- status: {task.status}",
        f"- stage: {task.current_stage}",
    ]
    if task.objective:
        lines.append(f"- objective: {task.objective[:220]}")
    if task.next_best_action:
        lines.append(f"- next_step: {task.next_best_action[:180]}")
    if task.pending_steps:
        lines.append("- pending: " + " | ".join(str(s)[:80] for s in task.pending_steps[:4]))
    if task.completed_steps:
        lines.append("- completed: " + " | ".join(str(s)[:80] for s in task.completed_steps[-3:]))
    if task.blockers:
        lines.append("- blockers: " + " | ".join(str(b)[:80] for b in task.blockers[:3]))
    if task.accepted_decisions:
        lines.append("- accepted: " + " | ".join(str(a)[:80] for a in task.accepted_decisions[-3:]))
    if task.rejected_decisions:
        lines.append("- rejected: " + " | ".join(str(r)[:80] for r in task.rejected_decisions[-3:]))
    text = "\n".join(lines)
    return text[: max(200, int(max_chars))]


def _row_to_task(row: Any) -> LongHorizonTask:
    d = dict(row)
    return LongHorizonTask(
        task_id=d["task_id"],
        user_id=d["user_id"],
        session_id=d.get("session_id") or "",
        title=d.get("title") or "",
        objective=d.get("objective") or "",
        constraints=list(_jl(d.get("constraints_json"), [])),
        accepted_decisions=list(_jl(d.get("accepted_decisions_json"), [])),
        rejected_decisions=list(_jl(d.get("rejected_decisions_json"), [])),
        current_stage=d.get("current_stage") or "init",
        completed_steps=list(_jl(d.get("completed_steps_json"), [])),
        pending_steps=list(_jl(d.get("pending_steps_json"), [])),
        blockers=list(_jl(d.get("blockers_json"), [])),
        artifacts=list(_jl(d.get("artifacts_json"), [])),
        dependencies=list(_jl(d.get("dependencies_json"), [])),
        last_action=d.get("last_action") or "",
        next_best_action=d.get("next_best_action") or "",
        status=d.get("status") or "active",
        confidence=float(d.get("confidence") or 0.5),
        created_at=float(d.get("created_at") or 0),
        updated_at=float(d.get("updated_at") or 0),
    )


def append_task_event(
    *, task_id: str, turn_id: str, event_type: str, payload: dict[str, Any]
) -> None:
    ensure_ready()
    exec_one(
        """
        INSERT INTO task_state_events(id, task_id, turn_id, event_type, payload_json, created_at)
        VALUES (?,?,?,?,?,?)
        """,
        (str(uuid.uuid4()), task_id, turn_id, event_type[:80], _j(payload), _now()),
    )


content_hash = _hash_text
