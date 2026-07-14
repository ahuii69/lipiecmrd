"""World knowledge engine: extract, resolve, score, retrieve, writeback, influence."""

from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Any
from urllib.parse import urlparse

from aihub.world_knowledge import store
from aihub.world_knowledge.execution import build_execution_graph_from_plan
from aihub.world_knowledge.models import (
    ClaimConflict,
    EvidenceRecord,
    KnowledgeClaim,
    KnowledgeContextPack,
    KnowledgeEntity,
    KnowledgeRelation,
    KnowledgeTurnResult,
)

log = logging.getLogger(__name__)

_MAX_ENTITIES = 8
_MAX_CLAIMS = 12
_MAX_EVIDENCE = 10
_MAX_HOPS = 2

_DEPLOY_RE = re.compile(
    r"(?iu)\b(repo|repozytorium|serwer|server|vps|host|deploy|stoi na|uruchomion\w*|na\s+(\S+)\s+stoi)\b"
)
_VERSION_RE = re.compile(r"(?iu)\b(wersja|version|v?\d+\.\d+(?:\.\d+)?)\b")
_OPINION_RE = re.compile(r"(?iu)\b(myślę|wydaje mi się|imo|imość|preferuję|lubię|nie lubię)\b")
_FACT_BIND_RE = re.compile(
    r"(?iu)(?:^|\b)([\w./:-]{2,80})\s+(?:jest|to|stoi na|=|:)\s+([\w./:-]{2,120})"
)
_CORRECTION_RE = re.compile(r"(?iu)\b(nie\s+o\s+to|poprawka|korekta|właściwie|zmienił[oa]m?)\b")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def score_evidence(
    *,
    source_type: str,
    is_primary: bool,
    is_user: bool,
    freshness: float,
    relevance: float,
    corroboration: float,
    domain_fit: float = 0.5,
) -> dict[str, float]:
    reliability = {
        "official_document": 0.9,
        "system_observation": 0.85,
        "tool_result": 0.8,
        "database": 0.8,
        "web_page": 0.55,
        "research_result": 0.6,
        "user_statement": 0.7,
        "conversation": 0.55,
        "memory": 0.6,
        "file": 0.65,
        "model_inference": 0.35,
    }.get(source_type, 0.5)
    if is_primary:
        reliability = _clamp(reliability + 0.12)
    if is_user and source_type in ("user_statement", "conversation"):
        reliability = _clamp(reliability)  # local truth, not global
    overall = _clamp(
        0.3 * reliability
        + 0.25 * freshness
        + 0.2 * relevance
        + 0.15 * corroboration
        + 0.1 * domain_fit
    )
    return {
        "reliability_score": reliability,
        "freshness_score": freshness,
        "relevance_score": relevance,
        "corroboration_score": corroboration,
        "overall_score": overall,
    }


def source_diversity_score(domains: list[str]) -> float:
    """Copied domains do not inflate diversity."""
    cleaned = []
    for d in domains:
        host = (d or "").lower().strip()
        if not host:
            continue
        # strip www / common mirrors as same family
        host = host.removeprefix("www.")
        base = host.split(".")[-2:] if "." in host else [host]
        key = ".".join(base)
        if key not in cleaned:
            cleaned.append(key)
    n = len(cleaned)
    if n <= 1:
        return 0.15 if n == 1 else 0.0
    return _clamp(0.2 * n)


def classify_claim_type(message: str, *, is_inference: bool = False) -> str:
    if is_inference:
        return "inference"
    if _OPINION_RE.search(message or ""):
        return "opinion"
    if _CORRECTION_RE.search(message or ""):
        return "user_statement"
    if re.search(r"(?iu)\b(odrzucam|nie chcę|bez)\b", message or ""):
        return "rejection"
    if re.search(r"(?iu)\b(zdecydujmy|wybieram|akceptuję)\b", message or ""):
        return "decision"
    return "fact" if _FACT_BIND_RE.search(message or "") or _DEPLOY_RE.search(message or "") else "user_statement"


