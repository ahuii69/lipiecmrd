"""World knowledge: Evidence + Claims + KG + Execution Graph tests."""

from __future__ import annotations

import time
import uuid

import pytest

from aihub.world_knowledge import (
    apply_action_claim_guard,
    apply_knowledge_influences_to_decision,
    process_turn_knowledge,
)
from aihub.world_knowledge import store
from aihub.world_knowledge.engine import (
    classify_claim_type,
    resolve_or_create_entity,
    retrieve_knowledge_context,
    score_evidence,
    source_diversity_score,
)
from aihub.world_knowledge.execution import (
    build_execution_graph_from_plan,
    fail_node_and_replan,
    mark_node_completed,
    resume_execution,
    should_retry,
)
from aihub.world_knowledge.models import KnowledgeClaim


@pytest.fixture()
def uid(isolated_db):
    return f"wk_user_{uuid.uuid4().hex[:8]}"


def test_entity_scope_and_alias(uid):
    e = resolve_or_create_entity(
        user_id=uid, name="vps-47af7d9d", entity_type="server", aliases=["mój VPS", "ovh-vps"]
    )
    assert e.user_id == uid
    assert e.scope == "user"
    found = store.find_entity_by_alias(user_id=uid, name="mój VPS")
    assert found is not None
    assert found.entity_id == e.entity_id


def test_no_false_merge(uid):
    a = resolve_or_create_entity(user_id=uid, name="projekt-alpha", entity_type="project")
    b = resolve_or_create_entity(user_id=uid, name="projekt-beta", entity_type="project")
    assert a.entity_id != b.entity_id


def test_merge_preserves_audit(uid):
    a = resolve_or_create_entity(user_id=uid, name="serwer-1", entity_type="server")
    b = resolve_or_create_entity(user_id=uid, name="serwer-1-alias", entity_type="server")
    ok = store.merge_entities(keep_id=a.entity_id, remove_id=b.entity_id, user_id=uid, reason="test")
    assert ok
    rem = store.get_entity(b.entity_id)
    assert rem is not None
    assert rem.merged_into_entity_id == a.entity_id


def test_claim_has_evidence_and_types(uid):
    r = process_turn_knowledge(
        turn_id="t1",
        user_id=uid,
        session_id="s1",
        message="repo mrd stoi na vps-47af7d9d",
        response_text="ok",
        trace={},
    )
    assert r.writeback_succeeded
    assert r.claims_upserted >= 1
    assert r.evidence_upserted >= 1
    claims = store.list_active_claims(user_id=uid, limit=10)
    assert claims
    assert claims[0].evidence_ids
    assert claims[0].claim_type in ("fact", "user_statement")


def test_opinion_not_fact(uid):
    assert classify_claim_type("myślę, że to najlepsze rozwiązanie") == "opinion"


def test_inference_not_observation():
    assert classify_claim_type("więc wynik wynosi 3", is_inference=True) == "inference"


def test_user_statement_scope(uid):
    r = process_turn_knowledge(
        turn_id="t2",
        user_id=uid,
        session_id="s",
        message="preferuję krótkie odpowiedzi",
        response_text="ok",
    )
    claims = store.list_active_claims(user_id=uid)
    # opinion/preference statements may or may not create claims; if created scope=user
    for c in claims:
        assert c.scope == "user"
        assert c.user_id == uid


def test_primary_source_higher_score():
    a = score_evidence(
        source_type="web_page",
        is_primary=True,
        is_user=False,
        freshness=0.7,
        relevance=0.7,
        corroboration=0.2,
    )
    b = score_evidence(
        source_type="web_page",
        is_primary=False,
        is_user=False,
        freshness=0.7,
        relevance=0.7,
        corroboration=0.2,
    )
    assert a["reliability_score"] > b["reliability_score"]


def test_freshness_affects_score():
    fresh = score_evidence(
        source_type="research_result",
        is_primary=False,
        is_user=False,
        freshness=0.95,
        relevance=0.7,
        corroboration=0.2,
    )
    stale = score_evidence(
        source_type="research_result",
        is_primary=False,
        is_user=False,
        freshness=0.1,
        relevance=0.7,
        corroboration=0.2,
    )
    assert fresh["overall_score"] > stale["overall_score"]


def test_copied_sources_low_diversity():
    # same registrable domain family
    d = source_diversity_score(["www.example.com", "example.com", "m.example.com"])
    assert d <= 0.25


def test_temporal_supersession(uid):
    process_turn_knowledge(
        turn_id="tv1",
        user_id=uid,
        session_id="s",
        message="usługa redis wersja 6.2.1",
        response_text="ok",
    )
    # force same subject different value via second write with identical predicate pattern
    e = resolve_or_create_entity(user_id=uid, name="usługa-redis", entity_type="service")
    old = KnowledgeClaim(
        claim_id=str(uuid.uuid4()),
        subject_entity_id=e.entity_id,
        predicate="version",
        literal_value="6.2.1",
        claim_type="fact",
        user_id=uid,
        statement="usługa-redis version 6.2.1",
        confidence=0.6,
        status="supported",
        observed_at=time.time(),
    )
    store.upsert_claim(old)
    new = KnowledgeClaim(
        claim_id=str(uuid.uuid4()),
        subject_entity_id=e.entity_id,
        predicate="version",
        literal_value="7.0.0",
        claim_type="fact",
        user_id=uid,
        statement="usługa-redis version 7.0.0",
        confidence=0.7,
        status="supported",
        observed_at=time.time(),
    )
    from aihub.world_knowledge.engine import detect_claim_conflicts

    conflicts = detect_claim_conflicts(user_id=uid, new_claim=new, existing=[old])
    assert conflicts
    assert conflicts[0].conflict_type == "changed_over_time"
    loaded = store.get_claim(old.claim_id)
    assert loaded is not None
    assert loaded.status == "superseded"


