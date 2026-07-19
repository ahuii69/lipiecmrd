#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PromptContextMixin + chat privilege gate regressions."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from aihub.chat_contracts import ChatTurnInput


def test_chat_turn_rejects_unknown_tool_policy_overrides():
    with pytest.raises(ValidationError):
        ChatTurnInput(
            user_id="u",
            session_id="s",
            message="hej",
            tool_policy_overrides={"unknown_policy_switch": True},
        )


def test_chat_turn_accepts_validated_tool_policy_overrides():
    turn = ChatTurnInput(
        user_id="u",
        session_id="s",
        message="hej",
        tool_policy_overrides={"allow_sensitive_mutations": True},
    )
    assert turn.tool_policy_overrides == {"allow_sensitive_mutations": True}


def test_local_guardrail_uses_effective_attachments(monkeypatch):
    from aihub.turn.ops import TurnOps

    monkeypatch.setattr(
        "aihub.turn.mixins.prompt_context.fetch_recent_session_attachment_ids",
        lambda **_kwargs: ["file_from_session"],
    )
    ops = TurnOps()
    decision = {
        "selected_strategy": "instant",
        "web_decision": "off",
        "web_decision_reason": "",
        "reason_codes": [],
    }
    # Deixis → session attachment IDs; freshness keyword must NOT force web.
    turn = ChatTurnInput(
        user_id="u",
        session_id="s",
        message="co widzisz na tym obrazku i jaki jest dzisiaj kurs",
        attached_file_ids=[],
    )
    ops._local_non_research_guardrails(turn, decision)
    assert decision.get("web_decision") != "required"
    assert decision.get("web_decision_reason") != "freshness_guardrail"


def test_privilege_gates_strip_debug_and_sensitive_for_non_admin():
    from types import SimpleNamespace

    from aihub.chat_api import _apply_chat_turn_privilege_gates
    from aihub.local_auth import Principal

    request = SimpleNamespace(
        state=SimpleNamespace(
            principal=Principal(
                account_id="acc",
                username="user",
                tenant_id="t",
                role="user",
                status="active",
                session_id="sess",
                csrf_token="x",
                expires_at=9_999_999_999.0,
            )
        )
    )
    payload = ChatTurnInput(
        user_id="other",
        session_id="s",
        message="hej",
        include_debug=True,
        tool_policy_overrides={"allow_sensitive_mutations": True},
    )
    gated = _apply_chat_turn_privilege_gates(request, payload)  # type: ignore[arg-type]
    assert gated.user_id == "acc"
    assert gated.include_debug is False
    assert gated.tool_policy_overrides.get("allow_sensitive_mutations") is False


def test_privilege_gates_keep_operator_switches_for_admin():
    from types import SimpleNamespace

    from aihub.chat_api import _apply_chat_turn_privilege_gates
    from aihub.local_auth import Principal

    request = SimpleNamespace(
        state=SimpleNamespace(
            principal=Principal(
                account_id="adm",
                username="admin",
                tenant_id="t",
                role="admin",
                status="active",
                session_id="sess",
                csrf_token="x",
                expires_at=9_999_999_999.0,
            )
        )
    )
    payload = ChatTurnInput(
        user_id="adm",
        session_id="s",
        message="hej",
        include_debug=True,
        tool_policy_overrides={"allow_sensitive_mutations": True},
    )
    gated = _apply_chat_turn_privilege_gates(request, payload)  # type: ignore[arg-type]
    assert gated.include_debug is True
    assert gated.tool_policy_overrides.get("allow_sensitive_mutations") is True