def resolve_or_create_entity(
    *,
    user_id: str,
    name: str,
    entity_type: str = "concept",
    aliases: list[str] | None = None,
    description: str = "",
    merge_threshold: float = 0.92,
) -> KnowledgeEntity:
    found = store.find_entity_by_alias(user_id=user_id, name=name)
    if found:
        return found
    # fuzzy collision guard: only merge when normalize equal
    norm = store.normalize_name(name)
    candidates = store.search_entities(user_id=user_id, query=name, limit=5)
    for c in candidates:
        if store.normalize_name(c.canonical_name) == norm:
            return c
        # refuse fuzzy merge without strong proof
        if store.normalize_name(c.canonical_name) and norm and abs(len(c.canonical_name) - len(name)) <= 1:
            # still require high similarity via exact alias later
            if store.normalize_name(c.canonical_name)[:6] == norm[:6] and len(norm) < 4:
                continue
    ent = KnowledgeEntity(
        entity_id=str(uuid.uuid4()),
        canonical_name=name[:160],
        entity_type=entity_type,
        aliases=list(aliases or [])[:10],
        description=description[:400],
        scope="user",
        user_id=user_id,
        confidence=0.55,
        created_at=time.time(),
        updated_at=time.time(),
    )
    return store.upsert_entity(ent)


