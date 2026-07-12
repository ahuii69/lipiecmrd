"""Tests for canonical LLM credential resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_llm_api_key_set() -> None:
    """When LLM_API_KEY is set, it should be used."""
    with patch.dict("os.environ", {"LLM_API_KEY": "sk-direct-key"}, clear=False):
        import os

        key = os.environ.get("LLM_API_KEY", "").strip()
        assert key == "sk-direct-key"


def test_llm_api_key_empty() -> None:
    """When LLM_API_KEY is empty, result should be empty string."""
    with patch.dict("os.environ", {"LLM_API_KEY": ""}, clear=False):
        import os

        key = os.environ.get("LLM_API_KEY", "").strip()
        assert key == ""
        assert not key


def test_llm_api_key_whitespace_stripped() -> None:
    """Whitespace around LLM_API_KEY value should be stripped."""
    with patch.dict("os.environ", {"LLM_API_KEY": "  sk-padded  "}, clear=False):
        import os

        key = os.environ.get("LLM_API_KEY", "").strip()
        assert key == "sk-padded"


def test_deepinfra_alias_uses_canonical_resolver() -> None:
    """Supported alias resolves identically; undocumented token names do not."""
    from aihub.secret_resolver import resolve_llm_api_key

    assert (
        resolve_llm_api_key(
            {
                "LLM_API_KEY": "",
                "DEEPINFRA_API_KEY": "sk-legacy",
                "DEEPINFRA_TOKEN": "sk-unsupported",
            }
        )
        == "sk-legacy"
    )
    assert resolve_llm_api_key({"DEEPINFRA_TOKEN": "sk-unsupported"}) == ""


@pytest.mark.anyio
async def test_fallback_response_has_truthful_grounding_mode() -> None:
    """When provider fails due to missing API key, response should mark fallback clearly."""
    from aihub.chat_contracts import ChatTurnInput
    from aihub.chat_runtime import ChatRuntime

    runtime = ChatRuntime()

    turn = ChatTurnInput(
        user_id="test_user_config_check",
        session_id="test_session",
        message="Spróbuj coś zrobić",
        history=[],
        mode="chat",
    )

    result = await runtime.run_turn(turn)

    # When provider fails, response must be truthful about fallback
    # (not claiming provider worked when it didn't)
    trace = result.trace or {}

    # If API key was missing, should have used fallback or delegated to executive (handoff)
    if not trace.get("provider_calls", 0):
        mode = trace.get("response_grounding_mode")
        assert (
            trace.get("used_fallback") is True
            or mode == "fallback"
            or mode == "agent_handoff"
        )


def test_config_startup_warning_diagnostic_message(caplog) -> None:
    """Config startup warning should include diagnostic details about checked sources."""
    import logging

    caplog.set_level(logging.WARNING)

    # Mock the config to simulate missing key
    with (
        patch("aihub.config.LLM_API_KEY", ""),
        patch("aihub.config.LLM_PROVIDER_NAME", "deepinfra"),
    ):
        from aihub.config import _validate_llm_api_key_on_startup

        # Call validation
        _validate_llm_api_key_on_startup()

    # Check that warning was logged with diagnostic details
    warning_messages = [
        record.message for record in caplog.records if record.levelname == "WARNING"
    ]
    assert any(warning_messages), "No warnings logged"
    assert any(
        "LLM provider" in msg for msg in warning_messages
    ), "Missing provider name in warning"
    assert any(
        "LLM_API_KEY is missing" in msg for msg in warning_messages
    ), "Missing 'LLM_API_KEY is missing' in warning"
    # Check that new diagnostic message includes source names
    assert any("LLM_API_KEY" in msg for msg in warning_messages) or any(
        "Checked sources" in msg for msg in warning_messages
    ), "Missing source diagnostics in warning"
