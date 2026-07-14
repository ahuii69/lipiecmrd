"""World knowledge schema ensure (SQLite + Postgres)."""

from __future__ import annotations

import logging
import os

from aihub.db import _DB_LOCK, _conn

log = logging.getLogger(__name__)

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS wk_entities (
        entity_id TEXT PRIMARY KEY,
        canonical_name TEXT NOT NULL,
        entity_type TEXT NOT NULL DEFAULT 'concept',
        aliases_json TEXT NOT NULL DEFAULT '[]',
        description TEXT NOT NULL DEFAULT '',
        scope TEXT NOT NULL DEFAULT 'user',
        user_id TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.5,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        merged_into_entity_id TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        name_norm TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wk_ent_user_type ON wk_entities(user_id, entity_type, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wk_ent_name_norm ON wk_entities(user_id, name_norm)",
    """
    CREATE TABLE IF NOT EXISTS wk_entity_aliases (
        id TEXT PRIMARY KEY,
        entity_id TEXT NOT NULL,
        user_id TEXT NOT NULL DEFAULT '',
        alias TEXT NOT NULL,
        alias_norm TEXT NOT NULL,
        created_at REAL NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wk_alias_user_norm ON wk_entity_aliases(user_id, alias_norm)",
    "CREATE INDEX IF NOT EXISTS idx_wk_alias_entity ON wk_entity_aliases(entity_id)",
    """
    CREATE TABLE IF NOT EXISTS wk_claims (
        claim_id TEXT PRIMARY KEY,
        subject_entity_id TEXT NOT NULL DEFAULT '',
        predicate TEXT NOT NULL DEFAULT '',
        object_entity_id TEXT NOT NULL DEFAULT '',
        literal_value TEXT NOT NULL DEFAULT '',
        value_type TEXT NOT NULL DEFAULT 'text',
        claim_type TEXT NOT NULL DEFAULT 'fact',
        scope TEXT NOT NULL DEFAULT 'user',
        user_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.4,
        status TEXT NOT NULL DEFAULT 'proposed',
        valid_from REAL,
        valid_until REAL,
        observed_at REAL NOT NULL,
        last_verified_at REAL,
        verification_due_at REAL,
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        supporting_evidence_count INTEGER NOT NULL DEFAULT 0,
        contradicting_evidence_count INTEGER NOT NULL DEFAULT 0,
        source_diversity_count INTEGER NOT NULL DEFAULT 0,
        content_hash TEXT NOT NULL DEFAULT '',
        statement TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wk_claim_hash_user ON wk_claims(content_hash, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_wk_claim_user_status ON wk_claims(user_id, status, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_wk_claim_subject ON wk_claims(subject_entity_id, status)",
    """
    CREATE TABLE IF NOT EXISTS wk_relations (
        relation_id TEXT PRIMARY KEY,
        subject_entity_id TEXT NOT NULL,
        predicate TEXT NOT NULL,
        object_entity_id TEXT NOT NULL,
        claim_id TEXT NOT NULL DEFAULT '',
        confidence REAL NOT NULL DEFAULT 0.5,
        valid_from REAL,
        valid_until REAL,
        status TEXT NOT NULL DEFAULT 'active',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        user_id TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wk_rel_spo_user ON wk_relations(user_id, subject_entity_id, predicate, object_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_wk_rel_user ON wk_relations(user_id, status, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS wk_evidence (
        evidence_id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        task_id TEXT NOT NULL DEFAULT '',
        source_type TEXT NOT NULL DEFAULT 'user_statement',
        source_uri TEXT NOT NULL DEFAULT '',
        source_title TEXT NOT NULL DEFAULT '',
        source_domain TEXT NOT NULL DEFAULT '',
        source_author TEXT NOT NULL DEFAULT '',
        source_published_at REAL,
        retrieved_at REAL NOT NULL,
        content_hash TEXT NOT NULL DEFAULT '',
        excerpt TEXT NOT NULL DEFAULT '',
        claim_ids_json TEXT NOT NULL DEFAULT '[]',
        reliability_score REAL NOT NULL DEFAULT 0.5,
        freshness_score REAL NOT NULL DEFAULT 0.5,
        relevance_score REAL NOT NULL DEFAULT 0.5,
        corroboration_score REAL NOT NULL DEFAULT 0,
        overall_score REAL NOT NULL DEFAULT 0.5,
        language TEXT NOT NULL DEFAULT 'pl',
        is_primary_source INTEGER NOT NULL DEFAULT 0,
        is_user_provided INTEGER NOT NULL DEFAULT 0,
        is_tool_result INTEGER NOT NULL DEFAULT 0,
        is_inference INTEGER NOT NULL DEFAULT 0,
        expires_at REAL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wk_ev_hash_user ON wk_evidence(content_hash, user_id)",
    "CREATE INDEX IF NOT EXISTS idx_wk_ev_user_ts ON wk_evidence(user_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS wk_claim_evidence (
        id TEXT PRIMARY KEY,
        claim_id TEXT NOT NULL,
        evidence_id TEXT NOT NULL,
        link_type TEXT NOT NULL DEFAULT 'supports',
        created_at REAL NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wk_ce_pair ON wk_claim_evidence(claim_id, evidence_id)",
    """
    CREATE TABLE IF NOT EXISTS wk_conflicts (
        conflict_id TEXT PRIMARY KEY,
        claim_a_id TEXT NOT NULL,
        claim_b_id TEXT NOT NULL,
        conflict_type TEXT NOT NULL,
        severity REAL NOT NULL DEFAULT 0.5,
        confidence REAL NOT NULL DEFAULT 0.5,
        resolution_status TEXT NOT NULL DEFAULT 'open',
        preferred_claim_id TEXT NOT NULL DEFAULT '',
        resolution_reason TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        resolved_at REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wk_conf_user ON wk_conflicts(user_id, resolution_status, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS wk_knowledge_events (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wk_events_user ON wk_knowledge_events(user_id, created_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS wk_execution_graphs (
        execution_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL DEFAULT '',
        goal_id TEXT NOT NULL DEFAULT '',
        user_id TEXT NOT NULL DEFAULT '',
        session_id TEXT NOT NULL DEFAULT '',
        turn_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        nodes_json TEXT NOT NULL DEFAULT '[]',
        edges_json TEXT NOT NULL DEFAULT '[]',
        current_node TEXT NOT NULL DEFAULT '',
        completed_nodes_json TEXT NOT NULL DEFAULT '[]',
        failed_nodes_json TEXT NOT NULL DEFAULT '[]',
        blocked_nodes_json TEXT NOT NULL DEFAULT '[]',
        replan_count INTEGER NOT NULL DEFAULT 0,
        lease_owner TEXT NOT NULL DEFAULT '',
        lease_until REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_wk_exec_user_status ON wk_execution_graphs(user_id, status, updated_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS wk_execution_effects (
        effect_id TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'completed',
        summary TEXT NOT NULL DEFAULT '',
        evidence_id TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )
    """,
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_wk_effect_idem ON wk_execution_effects(idempotency_key)",
    """
    CREATE TABLE IF NOT EXISTS wk_execution_validations (
        validation_id TEXT PRIMARY KEY,
        execution_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        rule TEXT NOT NULL DEFAULT '',
        succeeded INTEGER NOT NULL DEFAULT 0,
        evidence_id TEXT NOT NULL DEFAULT '',
        detail TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL
    )
    """,
]


def ensure_world_knowledge_schema() -> None:
    """Idempotent DDL for world knowledge tables."""
    backend = (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip()
    try:
        with _DB_LOCK, _conn() as con:
            for stmt in _DDL:
                try:
                    con.execute(stmt)
                except Exception as exc:
                    msg = str(exc).lower()
                    if "duplicate" in msg or "already exists" in msg:
                        try:
                            con.rollback()
                        except Exception:
                            log.debug("wk schema rollback after duplicate")
                        continue
                    if backend == "postgres":
                        try:
                            con.rollback()
                        except Exception:
                            log.debug("wk schema rollback")
                        # retry once after rollback for aborted txn
                        try:
                            con.execute(stmt)
                            continue
                        except Exception as exc2:
                            msg2 = str(exc2).lower()
                            if "duplicate" in msg2 or "already exists" in msg2:
                                try:
                                    con.rollback()
                                except Exception:
                                    log.debug("wk schema rollback2")
                                continue
                            raise
                    raise
            con.commit()
    except Exception:
        log.exception("world knowledge schema ensure failed")
        raise
