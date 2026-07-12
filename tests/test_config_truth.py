"""Enforce single config canon (aihub.config) + adapted core.config + OpenAPI path."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet


def test_canonical_config_paths_not_ai_hub_tree() -> None:
    import aihub.config as canon

    assert "/root/ai-hub" not in str(canon.DATA_DIR.resolve())
    assert "/root/ai-hub" not in str(canon.DB_PATH.resolve())
    assert "/root/ai-hub" not in str(canon.FS_ROOT.resolve())


def test_core_config_adapter_matches_canon_when_no_overrides() -> None:
    import aihub.config as canon
    from aihub.core.config import reload_settings

    s = reload_settings()
    assert s.host == canon.HOST
    assert s.port == canon.PORT
    assert Path(s.data_dir).resolve() == canon.DATA_DIR.resolve()
    assert Path(s.db_path).resolve() == canon.DB_PATH.resolve()
    assert Path(s.fs_root).resolve() == canon.FS_ROOT.resolve()


def test_core_config_aihub_host_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from aihub.core.config import reload_settings

    monkeypatch.setenv("AIHUB_HOST", "10.0.0.2")
    s = reload_settings()
    assert s.host == "10.0.0.2"


def test_gpt_openapi_spec_default_inside_repo() -> None:
    import aihub.config as canon

    p = canon.gpt_openapi_spec_path()
    assert p.name == "openapi-gpt.json"
    assert (p.parent / "aihub").is_dir()
    assert p.is_file()


def test_gpt_openapi_spec_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import aihub.config as canon

    f = tmp_path / "custom.json"
    f.write_text('{"openapi":"3.0.0","info":{"title":"t","version":"1"},"paths":{}}')
    monkeypatch.setenv("AIHUB_GPT_OPENAPI_PATH", str(f))
    assert canon.gpt_openapi_spec_path().resolve() == f.resolve()


def _clear_secret_envs(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LLM_API_KEY",
        "DEEPINFRA_API_KEY",
        "BRAVE_API_KEY",
        "VOYAGE_API_KEY",
        "AIHUB_USER_VAULT_KEY",
        "AIHUB_API_KEY",
        "HUB_API_KEY",
        "API_KEY",
        "AIHUB_PROXY_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)


def test_production_missing_all_secrets_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """06.07 P0: production must not start with no secrets at all (fail-fast, not fail-open)."""
    import aihub.config as canon

    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError) as exc_info:
        canon._validate_production_secrets()
    msg = str(exc_info.value)
    assert "AIHUB_USER_VAULT_KEY" in msg
    assert "AIHUB_API_KEY" in msg or "hub auth secret" in msg


def test_production_missing_only_vault_key_fails_fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """06.07 P0: vault key specifically must be required in production (no derivable fallback)."""
    import aihub.config as canon

    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "x")
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    monkeypatch.setenv("VOYAGE_API_KEY", "x")
    monkeypatch.setenv("API_KEY", "x")
    with pytest.raises(RuntimeError, match="AIHUB_USER_VAULT_KEY"):
        canon._validate_production_secrets()


def test_production_missing_auth_secret_fails_fast_even_with_other_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """06.07 P0: no hub auth secret means the auth middleware fails OPEN — must block prod start."""
    import aihub.config as canon

    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("DEEPINFRA_API_KEY", "x")
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    monkeypatch.setenv("VOYAGE_API_KEY", "x")
    monkeypatch.setenv("AIHUB_USER_VAULT_KEY", Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="hub auth secret"):
        canon._validate_production_secrets()


def test_production_with_all_secrets_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihub.config as canon

    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("LLM_API_KEY", "x")
    monkeypatch.setenv("BRAVE_API_KEY", "x")
    monkeypatch.setenv("VOYAGE_API_KEY", "x")
    monkeypatch.setenv("AIHUB_USER_VAULT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("API_KEY", "x")
    canon._validate_production_secrets()  # must not raise


def test_dev_mode_does_not_require_any_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev/test keeps the controlled, non-production fallback path — no hard requirement here."""
    import aihub.config as canon

    _clear_secret_envs(monkeypatch)
    monkeypatch.setenv("ENV", "development")
    canon._validate_production_secrets()  # must not raise


def test_user_vault_fernet_raises_in_production_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """06.07 P0: aihub.user_vault must refuse the derivable dev fallback in production."""
    import aihub.user_vault as uv

    monkeypatch.delenv("AIHUB_USER_VAULT_KEY", raising=False)
    monkeypatch.setenv("ENV", "production")
    with pytest.raises(RuntimeError, match="AIHUB_USER_VAULT_KEY"):
        uv._fernet()


