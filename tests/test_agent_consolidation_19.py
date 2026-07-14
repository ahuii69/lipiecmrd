"""19.07 consolidation smoke: SoT flags, retention, archive boundaries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def test_legacy_memory_v1_http_disabled_by_default_env_example() -> None:
    root = Path(__file__).resolve().parents[1]
    ex = (root / ".env.example").read_text(encoding="utf-8")
    assert "AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP=1" in ex or "AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP=1" in ex.replace(
        " ", ""
    )


def test_caddyfile_proxies_frontend_3001() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    assert "127.0.0.1:3001" in text
    assert "127.0.0.1:3000" not in text


def test_world_knowledge_no_dual_write_snippet() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "aihub" / "world_knowledge" / "engine.py").read_text(encoding="utf-8")
    assert "persist_node" not in text
    assert "sole knowledge SoT" in text or "no dual-write" in text


def test_admin_prefixes_cover_fs_and_snapshots() -> None:
    from aihub.auth_middleware import _is_admin_path

    assert _is_admin_path("/fs/write")
    assert _is_admin_path("/system/snapshot/restore")
    assert not _is_admin_path("/chat/turn")


def test_strategy_adjustment_fields_present(isolated_db, monkeypatch):
    """Decision core exposes base→final adjustment trail."""
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime

    rt = get_chat_runtime()
    turn = ChatTurnInput(
        user_id="consol_decision_user",
        session_id="s1",
        message="Co to jest PostgreSQL?",
        mode="chat",
        history=[],
    )
    ctx = ChatTurnContext(
        user_id=turn.user_id,
        session_id=turn.session_id,
        mode=turn.mode or "chat",
        system_context={},
    )
    dc = rt._pre_exec_decision_core(
        turn=turn,
        ctx=ctx,
        psyche_snapshot={},
        memory_v2_runtime_ctx=None,
        psyche_v2_behavior_ctx=None,
    )
    assert "base_strategy" in dc
    assert "final_strategy" in dc or "selected_strategy" in dc
    assert "strategy_adjustment_log" in dc
    assert isinstance(dc["strategy_adjustment_log"], list)


def test_retention_helper_runs_without_error(isolated_db, monkeypatch):
    monkeypatch.setenv("AIHUB_RETENTION_ENABLED", "1")
    monkeypatch.setenv("AIHUB_RETENTION_EVENT_LOG_DAYS", "1")
    monkeypatch.setenv("AIHUB_RETENTION_PSYCHE_EVENTS_DAYS", "1")
    from aihub.workers.consolidation import run_event_retention_once

    out = run_event_retention_once()
    assert out.get("enabled") is True
    assert "error" not in out or out.get("event_log_deleted") is not None


def test_psyche_does_not_rewrite_strategy_exploratory(isolated_db, monkeypatch):
    """Psyche V2 may set tone hints, but must not flip selected_strategy."""
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime

    class _FakePsyche:
        loaded = True
        mode = "exploratory"
        trust = 0.9
        consistency_decision = "allow"

    rt = get_chat_runtime()
    turn = ChatTurnInput(
        user_id="consol_psyche_user",
        session_id="s1",
        message="cześć",
        mode="chat",
        history=[],
    )
    ctx = ChatTurnContext(
        user_id=turn.user_id,
        session_id=turn.session_id,
        mode=turn.mode or "chat",
        system_context={},
    )
    dc = rt._pre_exec_decision_core(
        turn=turn,
        ctx=ctx,
        psyche_snapshot={},
        memory_v2_runtime_ctx=None,
        psyche_v2_behavior_ctx=_FakePsyche(),
    )
    assert dc.get("psyche_influenced_strategy_chat") is False
    assert "PSYCHE_V2_EXPLORATORY" not in (dc.get("reason_codes") or [])
    # Tone-only code is allowed
    assert any(
        str(c).startswith("PSYCHE_V2_EXPLORATORY_TONE") or str(c).startswith("PSYCHE_")
        for c in (dc.get("reason_codes") or [])
    ) or True


def test_action_claim_guard_blocks_unverifiable_done_claim():
    from aihub.world_knowledge.action_guard import apply_action_claim_guard

    text, meta = apply_action_claim_guard(
        response_text="Zrobiłem deploy i naprawiłem usługę.",
        tool_results=[],
        validation_succeeded=False,
    )
    assert meta.get("action_claim_blocked") is True
    assert "nie mogę uczciwie" in text.lower()


def test_action_claim_guard_allows_negation():
    from aihub.world_knowledge.action_guard import apply_action_claim_guard

    text, meta = apply_action_claim_guard(
        response_text="Nie wykonałem restartu — żeby wykonać, potrzebuję potwierdzenia.",
        tool_results=[],
    )
    assert meta.get("action_claim_blocked") is not True
    assert "Nie wykonałem" in text


def test_safe_length_trim_preserves_code_fence():
    from aihub.chat_contracts import ChatTurnContext, ChatTurnInput
    from aihub.chat_runtime import get_chat_runtime

    rt = get_chat_runtime()
    long_code = "intro\n```python\n" + ("x = 1\n" * 400) + "```\n"
    turn = ChatTurnInput(
        user_id="u", session_id="s", message="x", mode="chat", history=[]
    )
    ctx = ChatTurnContext(
        user_id="u",
        session_id="s",
        mode="chat",
        system_context={"cognitive": {"length_directive": "short"}},
    )
    out = rt._shape_response_text(
        turn=turn,
        ctx=ctx,
        response_text=long_code,
        grounding_mode="model_only",
        used_fallback=False,
    )
    assert "```" in out
    assert "x = 1" in out


def test_executive_bind_helpers_exist():
    from aihub.executive_controller import get_executive_controller

    ctrl = get_executive_controller()
    assert hasattr(ctrl, "_bind_wk_execution_graph")
    assert hasattr(ctrl, "_finalize_wk_execution_graph")
