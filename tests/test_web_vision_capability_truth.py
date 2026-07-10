from __future__ import annotations

import aihub.ops_platform as ops
import aihub.web_tools as web_tools


def test_web_health_ok_when_brave_token_valid(monkeypatch):
    monkeypatch.delenv("AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS", raising=False)
    monkeypatch.setenv("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", "1")
    monkeypatch.setattr("aihub.config.BRAVE_API_KEY", "valid-token", raising=False)
    monkeypatch.setattr(
        web_tools,
        "_brave_token_live_status",
        lambda key: {"probed": True, "valid": True, "http_status": 200},
    )
    wh = web_tools.web_health()
    assert wh["ok"] is True
    assert wh["research"]["brave_configured"] is True
    assert wh["research"]["brave_live"]["valid"] is True


def test_web_health_degraded_when_brave_token_invalid_and_no_public_backends(monkeypatch):
    """A present-but-invalid Brave token (HTTP 422) must not report a working web backend."""
    monkeypatch.setenv("AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS", "0")
    monkeypatch.setenv("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", "1")
    monkeypatch.setattr("aihub.config.BRAVE_API_KEY", "invalid-token", raising=False)
    monkeypatch.setattr(
        web_tools,
        "_brave_token_live_status",
        lambda key: {
            "probed": True,
            "valid": False,
            "http_status": 422,
            "reason": "brave_token_invalid",
        },
    )
    wh = web_tools.web_health()
    assert wh["ok"] is False
    assert wh["research"]["brave_configured"] is True
    assert wh["research"]["brave_live"]["valid"] is False


def test_web_health_ok_when_public_backends_enabled_even_without_brave(monkeypatch):
    monkeypatch.setenv("AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS", "1")
    monkeypatch.delenv("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", raising=False)
    monkeypatch.setattr("aihub.config.BRAVE_API_KEY", "", raising=False)
    wh = web_tools.web_health()
    assert wh["ok"] is True
    assert wh["research"]["optional_public_backends"] is True


def test_web_health_no_live_probe_keeps_configured_semantics(monkeypatch):
    """Without live probe we do not fabricate validity; a present key stays 'usable'."""
    monkeypatch.delenv("AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS", raising=False)
    monkeypatch.delenv("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", raising=False)
    monkeypatch.setattr("aihub.config.BRAVE_API_KEY", "some-token", raising=False)
    wh = web_tools.web_health()
    assert wh["ok"] is True
    assert "brave_live" not in wh["research"]


def test_ops_vision_openai_compatible_uses_llm_fallback(monkeypatch):
    """openai_compatible vision with blank vision key/url must resolve via LLM_* and report ok."""
    monkeypatch.setattr("aihub.config.CHAT_VISION_ENABLED", True, raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_BACKEND", "openai_compatible", raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct", raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_API_URL", "", raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_API_KEY", "", raising=False)
    monkeypatch.setattr("aihub.config.LLM_BASE_URL", "https://api.deepinfra.com/v1/openai", raising=False)
    monkeypatch.setattr("aihub.config.LLM_API_KEY", "llm-key", raising=False)

    health = ops.get_platform_health()
    vision = health["layers"]["vision"]
    assert vision["status"] == "ok", vision
    assert vision["backend"] == "openai_compatible"


def test_ops_vision_openai_compatible_degraded_without_any_key(monkeypatch):
    monkeypatch.setattr("aihub.config.CHAT_VISION_ENABLED", True, raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_BACKEND", "openai_compatible", raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_MODEL", "Qwen/Qwen3-VL-30B-A3B-Instruct", raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_API_URL", "", raising=False)
    monkeypatch.setattr("aihub.config.CHAT_VISION_API_KEY", "", raising=False)
    monkeypatch.setattr("aihub.config.LLM_BASE_URL", "", raising=False)
    monkeypatch.setattr("aihub.config.LLM_API_KEY", "", raising=False)

    health = ops.get_platform_health()
    vision = health["layers"]["vision"]
    assert vision["status"] == "degraded", vision
    assert vision["reason"] == "vision_remote_not_configured"
