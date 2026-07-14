"""Persistence for world knowledge tables."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid
from typing import Any

from aihub.db import exec_one, fetch_all, fetch_one, json_dumps, json_loads
from aihub.world_knowledge.models import (
    ClaimConflict,
    EvidenceRecord,
    ExecutionGraph,
    ExecutionNode,
    KnowledgeClaim,
    KnowledgeEntity,
    KnowledgeRelation,
)
from aihub.world_knowledge.schema import ensure_world_knowledge_schema

log = logging.getLogger(__name__)


def _now() -> float:
    return time.time()


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


def content_hash(*parts: str) -> str:
    blob = "|".join(str(p or "").strip().lower() for p in parts)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def normalize_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\s\-.:/]", "", s, flags=re.UNICODE)
    return s[:120]


def ensure_ready() -> None:
    ensure_world_knowledge_schema()


def append_event(user_id: str, event_type: str, payload: dict[str, Any]) -> None:
    ensure_ready()
    exec_one(
        """
        INSERT INTO wk_knowledge_events(id, user_id, event_type, payload_json, created_at)
        VALUES(?,?,?,?,?)
        """,
        (str(uuid.uuid4()), user_id or "", event_type, _j(payload), _now()),
    )


def upsert_entity(entity: KnowledgeEntity) -> KnowledgeEntity:
    ensure_ready()
    ts = _now()
    entity.created_at = entity.created_at or ts
    entity.updated_at = ts
    name_norm = normalize_name(entity.canonical_name)
    exec_one(
        """
        INSERT INTO wk_entities(
            entity_id, canonical_name, entity_type, aliases_json, description,
            scope, user_id, confidence, created_at, updated_at,
            merged_into_entity_id, metadata_json, name_norm
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(entity_id) DO UPDATE SET
            canonical_name=excluded.canonical_name,
            entity_type=excluded.entity_type,
            aliases_json=excluded.aliases_json,
            description=excluded.description,
            confidence=excluded.confidence,
            updated_at=excluded.updated_at,
            merged_into_entity_id=excluded.merged_into_entity_id,
            metadata_json=excluded.metadata_json,
            name_norm=excluded.name_norm
        """,
        (
            entity.entity_id,
            entity.canonical_name[:160],
            entity.entity_type,
            _j(entity.aliases[:20]),
            entity.description[:400],
            entity.scope,
            entity.user_id or "",
            entity.confidence,
            entity.created_at,
            entity.updated_at,
            entity.merged_into_entity_id or "",
            _j(entity.metadata),
            name_norm,
        ),
    )
    for alias in [entity.canonical_name, *entity.aliases]:
        an = normalize_name(alias)
        if not an:
            continue
        try:
            exec_one(
                """
                INSERT INTO wk_entity_aliases(id, entity_id, user_id, alias, alias_norm, created_at)
                VALUES(?,?,?,?,?,?)
                """,
                (str(uuid.uuid4()), entity.entity_id, entity.user_id or "", alias[:160], an, ts),
            )
        except Exception as alias_exc:
            # unique alias already bound — keep existing mapping
            log.debug("alias insert skipped: %s", alias_exc)
    return entity


def find_entity_by_alias(*, user_id: str, name: str) -> KnowledgeEntity | None:
    ensure_ready()
    an = normalize_name(name)
    if not an:
        return None
    row = fetch_one(
        """
        SELECT e.* FROM wk_entity_aliases a
        JOIN wk_entities e ON e.entity_id = a.entity_id
        WHERE a.user_id=? AND a.alias_norm=?
          AND (e.merged_into_entity_id IS NULL OR e.merged_into_entity_id='')
        LIMIT 1
        """,
        (user_id or "", an),
    )
    if not row:
        row = fetch_one(
            """
            SELECT * FROM wk_entities
            WHERE user_id=? AND name_norm=?
              AND (merged_into_entity_id IS NULL OR merged_into_entity_id='')
            LIMIT 1
            """,
            (user_id or "", an),
        )
    return _row_entity(row) if row else None


def get_entity(entity_id: str) -> KnowledgeEntity | None:
    ensure_ready()
    row = fetch_one("SELECT * FROM wk_entities WHERE entity_id=?", (entity_id,))
    return _row_entity(row) if row else None


def _row_entity(row: Any) -> KnowledgeEntity:
    d = dict(row)
    return KnowledgeEntity(
        entity_id=d["entity_id"],
        canonical_name=d.get("canonical_name") or "",
        entity_type=d.get("entity_type") or "concept",
        aliases=list(_jl(d.get("aliases_json"), [])),
        description=d.get("description") or "",
        scope=d.get("scope") or "user",
        user_id=d.get("user_id") or "",
        confidence=float(d.get("confidence") or 0.5),
        created_at=float(d.get("created_at") or 0),
        updated_at=float(d.get("updated_at") or 0),
        merged_into_entity_id=d.get("merged_into_entity_id") or "",
        metadata=dict(_jl(d.get("metadata_json"), {})),
    )


def merge_entities(
    *, keep_id: str, remove_id: str, user_id: str, reason: str = "alias_merge"
) -> bool:
    ensure_ready()
    keep = get_entity(keep_id)
    rem = get_entity(remove_id)
    if not keep or not rem:
        return False
    if keep.user_id != rem.user_id or keep.user_id != user_id:
        return False
    aliases = list(dict.fromkeys(keep.aliases + rem.aliases + [rem.canonical_name]))
    keep.aliases = aliases[:30]
    upsert_entity(keep)
    rem.merged_into_entity_id = keep_id
    upsert_entity(rem)
    exec_one(
        "UPDATE wk_entity_aliases SET entity_id=? WHERE entity_id=?",
        (keep_id, remove_id),
    )
    append_event(
        user_id,
        "entity_merge",
        {"keep": keep_id, "remove": remove_id, "reason": reason},
    )
    return True


def upsert_evidence(ev: EvidenceRecord) -> tuple[bool, str]:
    ensure_ready()
    if not ev.content_hash:
        ev.content_hash = content_hash(ev.user_id, ev.source_type, ev.excerpt, ev.source_uri)
    existing = fetch_one(
        "SELECT evidence_id FROM wk_evidence WHERE content_hash=? AND user_id=?",
        (ev.content_hash, ev.user_id or ""),
    )
    if existing:
        return False, dict(existing)["evidence_id"]
    ts = _now()
    ev.evidence_id = ev.evidence_id or str(uuid.uuid4())
    ev.created_at = ev.created_at or ts
    ev.retrieved_at = ev.retrieved_at or ts
    exec_one(
        """
        INSERT INTO wk_evidence(
            evidence_id, turn_id, user_id, session_id, task_id, source_type,
            source_uri, source_title, source_domain, source_author, source_published_at,
            retrieved_at, content_hash, excerpt, claim_ids_json,
            reliability_score, freshness_score, relevance_score, corroboration_score,
            overall_score, language, is_primary_source, is_user_provided,
            is_tool_result, is_inference, expires_at, metadata_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ev.evidence_id,
            ev.turn_id,
            ev.user_id or "",
            ev.session_id or "",
            ev.task_id or "",
            ev.source_type,
            ev.source_uri[:400],
            ev.source_title[:200],
            ev.source_domain[:120],
            ev.source_author[:120],
            ev.source_published_at,
            ev.retrieved_at,
            ev.content_hash,
            ev.excerpt[:800],
            _j(ev.claim_ids[:20]),
            ev.reliability_score,
            ev.freshness_score,
            ev.relevance_score,
            ev.corroboration_score,
            ev.overall_score,
            ev.language,
            int(ev.is_primary_source),
            int(ev.is_user_provided),
            int(ev.is_tool_result),
            int(ev.is_inference),
            ev.expires_at,
            _j(ev.metadata),
            ev.created_at,
        ),
    )
    return True, ev.evidence_id