def extract_candidates_from_message(
    *,
    message: str,
    user_id: str,
    session_id: str,
    turn_id: str,
    trace: dict[str, Any] | None = None,
) -> tuple[list[KnowledgeEntity], list[KnowledgeClaim], list[EvidenceRecord], list[KnowledgeRelation]]:
    text = (message or "").strip()
    entities: list[KnowledgeEntity] = []
    claims: list[KnowledgeClaim] = []
    evidence: list[EvidenceRecord] = []
    relations: list[KnowledgeRelation] = []
    if len(text) < 4:
        return entities, claims, evidence, relations

    claim_type = classify_claim_type(text)
    scores = score_evidence(
        source_type="user_statement",
        is_primary=True,
        is_user=True,
        freshness=1.0,
        relevance=0.8,
        corroboration=0.0,
    )
    ev = EvidenceRecord(
        evidence_id=str(uuid.uuid4()),
        turn_id=turn_id,
        user_id=user_id,
        session_id=session_id,
        source_type="user_statement",
        excerpt=text[:400],
        is_user_provided=True,
        is_primary_source=True,
        is_inference=claim_type == "inference",
        retrieved_at=time.time(),
        created_at=time.time(),
        **scores,
    )
    evidence.append(ev)

    # Entity patterns: repo/server bindings
    m = _FACT_BIND_RE.search(text)
    subject_name = ""
    object_name = ""
    predicate = "related_to"
    if m:
        subject_name, object_name = m.group(1), m.group(2)
        predicate = "is"
    elif _DEPLOY_RE.search(text):
        # "repo X stoi na serwerze Y" / "serwer vps-..."
        tokens = re.findall(r"[A-Za-z0-9][\w./-]{2,}", text)
        for t in tokens:
            low = t.lower()
            if any(x in low for x in ("vps", "server", "51.", "ovh")) or re.match(r"\d+\.\d+\.\d+\.\d+", t):
                object_name = object_name or t
            elif any(x in low for x in ("repo", "git", "mrd", "aihub")) or "/" in t:
                subject_name = subject_name or t
        if not subject_name and tokens:
            subject_name = tokens[0]
        if not object_name and len(tokens) > 1:
            object_name = tokens[1]
        predicate = "deployed_on" if object_name else "mentions"

    if subject_name:
        et = "repository" if "repo" in text.lower() or "/" in subject_name else "project"
        if re.search(r"\d+\.\d+\.\d+\.\d+|vps|serwer|server", subject_name, re.I):
            et = "server"
        subj = resolve_or_create_entity(user_id=user_id, name=subject_name, entity_type=et)
        entities.append(subj)
        obj = None
        if object_name:
            ot = "server" if predicate == "deployed_on" or re.search(r"vps|serwer|\d+\.\d+", object_name, re.I) else "concept"
            obj = resolve_or_create_entity(user_id=user_id, name=object_name, entity_type=ot)
            entities.append(obj)
            relations.append(
                KnowledgeRelation(
                    relation_id=str(uuid.uuid4()),
                    subject_entity_id=subj.entity_id,
                    predicate=predicate,
                    object_entity_id=obj.entity_id,
                    confidence=0.6 if claim_type == "fact" else 0.45,
                    created_at=time.time(),
                    updated_at=time.time(),
                )
            )
        stmt = text[:240]
        if obj:
            stmt = f"{subj.canonical_name} {predicate} {obj.canonical_name}"
        # Never mark opinion/inference as verified fact
        status = "proposed"
        conf = 0.55 if claim_type == "fact" else 0.4
        if claim_type in ("opinion", "hypothesis", "assumption"):
            conf = 0.35
        due = time.time() + (86400 * 7 if _VERSION_RE.search(text) or predicate == "deployed_on" else 86400 * 30)
        claim = KnowledgeClaim(
            claim_id=str(uuid.uuid4()),
            subject_entity_id=subj.entity_id,
            predicate=predicate,
            object_entity_id=obj.entity_id if obj else "",
            literal_value=object_name or text[:160],
            claim_type=claim_type,  # type: ignore[arg-type]
            scope="user",
            user_id=user_id,
            session_id=session_id,
            confidence=conf,
            status=status,  # type: ignore[arg-type]
            evidence_ids=[ev.evidence_id],
            supporting_evidence_count=1,
            source_diversity_count=1,
            statement=stmt,
            verification_due_at=due,
            observed_at=time.time(),
        )
        claims.append(claim)

    # Research evidence from trace
    tr = trace or {}
    if tr.get("controlled_web_triggered") and tr.get("controlled_web_ok"):
        query = str(tr.get("controlled_web_query") or "")[:200]
        domains: list[str] = []
        sources = tr.get("controlled_web_sources") or tr.get("research_sources") or []
        if isinstance(sources, list):
            for s in sources[:6]:
                if isinstance(s, dict):
                    uri = str(s.get("url") or s.get("uri") or "")
                    domain = str(s.get("domain") or "")
                    if not domain and uri:
                        try:
                            domain = urlparse(uri).netloc
                        except Exception:
                            domain = ""
                    domains.append(domain)
                    sc = score_evidence(
                        source_type="research_result",
                        is_primary=bool(s.get("primary")),
                        is_user=False,
                        freshness=0.7,
                        relevance=0.7,
                        corroboration=0.0,
                    )
                    evidence.append(
                        EvidenceRecord(
                            evidence_id=str(uuid.uuid4()),
                            turn_id=turn_id,
                            user_id=user_id,
                            session_id=session_id,
                            source_type="research_result",
                            source_uri=uri[:400],
                            source_title=str(s.get("title") or "")[:200],
                            source_domain=domain[:120],
                            excerpt=str(s.get("snippet") or s.get("excerpt") or query)[:400],
                            retrieved_at=time.time(),
                            created_at=time.time(),
                            is_primary_source=bool(s.get("primary")),
                            **sc,
                        )
                    )
        # single research evidence fallback
        if not any(e.source_type == "research_result" for e in evidence):
            sc = score_evidence(
                source_type="research_result",
                is_primary=False,
                is_user=False,
                freshness=0.65,
                relevance=0.65,
                corroboration=0.0,
            )
            evidence.append(
                EvidenceRecord(
                    evidence_id=str(uuid.uuid4()),
                    turn_id=turn_id,
                    user_id=user_id,
                    session_id=session_id,
                    source_type="research_result",
                    excerpt=f"research:{query}"[:400],
                    retrieved_at=time.time(),
                    created_at=time.time(),
                    **sc,
                )
            )
        div = source_diversity_score(domains)
        for e in evidence:
            if e.source_type == "research_result":
                e.corroboration_score = div
                e.overall_score = _clamp(e.overall_score * 0.85 + div * 0.15)

    return entities[:_MAX_ENTITIES], claims[:_MAX_CLAIMS], evidence[:_MAX_EVIDENCE], relations[:12]