def test_stale_forces_verification(uid):
    process_turn_knowledge(
        turn_id="ts1",
        user_id=uid,
        session_id="s",
        message="repo aihub stoi na serwer prod-1",
        response_text="ok",
    )
    claims = store.list_active_claims(user_id=uid)
    assert claims
    # expire verification
    from aihub.db import exec_one

    exec_one(
        "UPDATE wk_claims SET verification_due_at=? WHERE claim_id=?",
        (time.time() - 10, claims[0].claim_id),
    )
    ctx = retrieve_knowledge_context(user_id=uid, message="na którym serwerze stoi aihub")
    assert ctx.verification_required or ctx.stale_claims
    dc = {"selected_strategy": "instant", "reason_codes": [], "web_decision": "off", "strategy_confidence": 0.8}
    apply_knowledge_influences_to_decision(decision_core=dc, user_id=uid, message="gdzie stoi aihub")
    assert dc.get("web_decision") in ("optional", "required") or dc.get("verification_required")


def test_kg_influences_strategy_and_planner(uid):
    process_turn_knowledge(
        turn_id="ti1",
        user_id=uid,
        session_id="s",
        message="projekt cockpit stoi na vps-47af7d9d",
        response_text="ok",
    )
    dc = {
        "selected_strategy": "instant",
        "reason_codes": [],
        "web_decision": "off",
        "strategy_confidence": 0.9,
        "planner_recommended": True,
        "session_id": "s",
    }
    apply_knowledge_influences_to_decision(
        decision_core=dc, user_id=uid, message="na którym serwerze stoi cockpit", intent="task"
    )
    assert dc.get("knowledge_context_loaded") is True
    assert dc.get("knowledge_entities_count", 0) >= 1 or dc.get("knowledge_claims_count", 0) >= 1
    assert "WK_PLANNER_HINTS" in dc.get("reason_codes", []) or dc.get("graph_influenced_planner")


def test_user_isolation(uid):
    other = f"other_{uuid.uuid4().hex[:6]}"
    process_turn_knowledge(
        turn_id="iso1",
        user_id=uid,
        session_id="s",
        message="sekretny-serwer-xyz stoi na 10.0.0.1",
        response_text="ok",
    )
    ctx = retrieve_knowledge_context(user_id=other, message="sekretny-serwer-xyz")
    assert all("sekretny" not in (e.canonical_name or "").lower() for e in ctx.entities)


def test_action_claim_guard_blocks_and_allows():
    blocked, meta = apply_action_claim_guard(response_text="Usługa została uruchomiona i naprawiona.")
    assert meta["action_claim_guard_applied"]
    assert meta["action_claim_blocked"]
    assert "niepotwierdzone" in blocked.lower() or "nie mogę uczciwie" in blocked.lower()

    class _T:
        ok = True

    ok_text, meta2 = apply_action_claim_guard(
        response_text="Usługa została uruchomiona.",
        tool_results=[_T()],
        validation_succeeded=True,
    )
    assert meta2["action_claim_verified"]
    assert "uruchomiona" in ok_text.lower()


def test_execution_graph_idempotency_and_resume(uid):
    g = build_execution_graph_from_plan(
        user_id=uid,
        session_id="s",
        turn_id="tex1",
        task_id="task1",
        steps=[
            {
                "step_id": "n1",
                "action": "restart_service",
                "tool": "system.restart",
                "validation": ["is-active"],
                "idempotency_key": f"{uid}:n1:restart",
            },
            {
                "step_id": "n2",
                "action": "health_check",
                "tool": "system.health",
                "dependencies": ["n1"],
                "idempotency_key": f"{uid}:n2:health",
            },
        ],
    )
    assert g.execution_id
    g2 = mark_node_completed(g, "n1", summary="restart ok", validation_ok=True, evidence_ids=["ev1"])
    assert "n1" in g2.completed_nodes
    # second complete should not duplicate effect
    assert store.effect_already_done(f"{uid}:n1:restart")
    g3 = fail_node_and_replan(g2, "n2", error="timeout", error_class="transient", alternative_tool="system.health2")
    assert g3.replan_count >= 1
    resumed = resume_execution(g.execution_id, owner="test")
    assert resumed is not None
    assert "n1" in resumed.completed_nodes


def test_retry_policy_classes():
    assert should_retry("transient", 1) is True
    assert should_retry("auth", 0) is False
    assert should_retry("permanent", 0) is False
    assert should_retry("validation", 1) is False


def test_replay_does_not_write_knowledge(uid):
    process_turn_knowledge(
        turn_id="tr0",
        user_id=uid,
        session_id="s",
        message="app X deployed_on host-y",
        response_text="ok",
    )
    before = len(store.list_active_claims(user_id=uid))
    r = process_turn_knowledge(
        turn_id="tr1",
        user_id=uid,
        session_id="s",
        message="app X deployed_on host-z",
        response_text="ok",
        replay_mode=True,
    )
    assert r.writeback_succeeded is False
    after = len(store.list_active_claims(user_id=uid))
    assert after == before


def test_validation_failure_not_success(uid):
    g = build_execution_graph_from_plan(
        user_id=uid,
        session_id="s",
        turn_id="tv",
        steps=[
            {
                "step_id": "restart",
                "action": "restart",
                "validation": ["health"],
                "idempotency_key": f"{uid}:restart_val",
            }
        ],
    )
    g2 = mark_node_completed(g, "restart", summary="exit0", validation_ok=False)
    assert "restart" in g2.failed_nodes or any(n.status == "failed" for n in g2.nodes)