def test_user_vault_fernet_dev_fallback_works_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aihub.user_vault as uv

    monkeypatch.delenv("AIHUB_USER_VAULT_KEY", raising=False)
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("AIHUB_LOCAL_TEST_PROFILE", "1")
    f = uv._fernet()
    token = f.encrypt(b"secret")
    assert f.decrypt(token) == b"secret"


def test_user_vault_requires_explicit_key_outside_local_test_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aihub.user_vault as uv

    monkeypatch.delenv("AIHUB_USER_VAULT_KEY", raising=False)
    monkeypatch.delenv("AIHUB_LOCAL_TEST_PROFILE", raising=False)
    monkeypatch.setenv("ENV", "development")
    with pytest.raises(RuntimeError, match="explicit local test profile"):
        uv._fernet()


def test_user_vault_rejects_weak_explicit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aihub.user_vault as uv

    monkeypatch.setenv("AIHUB_USER_VAULT_KEY", "x" * 44)
    with pytest.raises(RuntimeError, match="weak"):
        uv._fernet()


def test_llm_key_aliases_use_one_resolver() -> None:
    import aihub.config as canon

    assert canon.resolve_llm_api_key({"LLM_API_KEY": "canonical"}) == "canonical"
    assert (
        canon.resolve_llm_api_key({"DEEPINFRA_API_KEY": "legacy-alias"})
        == "legacy-alias"
    )
    assert (
        canon.resolve_llm_api_key(
            {"LLM_API_KEY": "canonical", "DEEPINFRA_API_KEY": "legacy-alias"}
        )
        == "canonical"
    )


def test_gpt_openapi_http_not_under_ai_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    import aihub.config as canon
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    monkeypatch.setattr(
        "aihub.main.knowledge_graph.stats",
        lambda: {"nodes": 0},
    )

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        r = client.get("/gpt-openapi.json")
    assert r.status_code == 200
    assert "/root/ai-hub" not in str(canon.gpt_openapi_spec_path())


def test_gpt_openapi_never_serves_empty_paths_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """06.07 P1: /gpt-openapi.json must not serve a dead '\"paths\": {}' stub.

    The repo-default ``openapi-gpt.json`` intentionally ships with empty ``paths`` (documented as
    an override slot). ``GET /gpt-openapi.json`` must detect that and fall back to the live app
    schema instead of returning the empty stub verbatim.
    """
    from aihub import main

    monkeypatch.setattr(main, "start_worker_once", lambda: None)
    monkeypatch.setattr("aihub.main.knowledge_graph.stats", lambda: {"nodes": 0})

    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        r = client.get("/gpt-openapi.json")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("paths"), dict)
    assert len(body["paths"]) > 0, "gpt-openapi.json must not serve an empty paths stub"


def test_stop_new_is_wrapper_to_stop() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "stop_new.sh").read_text(encoding="utf-8")
    assert "NON-CANONICAL" in text or "LEGACY" in text
    assert "exec " in text
    assert "stop.sh" in text


def test_env_example_documents_runtime_flags() -> None:
    root = Path(__file__).resolve().parents[1]
    ex = (root / ".env.example").read_text(encoding="utf-8")
    assert "AIHUB_DISABLE_LEGACY_STM_TURN" in ex
    assert "AIHUB_DISABLE_LEGACY_MEMORY_V1_HTTP" in ex
    assert "AIHUB_GPT_OPENAPI_PATH" in ex
    assert "SELF_HEAL_WRITE" in ex


def test_firewall_and_recorder_middleware_not_registered_on_app() -> None:
    """06.07 P1: firewall/recorder middleware must not silently start protecting/recording.

    Both classes are LEGACY / NOT REGISTERED (see module docstrings in
    ``aihub/middleware/firewall.py`` and ``aihub/middleware/recorder.py``). This test locks that
    decision in: if someone adds ``app.add_middleware(FirewallMiddleware)`` or
    ``app.add_middleware(EventRecorderMiddleware)`` to ``aihub/main.py``, this test must be
    updated deliberately (and the recorder's body redaction must be addressed first).
    """
    from aihub.main import app
    from aihub.middleware.firewall import FirewallMiddleware
    from aihub.middleware.recorder import EventRecorderMiddleware

    registered_middleware_classes = {m.cls for m in app.user_middleware}
    assert FirewallMiddleware not in registered_middleware_classes
    assert EventRecorderMiddleware not in registered_middleware_classes


def test_admin_router_collision_archived_not_in_aihub_package() -> None:
    """06.07 P1: the colliding /admin router (unredacted body leak) was archived, not left unmounted."""
    import aihub

    aihub_root = Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "admin_router.py").exists()

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.admin_router")

    repo_root = aihub_root.parent
    assert (repo_root / "archive" / "legacy_routers" / "admin_router.py").is_file()