def detect_claim_conflicts(
    *, user_id: str, new_claim: KnowledgeClaim, existing: list[KnowledgeClaim]
) -> list[ClaimConflict]:
    out: list[ClaimConflict] = []
    for old in existing:
        if old.subject_entity_id != new_claim.subject_entity_id:
            continue
        if old.predicate != new_claim.predicate:
            continue
        if not old.literal_value or not new_claim.literal_value:
            continue
        if store.normalize_name(old.literal_value) == store.normalize_name(new_claim.literal_value):
            continue
        # temporal change vs negation
        if old.predicate in ("deployed_on", "version", "is", "uses"):
            ctype = "changed_over_time"
            # supersede old
            store.supersede_claim(old_id=old.claim_id, new_id=new_claim.claim_id, reason="temporal_update")
            conflict = ClaimConflict(
                conflict_id=str(uuid.uuid4()),
                claim_a_id=old.claim_id,
                claim_b_id=new_claim.claim_id,
                conflict_type=ctype,  # type: ignore[arg-type]
                severity=0.4,
                confidence=0.7,
                resolution_status="resolved",
                preferred_claim_id=new_claim.claim_id,
                resolution_reason="temporal_supersession",
                created_at=time.time(),
                resolved_at=time.time(),
            )
        else:
            conflict = ClaimConflict(
                conflict_id=str(uuid.uuid4()),
                claim_a_id=old.claim_id,
                claim_b_id=new_claim.claim_id,
                conflict_type="direct_negation",  # type: ignore[arg-type]
                severity=0.7,
                confidence=0.6,
                resolution_status="open",
                created_at=time.time(),
            )
            from aihub.db import exec_one

            exec_one(
                "UPDATE wk_claims SET status='disputed', contradicting_evidence_count=contradicting_evidence_count+1, updated_at=? WHERE claim_id=?",
                (time.time(), old.claim_id),
            )
        store.upsert_conflict(conflict, user_id=user_id)
        out.append(conflict)
    return out


def retrieve_knowledge_context(
    *,
    user_id: str,
    message: str,
    session_id: str = "",
    max_hops: int = _MAX_HOPS,
) -> KnowledgeContextPack:
    codes: list[str] = []
    pack = KnowledgeContextPack()
    try:
        store.ensure_ready()
        seeds = store.search_entities(user_id=user_id, query=message, limit=6)
        # also alias tokens
        for tok in re.findall(r"[A-Za-z0-9][\w./-]{2,}", message or "")[:8]:
            hit = store.find_entity_by_alias(user_id=user_id, name=tok)
            if hit and all(e.entity_id != hit.entity_id for e in seeds):
                seeds.append(hit)
        seeds = seeds[:_MAX_ENTITIES]
        pack.entities = seeds
        eids = [e.entity_id for e in seeds]
        rels = store.list_relations_for_entities(user_id=user_id, entity_ids=eids, limit=20)
        # 1 hop expansion
        hop_ids = set(eids)
        for r in rels:
            hop_ids.add(r.subject_entity_id)
            hop_ids.add(r.object_entity_id)
        if max_hops >= 2:
            more = store.list_relations_for_entities(
                user_id=user_id, entity_ids=list(hop_ids - set(eids))[:8], limit=16
            )
            rels = (rels + more)[:40]
        pack.relations = rels
        pack.hops = min(max_hops, 2 if rels else 0)
        claims = store.list_active_claims(user_id=user_id, limit=_MAX_CLAIMS)
        # filter relevance to seed entities when possible
        if eids:
            filtered = [c for c in claims if c.subject_entity_id in hop_ids or c.object_entity_id in hop_ids]
            pack.claims = filtered[:_MAX_CLAIMS] or claims[:6]
        else:
            pack.claims = claims[:6]
        now = time.time()
        for c in pack.claims:
            if c.verification_due_at and c.verification_due_at < now:
                pack.stale_claims.append(c.claim_id)
            if c.status == "disputed":
                pack.disputed_claims.append(c.claim_id)
            for eid in c.evidence_ids[:3]:
                ev = store.get_evidence(eid)
                if ev:
                    pack.evidence.append(ev)
        pack.conflicts = store.list_open_conflicts(user_id=user_id, limit=5)
        pack.verification_required = bool(pack.stale_claims) or bool(pack.conflicts)
        if pack.evidence:
            pack.evidence_quality = sum(e.overall_score for e in pack.evidence) / len(pack.evidence)
            pack.source_diversity = source_diversity_score([e.source_domain for e in pack.evidence])
        if pack.stale_claims:
            pack.evidence_gaps.append("stale_claims_need_reverify")
            codes.append("WK_STALE_CLAIMS")
        if pack.claims:
            codes.append("WK_CLAIMS_LOADED")
        if pack.entities:
            codes.append("WK_ENTITIES_LOADED")
        if pack.conflicts:
            codes.append("WK_CONFLICTS_OPEN")
        for c in pack.claims[:4]:
            pack.graph_path_hints.append(f"{c.subject_entity_id}:{c.predicate}:{c.literal_value[:40]}")
        pack.reason_codes = codes
    except Exception as exc:
        log.warning("retrieve_knowledge_context failed: %s", exc, exc_info=True)
        pack.degraded = True
        pack.reason_codes.append("WK_RETRIEVE_DEGRADED")
    return pack


