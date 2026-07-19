"""Shared fixtures for AI-Hub tests."""

import asyncio
import inspect
import os

# These must be set before importing aihub.main/agent_worker, because worker
# autostart flags are read at module import time.  Tests must never inherit
# production background workers or production Postgres from a real .env.
#
# Force ENV=test (not setdefault): a real `.env` often has ENV=production.
# If that value remains, `aihub.config._validate_production_secrets()` runs on
# every `importlib.reload(aihub.config)` in `isolated_db` after hub auth keys
# are cleared for isolation — and the whole suite aborts before any assertion.
os.environ["ENV"] = "test"
os.environ.setdefault("AGENT_AUTOSTART", "0")
os.environ.setdefault("AIHUB_BACKGROUND_AGENT_LOOP_ENABLED", "0")
os.environ.setdefault("AIHUB_CONSOLIDATION_WORKER", "0")
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
os.environ.setdefault("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
os.environ.setdefault("AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK", "0")
os.environ.setdefault("AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK", "1")
os.environ.setdefault("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", "0")
os.environ.setdefault("EMBEDDING_HEALTHCHECK_LIVE_PROBE", "0")
os.environ.setdefault("AIHUB_BFF_PRINCIPAL_SECRET", "test-principal-secret-value-123456")
# Unit tests use fake LLM providers; never hit live Groq/DeepInfra reserve in pytest.
os.environ.setdefault("GROQ_API_KEY", "")

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import TestClient as StarletteTestClient

from urllib.parse import parse_qs, urlparse

from aihub.auth_patch import HUB_KEY_ENV_NAMES
from aihub.logs import setup_test_logging
from aihub.main import app
from aihub.testing.runtime_reset import (
    assert_no_adapter_for_path,
    reset_runtime_for_tests,
)
from aihub.testing.test_auth_helpers import (
    infer_user_id,
    is_admin_test_path,
    is_public_test_path,
    signed_headers_for_request,
)

# Route test logs to logs/test.log (not aihub.log)
setup_test_logging()


def pytest_configure(config):
    """Register the ``asyncio`` marker used by ``async def`` tests.

    This environment (venv ``mrd-312``) intentionally does not ship ``pytest-asyncio``; only
    ``anyio`` is present. The suite's ``async def test_*`` functions are marked
    ``@pytest.mark.asyncio``. Without a runner, pytest fails them with "async def functions are
    not natively supported" before any assertion runs. We register the marker here (to silence
    the unknown-mark warning) and provide a minimal stdlib runner in ``pytest_pyfunc_call`` below.
    """
    config.addinivalue_line(
        "markers",
        "no_auth_injection: do not auto-sign TestClient requests (auth negative tests).",
    )
    config.addinivalue_line(
        "markers",
        "no_isolated_db: skip per-test isolated_db fixture (resource leak harness).",
    )
    config.addinivalue_line(
        "markers",
        "asyncio: run this coroutine test via asyncio.run() (stdlib runner defined in "
        "tests/conftest.py; this environment has no pytest-asyncio).",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Execute ``async def`` test functions using the stdlib event loop.

    Only handles genuine coroutine tests. Tests owned by the ``anyio`` plugin (marked
    ``@pytest.mark.anyio``) are left to anyio. Returning ``True`` tells pytest the call was
    handled so it does not additionally invoke the (un-awaited) coroutine. Fixtures are passed
    through unchanged, so assertions, fixtures and teardown behave exactly as for sync tests.
    """
    test_function = pyfuncitem.obj
    if not inspect.iscoroutinefunction(test_function):
        return None
    if pyfuncitem.get_closest_marker("anyio") is not None:
        return None

    argnames = pyfuncitem._fixtureinfo.argnames
    kwargs = {name: pyfuncitem.funcargs[name] for name in argnames}
    asyncio.run(test_function(**kwargs))
    return True


@pytest.fixture(autouse=True)
def inject_signed_principal_for_testclient(request, monkeypatch):
    """Sign backend requests in tests unless a case explicitly checks auth failures."""
    if request.node.get_closest_marker("no_auth_injection"):
        yield
        return

    original_request = StarletteTestClient.request
    original_stream = StarletteTestClient.stream

    def _request_path(url: object) -> str:
        text = str(url)
        if "://" in text:
            return urlparse(text).path or "/"
        return (text.split("?", 1)[0].split("#", 1)[0] or "/")

    def _request_query(url: object) -> str:
        text = str(url)
        if "://" in text:
            return urlparse(text).query
        if "?" not in text:
            return ""
        return text.split("?", 1)[1].split("#", 1)[0]

    def _query_user_id(url: object) -> str | None:
        values = parse_qs(_request_query(url)).get("user_id") or []
        return str(values[0]) if values else None

    def _params_user_id(params: object | None) -> str | None:
        if not params:
            return None
        if isinstance(params, dict):
            value = params.get("user_id")
            return str(value) if value is not None else None
        if isinstance(params, (list, tuple)):
            for item in params:
                if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "user_id":
                    return str(item[1])
        return None

    def _merge_signed_headers(
        *,
        method: str,
        url: object,
        headers: dict | None,
        json=None,
        data=None,
        content=None,
        params=None,
    ) -> dict:
        merged = dict(headers or {})
        lower = {str(k).lower(): v for k, v in merged.items()}
        if "x-aihub-principal" not in lower and "cookie" not in lower:
            path = _request_path(url)
            if not is_public_test_path(path):
                raw = content if content is not None else data
                user_id = infer_user_id(
                    path=path,
                    json_payload=json,
                    raw_content=raw,
                    query_user_id=_query_user_id(url) or _params_user_id(params),
                )
                merged.update(
                    signed_headers_for_request(
                        method=str(method).upper(),
                        path=path,
                        user_id=user_id,
                        roles=["admin"] if is_admin_test_path(path) else ["user"],
                    )
                )
        return merged

    def _request(self, method, url, *, headers=None, json=None, data=None, content=None, params=None, **kwargs):
        merged = _merge_signed_headers(
            method=str(method),
            url=url,
            headers=headers,
            json=json,
            data=data,
            content=content,
            params=params,
        )
        return original_request(
            self,
            method,
            url,
            headers=merged,
            json=json,
            data=data,
            content=content,
            params=params,
            **kwargs,
        )

    def _stream(self, method, url, *, headers=None, json=None, data=None, content=None, params=None, **kwargs):
        merged = _merge_signed_headers(
            method=str(method),
            url=url,
            headers=headers,
            json=json,
            data=data,
            content=content,
            params=params,
        )
        return original_stream(
            self,
            method,
            url,
            headers=merged,
            json=json,
            data=data,
            content=content,
            params=params,
            **kwargs,
        )

    monkeypatch.setattr(StarletteTestClient, "request", _request)
    monkeypatch.setattr(StarletteTestClient, "stream", _stream)
    yield


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch, request):
    """Use a temporary SQLite DB per test with full runtime reset on setup/teardown."""
    if request.node.get_closest_marker("no_isolated_db"):
        reset_runtime_for_tests()
        yield None
        reset_runtime_for_tests()
        return

    import importlib

    import aihub.config as cfg
    import aihub.db as db_pkg

    reset_runtime_for_tests()

    db_file = tmp_path / "test_aihub.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    # Keep reload outside production secret validation even if a test or shell
    # previously set ENV=production (production-secret tests monkeypatch ENV
    # themselves when they call `_validate_production_secrets` directly).
    monkeypatch.setenv("ENV", "test")
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_file.resolve()))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FS_ROOT", str(tmp_path / "fs"))
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    monkeypatch.setenv("AIHUB_CONSOLIDATION_WORKER", "0")
    vec_index = tmp_path / "vector.index"
    vec_meta = tmp_path / "vector_meta.json"
    monkeypatch.setenv("VECTOR_INDEX_PATH", str(vec_index))
    monkeypatch.setenv("VECTOR_META_PATH", str(vec_meta))
    for name in HUB_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_KEY", "")

    importlib.reload(cfg)
    monkeypatch.setattr(cfg, "DB_PATH", db_file.resolve())
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)

    import aihub.vector_engine as ve_mod
    from aihub.embedding_engine import clear_faiss_dimension_probe_cache

    ve_mod.VECTOR_INDEX_PATH = vec_index
    ve_mod.VECTOR_META_PATH = vec_meta
    ve_mod._index = None
    ve_mod._meta = None
    ve_mod._effective_dim = None
    clear_faiss_dimension_probe_cache()

    if request.node.get_closest_marker("legacy_sqlite_v2"):
        try:
            yield db_file
        finally:
            reset_info = reset_runtime_for_tests()
            assert_no_adapter_for_path(db_file)
            if reset_info["leftovers"]:
                raise RuntimeError(
                    "isolated_db teardown leftovers: " + "; ".join(reset_info["leftovers"])
                )
        return

    db_pkg.init_db()

    import aihub.agent_db as agent_db_mod

    agent_db_mod.ensure_schema()

    try:
        yield db_file
    finally:
        reset_info = reset_runtime_for_tests()
        assert_no_adapter_for_path(db_file)
        if reset_info["leftovers"]:
            raise RuntimeError(
                "isolated_db teardown leftovers: " + "; ".join(reset_info["leftovers"])
            )


@pytest.fixture(autouse=True)
def reset_strategy_confidence_bias_between_tests(isolated_db, request):
    """Clear strategy routing bias after DB is bound (avoids DELETE on empty pre-migrate DB)."""
    if request.node.get_closest_marker("no_isolated_db"):
        yield
        return
    from aihub.strategy_selector import reset_strategy_confidence_bias

    if not request.node.get_closest_marker("legacy_sqlite_v2"):
        reset_strategy_confidence_bias()
    yield
    if not request.node.get_closest_marker("legacy_sqlite_v2"):
        reset_strategy_confidence_bias()


@pytest.fixture
def client():
    """Return FastAPI test client with lifespan shutdown."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def ensure_legacy_event_loop_compatibility(monkeypatch):
    """Provide a current event loop for legacy tests using asyncio.get_event_loop().

    Some tests still call get_event_loop().run_until_complete(...), which raises
    RuntimeError on modern Python when no current loop is set in MainThread.
    """
    original_get_event_loop = asyncio.get_event_loop

    def _compat_get_event_loop():
        try:
            return original_get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    monkeypatch.setattr(asyncio, "get_event_loop", _compat_get_event_loop)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield
    finally:
        current = None
        try:
            current = original_get_event_loop()
        except RuntimeError:
            current = None

        if current is not None and not current.is_closed():
            current.close()

        if not loop.is_closed() and current is not loop:
            loop.close()
        asyncio.set_event_loop(None)
