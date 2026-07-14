"""Adaptive learning schema ensure (SQLite + Postgres via db adapters)."""

from __future__ import annotations

import logging

from aihub.db import _DB_LOCK, _conn

log = logging.getLogger(__name__)

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS turn_outcomes (
        turn_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        primary_intent TEXT NOT NULL DEFAULT 'unknown',
        selected_strategy TEXT NOT NULL DEFAULT 'contextual',
        planner_used INTEGER NOT NULL DEFAULT 0,
        reasoning_used INTEGER NOT NULL DEFAULT 0,
        web_used INTEGER NOT NULL DEFAULT 0,
        tools_used INTEGER NOT NULL DEFAULT 0,
        response_critic_score REAL,
        final_response_quality REAL NOT NULL DEFAULT 0.5,
        user_satisfaction_signal REAL NOT NULL DEFAULT 0,
        correction_signal REAL NOT NULL DEFAULT 0,
        rejection_signal REAL NOT NULL DEFAULT 0,
        continuation_signal REAL NOT NULL DEFAULT 0,
        acceptance_signal REAL NOT NULL DEFAULT 0,
        task_completion_signal REAL NOT NULL DEFAULT 0,
        factual_grounding_score REAL NOT NULL DEFAULT 0.5,
        style_match_score REAL NOT NULL DEFAULT 0.5,
        intent_match_score REAL NOT NULL DEFAULT 0.5,
        verbosity_match_score REAL NOT NULL DEFAULT 0.5,
        tool_success_score REAL NOT NULL DEFAULT 0.5,
        research_quality_score REAL NOT NULL DEFAULT 0.5,
        latency_score REAL NOT NULL DEFAULT 0.5,
        cost_score REAL NOT NULL DEFAULT 0.5,
        overall_reward REAL NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0.4,
        reason_codes TEXT NOT NULL DEFAULT '[]',
        degraded INTEGER NOT NULL DEFAULT 0,
        delayed_feedback_applied INTEGER NOT NULL DEFAULT 0,
        message_preview TEXT NOT NULL DEFAULT '',
        response_preview TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_turn_outcomes_user_ts ON turn_outcomes(user_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_turn_outcomes_session_ts ON turn_outcomes(session_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS causal_attributions (
        id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        factor TEXT NOT NULL,
        contribution_score REAL NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0.3,
        evidence TEXT NOT NULL DEFAULT '',
        evidence_kind TEXT NOT NULL DEFAULT 'inferred',
        positive_or_negative TEXT NOT NULL DEFAULT 'neutral',
        corrective_action TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'user',
        expiry REAL,
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_causal_turn ON causal_attributions(turn_id)",
    "CREATE INDEX IF NOT EXISTS idx_causal_user_ts ON causal_attributions(user_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS learned_lessons (
        lesson_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'user',
        trigger_turn_id TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL DEFAULT 'general',
        statement TEXT NOT NULL,
        machine_action TEXT NOT NULL DEFAULT '',
        machine_action_payload TEXT NOT NULL DEFAULT '{}',
        confidence REAL NOT NULL DEFAULT 0.4,
        evidence_count INTEGER NOT NULL DEFAULT 1,
        positive_examples TEXT NOT NULL DEFAULT '[]',
        negative_examples TEXT NOT NULL DEFAULT '[]',
        applicable_intents TEXT NOT NULL DEFAULT '[]',
        applicable_strategies TEXT NOT NULL DEFAULT '[]',
        applicable_tools TEXT NOT NULL DEFAULT '[]',
        applicable_conversation_states TEXT NOT NULL DEFAULT '[]',
        reinforcement_count INTEGER NOT NULL DEFAULT 1,
        contradiction_count INTEGER NOT NULL DEFAULT 0,
        success_rate REAL NOT NULL DEFAULT 0.5,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        expires_at REAL,
        suppressed INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        content_hash TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_hash_scope_user ON learned_lessons(content_hash, scope, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_lessons_user_conf ON learned_lessons(user_id, confidence DESC, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lessons_scope ON learned_lessons(scope, suppressed, archived)",
    """
    CREATE TABLE IF NOT EXISTS delayed_feedback (
        feedback_id TEXT PRIMARY KEY,
        feedback_turn_id TEXT NOT NULL,
        target_turn_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        feedback_type TEXT NOT NULL DEFAULT 'generic',
        polarity TEXT NOT NULL DEFAULT 'neutral',
        confidence REAL NOT NULL DEFAULT 0.5,
        evidence TEXT NOT NULL DEFAULT '',
        affected_dimensions TEXT NOT NULL DEFAULT '[]',
        explicit_or_inferred TEXT NOT NULL DEFAULT 'inferred',
        reason_codes TEXT NOT NULL DEFAULT '[]',
        text_preview TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_delayed_fb_target ON delayed_feedback(target_turn_id, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_delayed_fb_user_sess ON delayed_feedback(user_id, session_id, created_at DESC)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_delayed_fb_unique ON delayed_feedback(feedback_turn_id, target_turn_id)",
    """
    CREATE TABLE IF NOT EXISTS runtime_self_model (
        deployment_id TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        payload_json TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS strategy_metrics (
        metric_key TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        strategy TEXT NOT NULL,
        intent TEXT NOT NULL DEFAULT '',
        domain TEXT NOT NULL DEFAULT '',
        samples INTEGER NOT NULL DEFAULT 0,
        success_sum REAL NOT NULL DEFAULT 0,
        correction_sum REAL NOT NULL DEFAULT 0,
        rejection_sum REAL NOT NULL DEFAULT 0,
        critic_sum REAL NOT NULL DEFAULT 0,
        latency_sum REAL NOT NULL DEFAULT 0,
        reward_sum REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_strat_metrics_user ON strategy_metrics(user_id, strategy)",
    """
    CREATE TABLE IF NOT EXISTS planner_metrics (
        metric_key TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        samples INTEGER NOT NULL DEFAULT 0,
        plan_quality_sum REAL NOT NULL DEFAULT 0,
        step_count_sum REAL NOT NULL DEFAULT 0,
        unnecessary_steps_sum REAL NOT NULL DEFAULT 0,
        missing_steps_sum REAL NOT NULL DEFAULT 0,
        execution_success_sum REAL NOT NULL DEFAULT 0,
        user_acceptance_sum REAL NOT NULL DEFAULT 0,
        correction_sum REAL NOT NULL DEFAULT 0,
        tool_order_quality_sum REAL NOT NULL DEFAULT 0,
        completion_sum REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tool_metrics (
        metric_key TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        tool_name TEXT NOT NULL,
        samples INTEGER NOT NULL DEFAULT 0,
        success_sum REAL NOT NULL DEFAULT 0,
        timeout_sum REAL NOT NULL DEFAULT 0,
        malformed_sum REAL NOT NULL DEFAULT 0,
        empty_sum REAL NOT NULL DEFAULT 0,
        retry_success_sum REAL NOT NULL DEFAULT 0,
        latency_sum REAL NOT NULL DEFAULT 0,
        usefulness_sum REAL NOT NULL DEFAULT 0,
        side_effect_fail_sum REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tool_metrics_name ON tool_metrics(tool_name, user_id)",
    """
    CREATE TABLE IF NOT EXISTS provider_metrics (
        metric_key TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        provider TEXT NOT NULL,
        samples INTEGER NOT NULL DEFAULT 0,
        success_sum REAL NOT NULL DEFAULT 0,
        timeout_sum REAL NOT NULL DEFAULT 0,
        malformed_sum REAL NOT NULL DEFAULT 0,
        critic_sum REAL NOT NULL DEFAULT 0,
        latency_sum REAL NOT NULL DEFAULT 0,
        tool_calling_sum REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS research_metrics (
        metric_key TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        query_hash TEXT NOT NULL,
        raw_query TEXT NOT NULL DEFAULT '',
        rewritten_query TEXT NOT NULL DEFAULT '',
        samples INTEGER NOT NULL DEFAULT 0,
        result_count_sum REAL NOT NULL DEFAULT 0,
        useful_sum REAL NOT NULL DEFAULT 0,
        source_quality_sum REAL NOT NULL DEFAULT 0,
        grounding_sum REAL NOT NULL DEFAULT 0,
        acceptance_sum REAL NOT NULL DEFAULT 0,
        correction_sum REAL NOT NULL DEFAULT 0,
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_model_traits (
        user_id TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        payload_json TEXT NOT NULL DEFAULT '{}',
        updated_at REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS failure_patterns (
        failure_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL,
        trigger_text TEXT NOT NULL,
        context_text TEXT NOT NULL DEFAULT '',
        root_cause TEXT NOT NULL DEFAULT '',
        evidence TEXT NOT NULL DEFAULT '',
        affected_module TEXT NOT NULL DEFAULT '',
        corrective_action TEXT NOT NULL DEFAULT '',
        recurrence_count INTEGER NOT NULL DEFAULT 1,
        last_seen REAL NOT NULL,
        resolved INTEGER NOT NULL DEFAULT 0,
        resolution_turn_id TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.5,
        content_hash TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_failure_hash_user ON failure_patterns(content_hash, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_failure_user_seen ON failure_patterns(user_id, last_seen DESC)",
    """
    CREATE TABLE IF NOT EXISTS success_patterns (
        success_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        category TEXT NOT NULL,
        pattern_text TEXT NOT NULL,
        context_text TEXT NOT NULL DEFAULT '',
        evidence TEXT NOT NULL DEFAULT '',
        reinforcement_count INTEGER NOT NULL DEFAULT 1,
        success_rate REAL NOT NULL DEFAULT 0.7,
        last_seen REAL NOT NULL,
        confidence REAL NOT NULL DEFAULT 0.5,
        content_hash TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_success_hash_user ON success_patterns(content_hash, user_id)",
    """
    CREATE TABLE IF NOT EXISTS long_horizon_tasks (
        task_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL,
        objective TEXT NOT NULL DEFAULT '',
        constraints_json TEXT NOT NULL DEFAULT '[]',
        accepted_decisions_json TEXT NOT NULL DEFAULT '[]',
        rejected_decisions_json TEXT NOT NULL DEFAULT '[]',
        current_stage TEXT NOT NULL DEFAULT 'init',
        completed_steps_json TEXT NOT NULL DEFAULT '[]',
        pending_steps_json TEXT NOT NULL DEFAULT '[]',
        blockers_json TEXT NOT NULL DEFAULT '[]',
        artifacts_json TEXT NOT NULL DEFAULT '[]',
        dependencies_json TEXT NOT NULL DEFAULT '[]',
        last_action TEXT NOT NULL DEFAULT '',
        next_best_action TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active',
        confidence REAL NOT NULL DEFAULT 0.5,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_lht_user_status ON long_horizon_tasks(user_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_lht_session ON long_horizon_tasks(session_id, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS task_state_events (
        id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        turn_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_task_events_task ON task_state_events(task_id, created_at DESC)",
]


def ensure_adaptive_learning_schema() -> None:
    """Idempotent DDL. Always runs CREATE IF NOT EXISTS (tests swap DB files)."""
    import os

    backend = (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip()
    try:
        with _DB_LOCK, _conn() as con:
            for stmt in _DDL:
                con.execute(stmt)

            cols: set[str] = set()
            if backend == "postgres":
                try:
                    rows = con.execute(
                        """
                        SELECT column_name AS name
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'learned_lessons'
                        """
                    ).fetchall()
                    for r in rows:
                        name = r["name"] if hasattr(r, "keys") or isinstance(r, dict) else r[0]
                        if name:
                            cols.add(str(name))
                except Exception as info_exc:
                    log.debug("adaptive_learning information_schema skip: %s", info_exc)
                    try:
                        con.rollback()
                    except Exception:
                        log.debug("adaptive_learning rollback after info_schema miss")
            else:
                try:
                    for r in con.execute("PRAGMA table_info(learned_lessons)").fetchall():
                        name = r[1] if not isinstance(r, dict) else r.get("name")
                        if name:
                            cols.add(str(name))
                except Exception as pragma_exc:
                    log.debug("adaptive_learning pragma skip: %s", pragma_exc)
                    try:
                        con.rollback()
                    except Exception:
                        log.debug("adaptive_learning rollback after pragma miss")

            def _add_column(col: str, ddl_type: str) -> None:
                if cols and col in cols:
                    return
                # SQLite: plain ALTER; Postgres: IF NOT EXISTS when available
                stmts = []
                if backend == "postgres":
                    stmts.append(
                        f"ALTER TABLE learned_lessons ADD COLUMN IF NOT EXISTS {col} {ddl_type}"
                    )
                else:
                    stmts.append(f"ALTER TABLE learned_lessons ADD COLUMN {col} {ddl_type}")
                for stmt in stmts:
                    try:
                        con.execute(stmt)
                    except Exception as alter_exc:
                        msg = str(alter_exc).lower()
                        if "duplicate" in msg or "exists" in msg or "already" in msg:
                            try:
                                con.rollback()
                            except Exception:
                                log.debug("adaptive_learning alter duplicate rollback")
                            return
                        try:
                            con.rollback()
                        except Exception:
                            log.debug("adaptive_learning alter fail rollback")
                        raise

            _add_column("machine_action", "TEXT NOT NULL DEFAULT ''")
            _add_column("machine_action_payload", "TEXT NOT NULL DEFAULT '{}'")
            con.commit()
    except Exception:
        log.exception("adaptive learning schema ensure failed")
        raise


# Column upgrades are handled inside ensure_adaptive_learning_schema

