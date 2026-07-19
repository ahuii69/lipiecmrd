"""Test-only runtime reset and leak diagnostics."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EXPECTED_DAEMON_THREADS = frozenset(
    {
        "MainThread",
    }
)


def collect_runtime_diagnostics() -> dict[str, Any]:
    """Snapshot threads, FD usage, asyncio tasks and DB adapter path."""
    try:
        import resource

        fd_count = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss  # fallback metric
        try:
            fd_count = len(os.listdir("/proc/self/fd"))
        except OSError:
            pass
    except Exception:
        fd_count = None

    threads = [
        {"name": t.name, "daemon": t.daemon, "alive": t.is_alive()}
        for t in threading.enumerate()
    ]
    try:
        from aihub.config import DB_PATH

        db_path = str(DB_PATH)
    except Exception:
        db_path = os.getenv("DB_PATH", "")

    adapter_path = None
    adapter_parent_exists = None
    try:
        from aihub.db.runtime import active_sqlite_adapter_path

        ap = active_sqlite_adapter_path()
        if ap is not None:
            adapter_path = str(ap)
            adapter_parent_exists = ap.parent.exists()
    except Exception:
        pass

    pending_tasks = 0
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            pending_tasks = -1
        else:
            pending_tasks = len(asyncio.all_tasks(loop))
    except RuntimeError:
        pending_tasks = 0

    return {
        "thread_count": len(threads),
        "threads": threads,
        "fd_count": fd_count,
        "pending_asyncio_tasks": pending_tasks,
        "db_path": db_path,
        "adapter_path": adapter_path,
        "adapter_parent_exists": adapter_parent_exists,
    }


def _clear_singleton(module_name: str, attr: str) -> None:
    import importlib

    mod = importlib.import_module(module_name)
    setattr(mod, attr, None)


def reset_runtime_for_tests(*, fail_on_leftover: bool = False) -> dict[str, Any]:
    """Idempotently stop background work and drop cached runtime singletons."""
    leftovers: list[str] = []

    def _step(label: str, fn) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            msg = f"{label}: {exc}"
            leftovers.append(msg)
            logger.warning("reset_runtime_for_tests %s", msg, exc_info=True)

    _step("agent_worker.stop_worker", lambda: __import__("aihub.agent_worker", fromlist=["stop_worker"]).stop_worker())
    _step(
        "consolidation.stop_background",
        lambda: __import__(
            "aihub.workers.consolidation", fromlist=["stop_background"]
        ).stop_background(),
    )
    _step(
        "core.background.stop_background",
        lambda: __import__(
            "aihub.core.background", fromlist=["stop_background"]
        ).stop_background(),
    )

    def _cancel_asyncio_tasks() -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        if loop.is_running():
            return
        current = asyncio.current_task(loop=loop)
        for task in asyncio.all_tasks(loop):
            if task is current:
                continue
            task.cancel()
        if asyncio.all_tasks(loop):
            loop.run_until_complete(asyncio.sleep(0))

    _step("asyncio.cancel_pending", _cancel_asyncio_tasks)

    def _reset_embedding_and_vector() -> None:
        from aihub.embedding_engine import reset_providers

        reset_providers()
        import aihub.vector_engine as ve_mod

        ve_mod._index = None
        ve_mod._meta = None
        ve_mod._effective_dim = None
        ve_mod._backend = None

    _step("embedding_vector_reset", _reset_embedding_and_vector)

    def _reset_chat_runtime_cache() -> None:
        import aihub.chat_runtime as cr
        import aihub.turn.ops as tops
        import aihub.turn.idempotency as tidem
        import aihub.turn.concurrency as tconc

        cr._RUNTIME = None
        # get_turn_ops() delegates to get_chat_runtime(); no separate ops._RUNTIME.
        tops._TRACE_CACHE.clear()
        tidem._SCHEMA_READY = False
        tconc._LOCK_TABLE_READY = False

    _step("chat_runtime_reset", _reset_chat_runtime_cache)

    def _reset_canonical_cores() -> None:
        _clear_singleton("aihub.memory_core", "_core_singleton")
        _clear_singleton("aihub.psyche_core", "_psyche_core_singleton")
        _clear_singleton("aihub.user_vault", "_vault_singleton")

    _step("canonical_core_reset", _reset_canonical_cores)

    def _reset_knowledge_graph() -> None:
        import aihub.knowledge_graph as kg_mod

        kg_mod._graph.nodes.clear()
        kg_mod._graph.edges.clear()
        kg_mod._graph.relation_index.clear()

    _step("knowledge_graph_reset", _reset_knowledge_graph)

    def _dispose_db() -> None:
        from aihub.db import dispose_sqlite_engine

        dispose_sqlite_engine()

    _step("dispose_sqlite_engine", _dispose_db)

    diag = collect_runtime_diagnostics()
    if fail_on_leftover and leftovers:
        raise RuntimeError(
            "reset_runtime_for_tests left active resources: "
            + "; ".join(leftovers)
        )
    return {"leftovers": leftovers, "diagnostics": diag}


def assert_no_adapter_for_path(db_path: Path) -> None:
    """Fail teardown when SQLite adapter still references a temp DB path."""
    from aihub.db.runtime import SQLiteAdapter, _ADAPTER_HOLDER

    target = Path(db_path).resolve()
    for adapter in list(_ADAPTER_HOLDER):
        if isinstance(adapter, SQLiteAdapter) and Path(adapter.db_path).resolve() == target:
            raise RuntimeError(f"SQLite adapter still references removed path: {target}")


def assert_runtime_quiescent(
    *,
    baseline_threads: int | None = None,
    baseline_fds: int | None = None,
    fd_slack: int = 32,
    thread_slack: int = 4,
) -> None:
    """Raise when thread/FD counts grow beyond a stable baseline."""
    diag = collect_runtime_diagnostics()
    thread_count = int(diag["thread_count"])
    fd_count = diag.get("fd_count")
    if baseline_threads is not None and thread_count > baseline_threads + thread_slack:
        raise AssertionError(
            f"thread leak: {thread_count} > baseline {baseline_threads}+{thread_slack}; "
            f"threads={diag['threads']}"
        )
    if (
        baseline_fds is not None
        and isinstance(fd_count, int)
        and fd_count > baseline_fds + fd_slack
    ):
        raise AssertionError(
            f"FD leak: {fd_count} > baseline {baseline_fds}+{fd_slack}"
        )