def get_evidence(evidence_id: str) -> EvidenceRecord | None:
    ensure_ready()
    row = fetch_one("SELECT * FROM wk_evidence WHERE evidence_id=?", (evidence_id,))
    return _row_evidence(row) if row else None


def _row_evidence(row: Any) -> EvidenceRecord:
    d = dict(row)
    return EvidenceRecord(
        evidence_id=d["evidence_id"],
        turn_id=d.get("turn_id") or "",
        user_id=d.get("user_id") or "",
        session_id=d.get("session_id") or "",
        task_id=d.get("task_id") or "",
        source_type=d.get("source_type") or "user_statement",
        source_uri=d.get("source_uri") or "",
        source_title=d.get("source_title") or "",
        source_domain=d.get("source_domain") or "",
        source_author=d.get("source_author") or "",
        source_published_at=d.get("source_published_at"),
        retrieved_at=float(d.get("retrieved_at") or 0),
        content_hash=d.get("content_hash") or "",
        excerpt=d.get("excerpt") or "",
        claim_ids=list(_jl(d.get("claim_ids_json"), [])),
        reliability_score=float(d.get("reliability_score") or 0.5),
        freshness_score=float(d.get("freshness_score") or 0.5),
        relevance_score=float(d.get("relevance_score") or 0.5),
        corroboration_score=float(d.get("corroboration_score") or 0),
        overall_score=float(d.get("overall_score") or 0.5),
        language=d.get("language") or "pl",
        is_primary_source=bool(d.get("is_primary_source")),
        is_user_provided=bool(d.get("is_user_provided")),
        is_tool_result=bool(d.get("is_tool_result")),
        is_inference=bool(d.get("is_inference")),
        expires_at=d.get("expires_at"),
        metadata=dict(_jl(d.get("metadata_json"), {})),
        created_at=float(d.get("created_at") or 0),
    )


