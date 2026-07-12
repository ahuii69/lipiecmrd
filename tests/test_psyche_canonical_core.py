"""PsycheCanonicalCore: single v1 delegation + shared PsycheV2Service for all adapters."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_get_psyche_core_singleton_and_v2_identity(isolated_db):
    from aihub.psyche_core import PsycheCanonicalCore, get_psyche_core

    a = get_psyche_core()
    b = get_psyche_core()
    assert a is b
    assert isinstance(a, PsycheCanonicalCore)
    assert a.v2_service is b.v2_service


def test_runtime_psyche_bridge_uses_same_v2_service_as_core(isolated_db):
    import aihub.runtime_psyche_bridge as rpb
    from aihub.psyche_core import get_psyche_core

    rpb.build_psyche_v2_runtime_snapshot("bridge_psyche_singleton_user")
    assert rpb._canonical_v2() is get_psyche_core().v2_service


def test_turn_completed_exactly_once_psyche_and_reflection(isolated_db, monkeypatch):
    """One TurnCompleted event applies memory once, psyche once, reflection once."""
    from aihub.db import fetch_all
    from aihub.durable_jobs import execute_turn_completed_inline
    from aihub.psyche_core import PsycheCanonicalCore, get_psyche_core
    from aihub.reflection_engine import ReflectionEngine

    uid = "turn_completed_exactly_once"
    get_psyche_core().ensure_user(uid)
    evolve_calls: list[tuple[str, str, str]] = []
    orig_evolve = PsycheCanonicalCore.evolve

    def track_evolve(self, user_id: str, text: str, role: str):
        evolve_calls.append((user_id, text, role))
        return orig_evolve(self, user_id, text, role)

    monkeypatch.setattr(PsycheCanonicalCore, "evolve", track_evolve)

    reflection_calls: list[str] = []
    orig_reflect = ReflectionEngine.reflect

    def track_reflect(self, rinput):
        reflection_calls.append(rinput.user_id)
        return orig_reflect(self, rinput)

    monkeypatch.setattr(ReflectionEngine, "reflect", track_reflect)

    turn_id = "turn-exactly-once-001"
    execute_turn_completed_inline(
        turn_id=turn_id,
        user_id=uid,
        user_message="hello user",
        assistant_message="hello assistant",
        intent="chat",
        metadata={"source": "test"},
    )
    assert evolve_calls == [(uid, "hello user", "user"), (uid, "hello assistant", "assistant")]
    assert reflection_calls == [uid]

    receipts = fetch_all(
        "SELECT handler, status FROM durable_job_receipts ORDER BY handler"
    )
    assert {row["handler"] for row in receipts} == {"memory", "psyche", "reflection"}
    assert all(row["status"] == "completed" for row in receipts)

    evolve_before_redelivery = list(evolve_calls)
    reflection_before_redelivery = list(reflection_calls)
    execute_turn_completed_inline(
        turn_id=turn_id,
        user_id=uid,
        user_message="hello user",
        assistant_message="hello assistant",
        intent="chat",
        metadata={"source": "test"},
    )
    assert evolve_calls == evolve_before_redelivery
    assert reflection_calls == reflection_before_redelivery


def test_ingest_turn_does_not_invoke_psyche_core_evolve(isolated_db, monkeypatch):
    from aihub.memory_core import MemoryCanonicalCore
    from aihub.psyche_core import PsycheCanonicalCore

    uid = "ingest_no_psyche_side_effect"
    calls: list[tuple[str, str, str]] = []
    orig = PsycheCanonicalCore.evolve

    def track_evolve(self, user_id: str, text: str, role: str):
        calls.append((user_id, text, role))
        return orig(self, user_id, text, role)

    monkeypatch.setattr(PsycheCanonicalCore, "evolve", track_evolve)
    MemoryCanonicalCore().ingest_turn(uid, "u", "a", "chat", {})
    assert calls == []


def test_v1_psyche_http_uses_core(isolated_db, monkeypatch):
    from aihub import main
    from aihub.psyche_core import PsycheCanonicalCore, get_psyche_core

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    uid = "http_psyche_core_user"
    get_psyche_core().ensure_user(uid)
    seen: list[str] = []
    orig = PsycheCanonicalCore.ensure_user

    def tracked(self, user_id: str):
        seen.append(user_id)
        return orig(self, user_id)

    monkeypatch.setattr(PsycheCanonicalCore, "ensure_user", tracked)
    with TestClient(main.app) as client:
        r = client.get(f"/psyche/{uid}")
    assert r.status_code == 200
    assert uid in seen


def test_cockpit_psyche_v2_matches_core_snapshot(isolated_db, monkeypatch):
    from aihub import main
    from aihub.psyche_core import get_psyche_core

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    uid = "cockpit_psyche_core_snap"
    get_psyche_core().v2_service.ensure_user(uid)
    direct = get_psyche_core().v2_service.get_snapshot(uid)
    with TestClient(main.app) as client:
        http = client.get(f"/cockpit/psyche-v2/{uid}")
    assert http.status_code == 200
    body = http.json()
    assert body["user_id"] == direct.user_id
    assert body["state"]["current_mode"] == direct.state.current_mode
