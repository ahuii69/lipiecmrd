"""Shared fixtures for AI-Hub tests."""

import asyncio
import inspect
import os

# These must be set before importing aihub.main/agent_worker, because worker
# autostart flags are read at module import time.  Tests must never inherit
# production background workers or production Postgres from a real .env.
os.environ.setdefault("AGENT_AUTOSTART", "0")
os.environ.setdefault("AIHUB_BACKGROUND_AGENT_LOOP_ENABLED", "0")
os.environ.setdefault("DB_BACKEND", "sqlite")
os.environ.setdefault("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
os.environ.setdefault("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
os.environ.setdefault("AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK", "1")

import pytest
from fastapi.testclient import TestClient

from aihub.auth_patch import HUB_KEY_ENV_NAMES
from aihub.logs import setup_test_logging
from aihub.main import app

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
def isolated_db(tmp_path, monkeypatch, request):
    """Use a temporary SQLite DB per test; teardown checkpoints WAL and drops adapter cache.

    Do **not** ``importlib.reload(aihub.db)``: the package re-executing would bind a second
    copy of the legacy runtime while ``memory_v2_repository`` etc. keep the first ``fetch_one``.
    """
    import importlib

    import aihub.config as cfg
    import aihub.db as db_pkg

    db_file = tmp_path / "test_aihub.sqlite3"
    # Tests are hermetic and must not inherit a production DB_BACKEND=postgres from .env.
    # Production startup is guarded by scripts/doctor.py; unit/integration tests use SQLite.
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_file))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FS_ROOT", str(tmp_path / "fs"))
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    vec_index = tmp_path / "vector.index"
    vec_meta = tmp_path / "vector_meta.json"
    monkeypatch.setenv("VECTOR_INDEX_PATH", str(vec_index))
    monkeypatch.setenv("VECTOR_META_PATH", str(vec_meta))
    for name in HUB_KEY_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("API_KEY", "")

    importlib.reload(cfg)
    monkeypatch.setattr(cfg, "DB_PATH", db_file)
    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)

    import aihub.vector_engine as ve_mod
    from aihub.embedding_engine import clear_faiss_dimension_probe_cache

    ve_mod.VECTOR_INDEX_PATH = vec_index
    ve_mod.VECTOR_META_PATH = vec_meta
    ve_mod._index = None
    ve_mod._meta = None
    ve_mod._effective_dim = None
    clear_faiss_dimension_probe_cache()

    def _teardown_db() -> None:
        try:
            db_pkg.dispose_sqlite_engine()
        except Exception:
            pass

    _teardown_db()

    if request.node.get_closest_marker("legacy_sqlite_v2"):
        try:
            yield db_file
        finally:
            _teardown_db()
        return

    db_pkg.init_db()

    import aihub.agent_db as agent_db_mod

    agent_db_mod.ensure_schema()

    import aihub.knowledge_graph as kg_mod

    kg_mod._graph.nodes.clear()
    kg_mod._graph.edges.clear()
    kg_mod._graph.relation_index.clear()

    try:
        yield db_file
    finally:
        _teardown_db()


@pytest.fixture(autouse=True)
def reset_strategy_confidence_bias_between_tests(isolated_db, request):
    """Clear strategy routing bias after DB is bound (avoids DELETE on empty pre-migrate DB)."""
    from aihub.strategy_selector import reset_strategy_confidence_bias

    if not request.node.get_closest_marker("legacy_sqlite_v2"):
        reset_strategy_confidence_bias()
    yield
    if not request.node.get_closest_marker("legacy_sqlite_v2"):
        reset_strategy_confidence_bias()


@pytest.fixture
def client():
    """Return FastAPI test client."""
    return TestClient(app)


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