def upsert_claim(claim: KnowledgeClaim) -> tuple[bool, str, KnowledgeClaim]:
    ensure_ready()
    if not claim.content_hash:
        claim.content_hash = content_hash(
            claim.user_id, claim.subject_entity_id, claim.predicate, claim.literal_value, claim.statement
        )
    existing = fetch_one(
        "SELECT * FROM wk_claims WHERE content_hash=? AND user_id=?",
        (claim.content_hash, claim.user_id or ""),
    )
    ts = _now()
    if existing:
        d = dict(existing)
        conf = min(0.95, float(d.get("confidence") or 0.4) + 0.05)
        ver = int(d.get("version") or 1) + 1
        exec_one(
            """
            UPDATE wk_claims SET confidence=?, supporting_evidence_count=?,
                source_diversity_count=?, updated_at=?, version=?,
                evidence_ids_json=?, last_verified_at=?
            WHERE claim_id=?
            """,
            (
                conf,
                int(d.get("supporting_evidence_count") or 0) + max(1, claim.supporting_evidence_count),
                max(int(d.get("source_diversity_count") or 0), claim.source_diversity_count),
                ts,
                ver,
                _j(list(dict.fromkeys(list(_jl(d.get("evidence_ids_json"), [])) + claim.evidence_ids))[:20]),
                ts,
                d["claim_id"],
            ),
        )
        claim.claim_id = d["claim_id"]
        claim.confidence = conf
        claim.version = ver
        return False, "reinforced", claim
    claim.claim_id = claim.claim_id or str(uuid.uuid4())
    claim.created_at = claim.created_at or ts
    claim.updated_at = ts
    claim.observed_at = claim.observed_at or ts
    exec_one(
        """
        INSERT INTO wk_claims(
            claim_id, subject_entity_id, predicate, object_entity_id, literal_value,
            value_type, claim_type, scope, user_id, session_id, task_id, confidence,
            status, valid_from, valid_until, observed_at, last_verified_at,
            verification_due_at, evidence_ids_json, supporting_evidence_count,
            contradicting_evidence_count, source_diversity_count, content_hash,
            statement, created_at, updated_at, version
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            claim.claim_id,
            claim.subject_entity_id,
            claim.predicate[:120],
            claim.object_entity_id,
            claim.literal_value[:400],
            claim.value_type,
            claim.claim_type,
            claim.scope,
            claim.user_id or "",
            claim.session_id or "",
            claim.task_id or "",
            claim.confidence,
            claim.status,
            claim.valid_from,
            claim.valid_until,
            claim.observed_at,
            claim.last_verified_at,
            claim.verification_due_at,
            _j(claim.evidence_ids[:20]),
            claim.supporting_evidence_count,
            claim.contradicting_evidence_count,
            claim.source_diversity_count,
            claim.content_hash,
            claim.statement[:480],
            claim.created_at,
            claim.updated_at,
            claim.version,
        ),
    )
    return True, "created", claim


def get_claim(claim_id: str) -> KnowledgeClaim | None:
    ensure_ready()
    row = fetch_one("SELECT * FROM wk_claims WHERE claim_id=?", (claim_id,))
    return _row_claim(row) if row else None


def _row_claim(row: Any) -> KnowledgeClaim:
    d = dict(row)
    return KnowledgeClaim(
        claim_id=d["claim_id"],
        subject_entity_id=d.get("subject_entity_id") or "",
        predicate=d.get("predicate") or "",
        object_entity_id=d.get("object_entity_id") or "",
        literal_value=d.get("literal_value") or "",
        value_type=d.get("value_type") or "text",
        claim_type=d.get("claim_type") or "fact",
        scope=d.get("scope") or "user",
        user_id=d.get("user_id") or "",
        session_id=d.get("session_id") or "",
        task_id=d.get("task_id") or "",
        confidence=float(d.get("confidence") or 0.4),
        status=d.get("status") or "proposed",
        valid_from=d.get("valid_from"),
        valid_until=d.get("valid_until"),
        observed_at=float(d.get("observed_at") or 0),
        last_verified_at=d.get("last_verified_at"),
        verification_due_at=d.get("verification_due_at"),
        evidence_ids=list(_jl(d.get("evidence_ids_json"), [])),
        supporting_evidence_count=int(d.get("supporting_evidence_count") or 0),
        contradicting_evidence_count=int(d.get("contradicting_evidence_count") or 0),
        source_diversity_count=int(d.get("source_diversity_count") or 0),
        content_hash=d.get("content_hash") or "",
        statement=d.get("statement") or "",
        created_at=float(d.get("created_at") or 0),
        updated_at=float(d.get("updated_at") or 0),
        version=int(d.get("version") or 1),
    )


def list_active_claims(*, user_id: str, limit: int = 20) -> list[KnowledgeClaim]:
    ensure_ready()
    now = _now()
    rows = fetch_all(
        """
        SELECT * FROM wk_claims
        WHERE user_id=? AND status IN ('proposed','supported','verified','disputed')
          AND (valid_until IS NULL OR valid_until > ?)
        ORDER BY confidence DESC, updated_at DESC LIMIT ?
        """,
        (user_id, now, limit),
    )
    return [_row_claim(r) for r in rows]


def supersede_claim(*, old_id: str, new_id: str, reason: str = "supersession") -> None:
    ensure_ready()
    ts = _now()
    exec_one(
        "UPDATE wk_claims SET status='superseded', updated_at=?, valid_until=? WHERE claim_id=?",
        (ts, ts, old_id),
    )
    append_event("", "claim_supersede", {"old": old_id, "new": new_id, "reason": reason})


def link_claim_evidence(claim_id: str, evidence_id: str, link_type: str = "supports") -> None:
    ensure_ready()
    try:
        exec_one(
            """
            INSERT INTO wk_claim_evidence(id, claim_id, evidence_id, link_type, created_at)
            VALUES(?,?,?,?,?)
            """,
            (str(uuid.uuid4()), claim_id, evidence_id, link_type, _now()),
        )
    except Exception as link_exc:
        log.debug("claim-evidence link skipped: %s", link_exc)


def upsert_relation(rel: KnowledgeRelation, user_id: str = "") -> KnowledgeRelation:
    ensure_ready()
    ts = _now()
    rel.relation_id = rel.relation_id or str(uuid.uuid4())
    rel.created_at = rel.created_at or ts
    rel.updated_at = ts
    exec_one(
        """
        INSERT INTO wk_relations(
            relation_id, subject_entity_id, predicate, object_entity_id, claim_id,
            confidence, valid_from, valid_until, status, created_at, updated_at, user_id
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(user_id, subject_entity_id, predicate, object_entity_id) DO UPDATE SET
            confidence=excluded.confidence,
            claim_id=excluded.claim_id,
            updated_at=excluded.updated_at,
            status=excluded.status
        """,
        (
            rel.relation_id,
            rel.subject_entity_id,
            rel.predicate[:80],
            rel.object_entity_id,
            rel.claim_id,
            rel.confidence,
            rel.valid_from,
            rel.valid_until,
            rel.status,
            rel.created_at,
            rel.updated_at,
            user_id,
        ),
    )
    return rel


def list_relations_for_entities(
    *, user_id: str, entity_ids: list[str], limit: int = 40
) -> list[KnowledgeRelation]:
    ensure_ready()
    if not entity_ids:
        return []
    out: list[KnowledgeRelation] = []
    for eid in entity_ids[:12]:
        rows = fetch_all(
            """
            SELECT * FROM wk_relations
            WHERE user_id=? AND status='active'
              AND (subject_entity_id=? OR object_entity_id=?)
            ORDER BY confidence DESC LIMIT ?
            """,
            (user_id, eid, eid, max(2, limit // max(1, len(entity_ids[:12])))),
        )
        for r in rows:
            d = dict(r)
            out.append(
                KnowledgeRelation(
                    relation_id=d["relation_id"],
                    subject_entity_id=d["subject_entity_id"],
                    predicate=d["predicate"],
                    object_entity_id=d["object_entity_id"],
                    claim_id=d.get("claim_id") or "",
                    confidence=float(d.get("confidence") or 0.5),
                    valid_from=d.get("valid_from"),
                    valid_until=d.get("valid_until"),
                    status=d.get("status") or "active",
                    created_at=float(d.get("created_at") or 0),
                    updated_at=float(d.get("updated_at") or 0),
                )
            )
    return out[:limit]


def upsert_conflict(c: ClaimConflict, user_id: str = "") -> ClaimConflict:
    ensure_ready()
    ts = _now()
    c.conflict_id = c.conflict_id or str(uuid.uuid4())
    c.created_at = c.created_at or ts
    exec_one(
        """
        INSERT INTO wk_conflicts(
            conflict_id, claim_a_id, claim_b_id, conflict_type, severity, confidence,
            resolution_status, preferred_claim_id, resolution_reason, user_id,
            created_at, resolved_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            c.conflict_id,
            c.claim_a_id,
            c.claim_b_id,
            c.conflict_type,
            c.severity,
            c.confidence,
            c.resolution_status,
            c.preferred_claim_id,
            c.resolution_reason[:240],
            user_id,
            c.created_at,
            c.resolved_at,
        ),
    )
    return c


def list_open_conflicts(*, user_id: str, limit: int = 10) -> list[ClaimConflict]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM wk_conflicts
        WHERE user_id=? AND resolution_status='open'
        ORDER BY severity DESC, created_at DESC LIMIT ?
        """,
        (user_id, limit),
    )
    out: list[ClaimConflict] = []
    for r in rows:
        d = dict(r)
        out.append(
            ClaimConflict(
                conflict_id=d["conflict_id"],
                claim_a_id=d["claim_a_id"],
                claim_b_id=d["claim_b_id"],
                conflict_type=d.get("conflict_type") or "source_disagreement",
                severity=float(d.get("severity") or 0.5),
                confidence=float(d.get("confidence") or 0.5),
                resolution_status=d.get("resolution_status") or "open",
                preferred_claim_id=d.get("preferred_claim_id") or "",
                resolution_reason=d.get("resolution_reason") or "",
                created_at=float(d.get("created_at") or 0),
                resolved_at=d.get("resolved_at"),
            )
        )
    return out


def save_execution_graph(graph: ExecutionGraph) -> None:
    ensure_ready()
    ts = _now()
    graph.updated_at = ts
    graph.created_at = graph.created_at or ts
    exec_one(
        """
        INSERT INTO wk_execution_graphs(
            execution_id, task_id, goal_id, user_id, session_id, turn_id, status,
            nodes_json, edges_json, current_node, completed_nodes_json,
            failed_nodes_json, blocked_nodes_json, replan_count,
            lease_owner, lease_until, created_at, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(execution_id) DO UPDATE SET
            status=excluded.status,
            nodes_json=excluded.nodes_json,
            edges_json=excluded.edges_json,
            current_node=excluded.current_node,
            completed_nodes_json=excluded.completed_nodes_json,
            failed_nodes_json=excluded.failed_nodes_json,
            blocked_nodes_json=excluded.blocked_nodes_json,
            replan_count=excluded.replan_count,
            lease_owner=excluded.lease_owner,
            lease_until=excluded.lease_until,
            updated_at=excluded.updated_at
        """,
        (
            graph.execution_id,
            graph.task_id,
            graph.goal_id,
            graph.user_id,
            graph.session_id,
            graph.turn_id,
            graph.status,
            _j([n.model_dump() for n in graph.nodes]),
            _j(graph.edges),
            graph.current_node,
            _j(graph.completed_nodes),
            _j(graph.failed_nodes),
            _j(graph.blocked_nodes),
            graph.replan_count,
            graph.lease_owner,
            graph.lease_until,
            graph.created_at,
            graph.updated_at,
        ),
    )


def get_execution_graph(execution_id: str) -> ExecutionGraph | None:
    ensure_ready()
    row = fetch_one("SELECT * FROM wk_execution_graphs WHERE execution_id=?", (execution_id,))
    if not row:
        return None
    return _row_exec(row)


def list_active_executions(*, user_id: str, limit: int = 5) -> list[ExecutionGraph]:
    ensure_ready()
    rows = fetch_all(
        """
        SELECT * FROM wk_execution_graphs
        WHERE user_id=? AND status IN ('pending','running','blocked','waiting_user')
        ORDER BY updated_at DESC LIMIT ?
        """,
        (user_id, limit),
    )
    return [_row_exec(r) for r in rows]


def _row_exec(row: Any) -> ExecutionGraph:
    d = dict(row)
    nodes_raw = list(_jl(d.get("nodes_json"), []))
    nodes = [ExecutionNode.model_validate(n) for n in nodes_raw if isinstance(n, dict)]
    return ExecutionGraph(
        execution_id=d["execution_id"],
        task_id=d.get("task_id") or "",
        goal_id=d.get("goal_id") or "",
        user_id=d.get("user_id") or "",
        session_id=d.get("session_id") or "",
        turn_id=d.get("turn_id") or "",
        status=d.get("status") or "pending",
        nodes=nodes,
        edges=list(_jl(d.get("edges_json"), [])),
        current_node=d.get("current_node") or "",
        completed_nodes=list(_jl(d.get("completed_nodes_json"), [])),
        failed_nodes=list(_jl(d.get("failed_nodes_json"), [])),
        blocked_nodes=list(_jl(d.get("blocked_nodes_json"), [])),
        replan_count=int(d.get("replan_count") or 0),
        lease_owner=d.get("lease_owner") or "",
        lease_until=d.get("lease_until"),
        created_at=float(d.get("created_at") or 0),
        updated_at=float(d.get("updated_at") or 0),
    )


def record_effect(
    *,
    execution_id: str,
    node_id: str,
    idempotency_key: str,
    summary: str = "",
    evidence_id: str = "",
) -> bool:
    """Return False if effect already recorded (skip re-exec)."""
    ensure_ready()
    existing = fetch_one(
        "SELECT effect_id FROM wk_execution_effects WHERE idempotency_key=?",
        (idempotency_key,),
    )
    if existing:
        return False
    exec_one(
        """
        INSERT INTO wk_execution_effects(
            effect_id, execution_id, node_id, idempotency_key, status, summary, evidence_id, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (str(uuid.uuid4()), execution_id, node_id, idempotency_key, "completed", summary[:400], evidence_id, _now()),
    )
    return True


def effect_already_done(idempotency_key: str) -> bool:
    ensure_ready()
    return (
        fetch_one(
            "SELECT 1 FROM wk_execution_effects WHERE idempotency_key=?",
            (idempotency_key,),
        )
        is not None
    )


def record_validation(
    *,
    execution_id: str,
    node_id: str,
    rule: str,
    succeeded: bool,
    evidence_id: str = "",
    detail: str = "",
) -> None:
    ensure_ready()
    exec_one(
        """
        INSERT INTO wk_execution_validations(
            validation_id, execution_id, node_id, rule, succeeded, evidence_id, detail, created_at
        ) VALUES (?,?,?,?,?,?,?,?)
        """,
        (
            str(uuid.uuid4()),
            execution_id,
            node_id,
            rule[:120],
            int(succeeded),
            evidence_id,
            detail[:400],
            _now(),
        ),
    )


def search_entities(*, user_id: str, query: str, limit: int = 8) -> list[KnowledgeEntity]:
    ensure_ready()
    q = normalize_name(query)
    if not q:
        return []
    rows = fetch_all(
        """
        SELECT * FROM wk_entities
        WHERE user_id=? AND (merged_into_entity_id IS NULL OR merged_into_entity_id='')
          AND (name_norm LIKE ? OR description LIKE ?)
        ORDER BY confidence DESC LIMIT ?
        """,
        (user_id, f"%{q}%", f"%{query[:80]}%", limit),
    )
    return [_row_entity(r) for r in rows]
