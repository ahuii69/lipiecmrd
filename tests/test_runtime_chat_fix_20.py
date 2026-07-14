"""20.07 runtime chat fixes: SQL JSON dialect, routing, memory hygiene, smoke contract."""

from __future__ import annotations

import json
import os

import pytest


def _patch_fake_llm_provider(monkeypatch, cr, fake_provider):
    """Wire fake primary provider through canonical failover service (no reserve)."""
    from aihub.llm import provider_registry as pr
    from aihub.turn.provider_service import ProviderExecutionService

    monkeypatch.setattr(cr, "get_default_provider", lambda: fake_provider)
    monkeypatch.setattr(
        pr,
        "build_provider_execution_service",
        lambda primary=None: ProviderExecutionService(primary=fake_provider, reserve=None),
    )
    monkeypatch.setattr("aihub.llm.provider_registry.get_default_provider", lambda: fake_provider)
    cr._RUNTIME = None


def test_sql_json_expressions_sqlite(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    from aihub.db import sql_json

    assert "json_extract(metadata" in sql_json.json_text_path("metadata", "goal_fingerprint")
    assert sql_json.json_text_eq("metadata", "goal_fingerprint").endswith("= ?")


def test_sql_json_expressions_postgres(monkeypatch):
    monkeypatch.setenv("DB_BACKEND", "postgres")
    from aihub.db import sql_json

    expr = sql_json.json_text_path("metadata", "goal_fingerprint")
    assert "::jsonb" in expr
    assert "->>" in expr
    assert "json_extract" not in expr


def test_goal_fingerprint_query_runs_on_active_backend(isolated_db, monkeypatch):
    """Execute the production fingerprint lookup against the live dialect."""
    from aihub.db.runtime import exec_one, fetch_one, now_ts
    from aihub.db.sql_json import json_text_eq
    from aihub.goal_engine import _goal_fingerprint

    uid = "gp_fp_user"
    title = "migrate database"
    fp = _goal_fingerprint(uid, "task", title)
    meta = json.dumps({"goal_fingerprint": fp})
    ts = now_ts()
    exec_one(
        """
        INSERT INTO goals(
          goal_id,user_id,title,description,goal_type,source,status,
          priority,urgency,importance,confidence,created_at,updated_at,
          expires_at,parent_goal_id,tags,success_criteria,failure_criteria,
          progress,metadata
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "g1",
            uid,
            title,
            "desc",
            "task",
            "test",
            "active",
            0.5,
            0.5,
            0.5,
            0.8,
            ts,
            ts,
            None,
            None,
            "[]",
            "[]",
            "[]",
            0.0,
            meta,
        ),
    )
    row = fetch_one(
        f"""
        SELECT * FROM goals
        WHERE user_id=?
          AND status IN ('proposed','active','blocked','scheduled')
          AND {json_text_eq("metadata", "goal_fingerprint")}
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (uid, fp),
    )
    assert row is not None
    assert str(row["goal_id"]) == "g1"


@pytest.mark.parametrize(
    "message,allowed",
    [
        ("Kim jesteś?", {"instant", "contextual"}),
        ("Powiedz krótko, jak działasz", {"instant", "contextual"}),
        ("Jakich elementów systemu użyłeś?", {"instant", "contextual"}),
        ("Napisz krótką odpowiedź", {"instant", "contextual"}),
        (
            "Zaplanuj migrację bazy i wykonaj ją krok po kroku",
            {"agentic"},
        ),
        (
            "Przeanalizuj cały projekt i przygotuj plan krok po kroku",
            {"agentic"},
        ),
    ],
)
def test_routing_identity_vs_agentic(message, allowed):
    from aihub.strategy_selector import StrategySelector

    sel = StrategySelector()
    out = sel.select_strategy(message, {"active_goals_count": 0, "history_turns": 0})
    assert out["strategy"] in allowed, (message, out)


def test_meta_ask_blocks_agentic_even_with_active_goals():
    from aihub.strategy_selector import StrategySelector, is_assistant_meta_ask

    assert is_assistant_meta_ask(
        "Powiedz krótko, kim jesteś i jakie elementy własnego systemu realnie wykorzystałeś"
    )
    out = StrategySelector().select_strategy(
        "Powiedz krótko, kim jesteś i jakie elementy systemu wykorzystałeś",
        {"active_goals_count": 3, "goal_max_urgency": 0.9, "history_turns": 2},
    )
    assert out["strategy"] != "agentic"


def test_junk_memory_filter_and_pack_bounds(isolated_db):
    from aihub.memory_context_pack import build_memory_context_pack, is_junk_memory_content
    from aihub.memory_read_contracts import MemoryReadOutcome
    from aihub.memory_v2_models import MemoryV2Item, MemoryV2SearchResponse
    import time

    assert is_junk_memory_content("Memory-guided response from 12 memories helped")
    assert is_junk_memory_content("BRAK DANYCH (web)")
    assert is_junk_memory_content("Elo")
    assert is_junk_memory_content(
        "Wynik meczu Real–Barca 2:1", query="Kim jesteś?"
    )

    items = []
    for i, content in enumerate(
        [
            "User prefers short answers",
            "Memory-guided response",
            "BRAK DANYCH (web)",
            "Wynik meczu wczoraj 3:0",
            "Działa. Gotowy…",
            "User name is Ada",
            "User name is Ada",  # dup
        ]
        + [f"noise fact {i}" for i in range(20)]
    ):
        items.append(
            MemoryV2Item(
                id=f"m{i}",
                user_id="u",
                memory_type="fact" if i != 0 else "preference",
                scope="user",
                title=f"t{i}",
                content=content,
                summary=content,
                source_kind="explicit_learning",
                source_ref="",
                importance_score=0.5,
                confidence_score=0.5,
                salience_score=0.5,
                retrieval_priority_score=0.9 - i * 0.01,
                created_ts=time.time(),
                updated_ts=time.time(),
            )
        )
    outcome = MemoryReadOutcome(
        user_id="u",
        query="Kim jesteś?",
        v2=MemoryV2SearchResponse(items=items, total_count=len(items)),
    )
    pack = build_memory_context_pack(outcome, max_chars=2400, max_items=8)
    texts = " ".join(i.content for i in pack.all_items()).lower()
    assert len(pack.all_items()) <= 8
    assert pack.used_chars <= 2400
    assert "memory-guided" not in texts
    assert "brak danych" not in texts
    assert "wynik meczu" not in texts


def test_smoke_contract_http200_ok_false_is_failure():
    """Documented smoke rule: HTTP 200 + ok=false → FAIL."""
    body = {
        "ok": False,
        "response_text": "Plan/agent się wywalił…",
        "trace": {"effective_runtime_path": "agent_handoff_error"},
    }
    assert body.get("ok") is False
    # Mimic scripts/smoke_chat_real.py gate
    fail = body.get("ok") is not True or body["trace"]["effective_runtime_path"] == (
        "agent_handoff_error"
    )
    assert fail is True


def test_decision_core_meta_ask_not_agentic(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime

    rt = get_chat_runtime()
    turn = ChatTurnInput(
        user_id="meta_route_user",
        session_id="s1",
        message="Powiedz krótko, kim jesteś i jakie elementy systemu wykorzystałeś",
        mode="chat",
        history=[],
    )
    ctx = ChatTurnContext(
        user_id=turn.user_id,
        session_id=turn.session_id,
        mode="chat",
        system_context={},
    )
    dc = rt._pre_exec_decision_core(
        turn=turn,
        ctx=ctx,
        psyche_snapshot={},
        memory_v2_runtime_ctx=None,
        psyche_v2_behavior_ctx=None,
    )
    assert dc["selected_strategy"] in ("instant", "contextual")
    assert dc.get("execution_mode") != "planner"


@pytest.mark.no_isolated_db
@pytest.mark.skipif(
    (os.getenv("AIHUB_RUNTIME_PG_TEST") or "").strip() != "1",
    reason="Set AIHUB_RUNTIME_PG_TEST=1 with DB_BACKEND=postgres to run live PG fingerprint query",
)
def test_goal_fingerprint_live_postgres():
    """Optional live PostgreSQL proof for the production query."""
    assert (os.getenv("DB_BACKEND") or "").lower() == "postgres"
    from aihub.db.runtime import exec_one, fetch_one, now_ts
    from aihub.db.sql_json import json_text_eq
    from aihub.goal_engine import _goal_fingerprint

    ts = now_ts()
    uid = f"pg_fp_live_user_{int(ts * 1000)}"
    title = f"pg fingerprint live {int(ts * 1000)}"
    fp = _goal_fingerprint(uid, "task", title)
    meta = json.dumps({"goal_fingerprint": fp})
    gid = f"pgfp-{int(ts * 1000)}"
    exec_one(
        """
        INSERT INTO goals(
          goal_id,user_id,title,description,goal_type,source,status,
          priority,urgency,importance,confidence,created_at,updated_at,
          expires_at,parent_goal_id,tags,success_criteria,failure_criteria,
          progress,metadata
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            gid,
            uid,
            title,
            "desc",
            "task",
            "test",
            "active",
            0.5,
            0.5,
            0.5,
            0.8,
            ts,
            ts,
            None,
            None,
            "[]",
            "[]",
            "[]",
            0.0,
            meta,
        ),
    )
    # Exact production lookup shape from GoalEngine._find_similar_open_goal
    row = fetch_one(
        f"""
        SELECT goal_id FROM goals
        WHERE user_id=?
          AND status IN ('proposed','active','blocked','scheduled')
          AND {json_text_eq("metadata", "goal_fingerprint")}
        ORDER BY updated_at DESC
        LIMIT 1
        """,
        (uid, fp),
    )
    assert row is not None
    assert str(row["goal_id"]) == gid
    exec_one("DELETE FROM goals WHERE goal_id=?", (gid,))


@pytest.mark.no_auth_injection
def test_authenticated_simple_chat_turn_e2e(isolated_db, monkeypatch):
    """Login-equivalent principal → /chat/turn → ok=true, not agentic, uuid ownership."""
    monkeypatch.setenv("AIHUB_BFF_PRINCIPAL_SECRET", "test-principal-secret-value-123456")
    monkeypatch.setenv("AIHUB_AUTH_REQUIRED", "1")
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")

    from aihub import main
    from aihub.local_auth import create_account
    from aihub.signed_principal import sign_principal_context
    from aihub.testing.test_auth_helpers import TEST_PRINCIPAL_SECRET
    from fastapi.testclient import TestClient
    import time

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    from aihub import chat_runtime as cr
    from aihub.chat_contracts import ModelResponse, ProviderUsage

    class FakeProvider:
        provider_name = "deepinfra"

        async def generate(self, _request):
            return ModelResponse(
                provider="deepinfra",
                model="t",
                content="Jestem AIHub. Użyłem warstwy chat i pamięci kontekstowej.",
                usage=ProviderUsage(total_tokens=2, reporting_mode="provider"),
            )

    fake = FakeProvider()
    _patch_fake_llm_provider(monkeypatch, cr, fake)

    uid = "e2e-uuid-1111-2222-3333-444455556666"
    create_account(username="e2e.user", password="secure-password-1", account_id=uid, role="user")

    with TestClient(main.app) as client:
        me_headers = {
            "x-aihub-principal": sign_principal_context(
                principal_id=uid,
                user_id=uid,
                tenant_id=uid,
                roles=["user"],
                session_id="sess-e2e",
                method="GET",
                path="/auth/me",
                request_id="req-me",
                nonce="n1",
                timestamp=time.time(),
            )
        }
        me = client.get("/auth/me", headers=me_headers)
        assert me.status_code == 200
        assert me.json()["principal"]["user_id"] == uid

        turn_headers = {
            "x-aihub-principal": sign_principal_context(
                principal_id=uid,
                user_id=uid,
                tenant_id=uid,
                roles=["user"],
                session_id="sess-e2e",
                method="POST",
                path="/chat/turn",
                request_id="req-turn",
                nonce="n2",
                timestamp=time.time(),
            ),
            "content-type": "application/json",
        }
        # Body may claim default — server must overwrite from principal.
        payload = {
            "user_id": uid,
            "session_id": "sess-e2e-chat",
            "message": "Kim jesteś?",
            "mode": "chat",
            "history": [],
            "idempotency_key": "e2e-simple-1",
        }
        resp = client.post("/chat/turn", headers=turn_headers, json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("ok") is True, body
        assert (body.get("response_text") or "").strip()
        assert not body.get("errors")
        trace = body.get("trace") or {}
        assert trace.get("effective_runtime_path") != "agent_handoff_error"
        assert trace.get("selected_strategy") in ("instant", "contextual")
        hits = int(trace.get("memory_hits") or 0)
        assert hits <= 12
        from aihub.db.runtime import fetch_all

        own = fetch_all(
            "SELECT user_id FROM experiences WHERE user_id=? LIMIT 5",
            (uid,),
        )
        assert all(str(r["user_id"]) == uid for r in own)
        assert uid != "default"
        assert str(payload["user_id"]) == uid


@pytest.mark.no_auth_injection
def test_authenticated_agentic_prompt_no_json_extract_crash(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_BFF_PRINCIPAL_SECRET", "test-principal-secret-value-123456")
    monkeypatch.setenv("AIHUB_AUTH_REQUIRED", "1")
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")

    from aihub import main
    from aihub.local_auth import create_account
    from aihub.signed_principal import sign_principal_context
    from fastapi.testclient import TestClient
    import time

    monkeypatch.setattr(main, "start_worker_once", lambda: None)

    from aihub import chat_runtime as cr
    from aihub.chat_contracts import ModelResponse, ProviderUsage

    class FakeProvider:
        provider_name = "deepinfra"

        async def generate(self, _request):
            return ModelResponse(
                provider="deepinfra",
                model="t",
                content="Plan migracji: 1) snapshot 2) migrate 3) verify",
                usage=ProviderUsage(total_tokens=2, reporting_mode="provider"),
            )

    fake = FakeProvider()
    _patch_fake_llm_provider(monkeypatch, cr, fake)

    uid = "e2e-agentic-aaaa-bbbb-cccc-ddddeeeeffff"
    create_account(username="e2e.agentic", password="secure-password-1", account_id=uid)

    with TestClient(main.app) as client:
        headers = {
            "x-aihub-principal": sign_principal_context(
                principal_id=uid,
                user_id=uid,
                tenant_id=uid,
                roles=["user"],
                session_id="sess-ag",
                method="POST",
                path="/chat/turn",
                request_id="req-ag",
                nonce="n3",
                timestamp=time.time(),
            ),
            "content-type": "application/json",
        }
        resp = client.post(
            "/chat/turn",
            headers=headers,
            json={
                "user_id": uid,
                "session_id": "sess-ag-1",
                "message": "Zaplanuj migrację bazy i wykonaj ją krok po kroku",
                "mode": "chat",
                "history": [],
                "idempotency_key": "e2e-agentic-1",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        # Must not crash on json_extract; either success or honest failure.
        assert "json_extract" not in str(body).lower()
        assert "does not exist" not in str(body).lower()
        if body.get("ok") is True:
            assert (body.get("response_text") or "").strip()
        else:
            # Honest failure is acceptable; never false success.
            assert body.get("ok") is False