def apply_knowledge_influences_to_decision(
    *,
    decision_core: dict[str, Any],
    user_id: str,
    message: str,
    intent: str = "",
) -> dict[str, Any]:
    codes = list(decision_core.get("reason_codes") or [])
    ctx = retrieve_knowledge_context(user_id=user_id, message=message, session_id=str(decision_core.get("session_id") or ""))
    decision_core["knowledge_context"] = {
        "entities": [e.canonical_name for e in ctx.entities[:6]],
        "claims": [
            {
                "id": c.claim_id,
                "type": c.claim_type,
                "statement": c.statement[:160],
                "confidence": c.confidence,
                "status": c.status,
            }
            for c in ctx.claims[:6]
        ],
        "relations": [f"{r.predicate}" for r in ctx.relations[:6]],
        "stale": list(ctx.stale_claims[:4]),
        "disputed": list(ctx.disputed_claims[:4]),
        "verification_required": ctx.verification_required,
        "evidence_quality": ctx.evidence_quality,
        "source_diversity": ctx.source_diversity,
        "path_hints": ctx.graph_path_hints[:4],
    }
    decision_core["knowledge_context_loaded"] = True
    decision_core["knowledge_entities_count"] = len(ctx.entities)
    decision_core["knowledge_claims_count"] = len(ctx.claims)
    decision_core["knowledge_relations_count"] = len(ctx.relations)
    decision_core["knowledge_conflicts_count"] = len(ctx.conflicts)
    decision_core["stale_claims_count"] = len(ctx.stale_claims)
    decision_core["verification_required"] = ctx.verification_required
    decision_core["evidence_quality_score"] = ctx.evidence_quality
    decision_core["source_diversity_score"] = ctx.source_diversity

    influenced_strategy = False
    if ctx.verification_required and str(decision_core.get("web_decision") or "off") == "off":
        decision_core["web_decision"] = "optional"
        codes.append("WK_STALE_FORCE_OPTIONAL_WEB")
        influenced_strategy = True
    if ctx.conflicts and str(decision_core.get("selected_strategy") or "") == "instant":
        decision_core["selected_strategy"] = "contextual"
        codes.append("WK_CONFLICT_FORCE_CONTEXTUAL")
        influenced_strategy = True
    if any(c.claim_type == "fact" and c.confidence >= 0.55 for c in ctx.claims):
        decision_core["knowledge_grounding_available"] = True
        codes.append("WK_GROUNDING_AVAILABLE")
    if ctx.disputed_claims:
        raw = float(decision_core.get("strategy_confidence") or 0.7)
        decision_core["strategy_confidence"] = round(_clamp(raw - 0.08), 3)
        codes.append("WK_DISPUTED_LOWERS_CONFIDENCE")

    # Planner influence: inject required entities/claims into plan hints
    if ctx.entities or ctx.relations:
        decision_core["planner_entity_hints"] = [e.entity_id for e in ctx.entities[:5]]
        decision_core["planner_relation_predicates"] = list(
            dict.fromkeys(r.predicate for r in ctx.relations[:8])
        )
        codes.append("WK_PLANNER_HINTS")
        decision_core["graph_influenced_planner"] = True

    # Build light execution graph when agentic/planner recommended
    if decision_core.get("planner_recommended") or str(decision_core.get("selected_strategy")) == "agentic":
        steps = []
        if ctx.verification_required:
            steps.append(
                {
                    "step_id": "verify_stale",
                    "action": "reverify_stale_claims",
                    "node_type": "research",
                    "preconditions": ["stale_claims_present"],
                    "expected_effects": ["claims_verified"],
                    "validation": ["claim_freshness_ok"],
                }
            )
        if ctx.entities:
            steps.append(
                {
                    "step_id": "use_kg",
                    "action": "apply_graph_context",
                    "node_type": "reason",
                    "required_entities": [e.entity_id for e in ctx.entities[:4]],
                    "dependencies": ["verify_stale"] if steps else [],
                }
            )
        if steps:
            g = build_execution_graph_from_plan(
                user_id=user_id,
                session_id=str(decision_core.get("session_id") or ""),
                turn_id=str(decision_core.get("turn_id") or ""),
                task_id=str(decision_core.get("long_horizon_task_id") or ""),
                steps=steps,
            )
            decision_core["execution_graph_id"] = g.execution_id
            codes.append("WK_EXECUTION_GRAPH_BUILT")

    decision_core["graph_influenced_strategy"] = influenced_strategy
    decision_core["knowledge_reason_codes"] = list(ctx.reason_codes) + [c for c in codes if c.startswith("WK_")]
    decision_core["reason_codes"] = codes
    # Enrich cognitive pack fields if present later
    decision_core["relevant_claims"] = decision_core["knowledge_context"]["claims"]
    decision_core["relevant_entities"] = decision_core["knowledge_context"]["entities"]
    decision_core["disputed_claims"] = list(ctx.disputed_claims)
    decision_core["stale_claims"] = list(ctx.stale_claims)
    return decision_core


