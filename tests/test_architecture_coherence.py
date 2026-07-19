"""Architecture coherence: Memory V1↔V2, executive→registry, vision config."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock


def test_executive_aliases_map_to_registry_names():
    from aihub.tools.executive_dispatch import resolve_executive_tool_name

    assert resolve_executive_tool_name("web.fetch") == "web.fetch_url"
    assert resolve_executive_tool_name("web_fetch") == "web.fetch_url"
    assert resolve_executive_tool_name("fs.write") == "fs.write_file"
    assert resolve_executive_tool_name("fs_write") == "fs.write_file"
    assert resolve_executive_tool_name("snapshot") == "snapshot.create"
    assert resolve_executive_tool_name("memory.search") == "memory.search"
    assert resolve_executive_tool_name("image.generate") == "image.generate"


def test_dispatch_executive_web_fetch_via_registry(monkeypatch):
    import aihub.web_tools as wt
    from aihub.tools.executive_dispatch import dispatch_executive_tool

    fetch_mock = AsyncMock(
        return_value={"ok": True, "status": 200, "text": "hello", "url": "https://ex.com"}
    )
    monkeypatch.setattr(wt, "fetch_url", fetch_mock)

    out = asyncio.get_event_loop().run_until_complete(
        dispatch_executive_tool(
            user_id="exec_dispatch_user",
            tool_name="web.fetch",
            arguments={"url": "https://example.com"},
        )
    )
    assert out["ok"] is True
    assert out["tool"] == "web.fetch_url"
    fetch_mock.assert_awaited_once()


def test_vision_config_uses_resolved_llm_key(monkeypatch):
    """CHAT_VISION_API_KEY must fall back to resolved LLM_API_KEY, not raw env only."""
    monkeypatch.setenv("LLM_API_KEY", "sk-vision-test-key")
    monkeypatch.delenv("CHAT_VISION_API_KEY", raising=False)
    monkeypatch.delenv("CHAT_VISION_ENABLED", raising=False)
    monkeypatch.delenv("CHAT_VISION_BACKEND", raising=False)
    monkeypatch.delenv("CHAT_VISION_MODEL", raising=False)
    monkeypatch.delenv("CHAT_VISION_API_URL", raising=False)
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepinfra.com/v1/openai")

    import importlib

    import aihub.config as cfg

    importlib.reload(cfg)
    try:
        assert cfg.CHAT_VISION_API_KEY == "sk-vision-test-key"
        assert cfg.CHAT_VISION_BACKEND == "openai_compatible"
        assert cfg.CHAT_VISION_API_URL.endswith("/v1/openai") or "deepinfra" in cfg.CHAT_VISION_API_URL
        assert cfg.CHAT_VISION_MODEL
        assert cfg.CHAT_VISION_ENABLED is True
    finally:
        # Restore module for other tests
        importlib.reload(cfg)


def test_psyche_brief_labels_v1_role():
    from aihub.turn.mixins.prompt_context import PromptContextMixin

    class _T(PromptContextMixin):
        pass

    brief = _T()._build_psyche_brief(
        {"mood": 0.5, "energy": 0.6, "focus": 0.7, "style": "direct", "traits": {"directness": 0.8}}
    )
    assert "Psyche V1" in brief
    assert "snapshot" in brief.lower() or "V2" in brief