def process_turn_knowledge(
    *,
    turn_id: str,
    user_id: str,
    session_id: str,
    message: str,
    response_text: str = "",
    trace: dict[str, Any] | None = None,
    decision_core: dict[str, Any] | None = None,
    replay_mode: bool = False,
) -> KnowledgeTurnResult:
    t0 = time.time()
    result = KnowledgeTurnResult()
    if not user_id or str(user_id).startswith("audit"):
        result.degraded = True
        result.reason_codes.append("WK_SKIPPED")
        return result
    try:
        store.ensure_ready()
        ents, claims, evidence, rels = extract_candidates_from_message(
            message=message,
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            trace=trace,
        )
        existing = store.list_active_claims(user_id=user_id, limit=30) if not replay_mode else []
        if not replay_mode:
            for e in evidence:
                ok, eid = store.upsert_evidence(e)
                if ok:
                    result.evidence_upserted += 1
                else:
                    e.evidence_id = eid
            for ent in ents:
                store.upsert_entity(ent)
                result.entities_upserted += 1
                # 19.07: world_knowledge is the sole knowledge SoT — no dual-write to legacy KG.
            for cl in claims:
                # attach evidence
                for e in evidence:
                    if e.evidence_id not in cl.evidence_ids:
                        cl.evidence_ids.append(e.evidence_id)
                conflicts = detect_claim_conflicts(user_id=user_id, new_claim=cl, existing=existing)
                result.conflicts_found += len(conflicts)
                result.claims_superseded += sum(
                    1 for c in conflicts if c.conflict_type == "changed_over_time"
                )
                created, why, cl = store.upsert_claim(cl)
                if created:
                    result.claims_upserted += 1
                for eid in cl.evidence_ids[:5]:
                    store.link_claim_evidence(cl.claim_id, eid)
                for e in evidence:
                    e.claim_ids = list(dict.fromkeys(e.claim_ids + [cl.claim_id]))
            for rel in rels:
                if claims:
                    rel.claim_id = claims[0].claim_id
                store.upsert_relation(rel, user_id=user_id)
                result.relations_upserted += 1
            result.writeback_succeeded = True
            result.reason_codes.append("WK_WRITEBACK_OK")
        else:
            result.reason_codes.append("WK_REPLAY_NO_WRITE")

        result.context = retrieve_knowledge_context(
            user_id=user_id, message=message, session_id=session_id
        )
        if decision_core and decision_core.get("execution_graph_id"):
            result.execution_id = str(decision_core.get("execution_graph_id"))
        result.reason_codes.append("WK_PIPELINE_OK")
    except Exception as exc:
        log.warning("process_turn_knowledge failed: %s", exc, exc_info=True)
        result.degraded = True
        result.reason_codes.append("WK_DEGRADED")
    result.timing_ms = (time.time() - t0) * 1000.0
    return result


def knowledge_trace_fields(result: KnowledgeTurnResult) -> dict[str, Any]:
    ctx = result.context
    out: dict[str, Any] = {
        "knowledge_writeback_attempted": True,
        "knowledge_writeback_succeeded": result.writeback_succeeded,
        "knowledge_learning_degraded": result.degraded,
        "knowledge_entities_count": result.entities_upserted
        if result.entities_upserted
        else (len(ctx.entities) if ctx else 0),
        "knowledge_claims_count": result.claims_upserted
        if result.claims_upserted
        else (len(ctx.claims) if ctx else 0),
        "knowledge_relations_count": result.relations_upserted
        if result.relations_upserted
        else (len(ctx.relations) if ctx else 0),
        "knowledge_conflicts_count": result.conflicts_found,
        "claim_superseded": result.claims_superseded > 0,
        "execution_graph_id": result.execution_id,
        "knowledge_reason_codes": list(result.reason_codes)[:24],
        "knowledge_timing_ms": result.timing_ms,
    }
    if ctx:
        out.update(
            {
                "knowledge_context_loaded": True,
                "knowledge_graph_hops": ctx.hops,
                "stale_claims_count": len(ctx.stale_claims),
                "verification_required": ctx.verification_required,
                "evidence_records_used": len(ctx.evidence),
                "evidence_quality_score": ctx.evidence_quality,
                "source_diversity_score": ctx.source_diversity,
            }
        )
    else:
        out["knowledge_context_loaded"] = False
    return out


def knowledge_prompt_block(decision_core: dict[str, Any]) -> str:
    kc = decision_core.get("knowledge_context") or {}
    if not kc:
        return ""
    lines = ["KNOWLEDGE GRAPH (wiążące fakty użytkownika — z provenance; nie zmyślaj):"]
    for c in (kc.get("claims") or [])[:5]:
        if isinstance(c, dict):
            lines.append(
                f"- [{c.get('type')}|{c.get('status')}|conf={float(c.get('confidence') or 0):.2f}] {c.get('statement')}"
            )
    if kc.get("stale"):
        lines.append(f"- STALE claims → re-verify: {', '.join(str(x)[:20] for x in kc.get('stale')[:3])}")
    if kc.get("disputed"):
        lines.append(f"- DISPUTED claims → express uncertainty: {len(kc.get('disputed') or [])}")
    ents = kc.get("entities") or []
    if ents:
        lines.append("- entities: " + ", ".join(str(e) for e in ents[:6]))
    if len(lines) == 1:
        return ""
    return "\n".join(lines)
