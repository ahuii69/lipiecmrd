"""Cross-process turn concurrency: advisory locks + session queue.

Async path uses:
- per-session ``asyncio.Lock`` (same event loop / process)
- DB advisory/lease acquire+release each in ``asyncio.to_thread``
  (never hold ``threading.Lock`` across thread boundaries)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import AsyncIterator, Iterator

from aihub.db import _DB_LOCK, _conn, _db_backend

log = logging.getLogger(__name__)

_LOCK_TABLE_READY = False
_LOCAL_SESSION_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_GUARD = threading.Lock()
_ASYNC_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_ASYNC_GUARD = threading.Lock()


def _lock_key(user_id: str, session_id: str) -> str:
    return f"{user_id}\0{session_id}"


def _pg_keys(user_id: str, session_id: str) -> tuple[int, int]:
    h1 = int(hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
    h2 = int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16) & 0x7FFFFFFF
    return h1, h2


def ensure_lock_schema() -> None:
    global _LOCK_TABLE_READY
    if _LOCK_TABLE_READY:
        return
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS turn_session_locks (
                lock_key TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                turn_id TEXT NOT NULL DEFAULT '',
                acquired_ts REAL NOT NULL,
                expires_ts REAL NOT NULL
            )
            """
        )
        con.commit()
    _LOCK_TABLE_READY = True


def _acquire_db_lock(
    *,
    user_id: str,
    session_id: str,
    turn_id: str,
    timeout_s: float,
    lease_s: float,
) -> tuple[str, str, bool, int, int]:
    """Blocking DB lock only (no threading.Lock). Safe to call via to_thread."""
    ensure_lock_schema()
    key = _lock_key(user_id, session_id)
    owner = f"{os.getpid()}:{threading.get_ident()}:{turn_id or 'na'}"
    deadline = time.time() + max(1.0, timeout_s)
    use_pg = _db_backend() == "postgres"
    k1 = k2 = 0

    if use_pg:
        k1, k2 = _pg_keys(user_id, session_id)
        while time.time() < deadline:
            with _DB_LOCK, _conn() as con:
                res = con.execute(
                    "SELECT pg_try_advisory_lock(?, ?) AS ok",
                    (k1, k2),
                ).fetchone()
                if res is None:
                    ok = False
                elif isinstance(res, dict):
                    ok = bool(res.get("ok"))
                else:
                    ok = bool(res[0])
                if ok:
                    return key, owner, True, k1, k2
            time.sleep(0.05)
        raise TimeoutError(
            f"session_turn_lock timeout user={user_id} session={session_id}"
        )

    while time.time() < deadline:
        now = time.time()
        with _DB_LOCK, _conn() as con:
            con.execute(
                "DELETE FROM turn_session_locks WHERE expires_ts < ?",
                (now,),
            )
            row = con.execute(
                "SELECT owner FROM turn_session_locks WHERE lock_key=?",
                (key,),
            ).fetchone()
            if row is None:
                con.execute(
                    """
                    INSERT INTO turn_session_locks(lock_key, owner, turn_id, acquired_ts, expires_ts)
                    VALUES(?,?,?,?,?)
                    """,
                    (key, owner, turn_id, now, now + lease_s),
                )
                con.commit()
                return key, owner, False, 0, 0
            con.commit()
        time.sleep(0.05)
    raise TimeoutError(
        f"session_turn_lock timeout user={user_id} session={session_id}"
    )


def _release_db_lock(
    *,
    key: str,
    owner: str,
    use_pg: bool,
    k1: int,
    k2: int,
) -> None:
    if use_pg:
        with _DB_LOCK, _conn() as con:
            con.execute("SELECT pg_advisory_unlock(?, ?)", (k1, k2))
            con.commit()
        return
    with _DB_LOCK, _conn() as con:
        con.execute(
            "DELETE FROM turn_session_locks WHERE lock_key=? AND owner=?",
            (key, owner),
        )
        con.commit()


def _local_threading_lock(key: str) -> threading.Lock:
    with _LOCAL_GUARD:
        return _LOCAL_SESSION_LOCKS.setdefault(key, threading.Lock())


def _async_lock_for(key: str) -> asyncio.Lock:
    with _ASYNC_GUARD:
        lock = _ASYNC_SESSION_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _ASYNC_SESSION_LOCKS[key] = lock
        return lock


@contextmanager
def session_turn_lock(
    *,
    user_id: str,
    session_id: str,
    turn_id: str = "",
    timeout_s: float = 30.0,
    lease_s: float = 180.0,
) -> Iterator[None]:
    """Sync context manager (tests / worker threads). Acquire+release on same thread."""
    key = _lock_key(user_id, session_id)
    local = _local_threading_lock(key)
    local.acquire()
    try:
        handle = _acquire_db_lock(
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            timeout_s=timeout_s,
            lease_s=lease_s,
        )
        try:
            yield
        finally:
            _release_db_lock(
                key=handle[0],
                owner=handle[1],
                use_pg=handle[2],
                k1=handle[3],
                k2=handle[4],
            )
    finally:
        local.release()


@asynccontextmanager
async def async_session_turn_lock(
    *,
    user_id: str,
    session_id: str,
    turn_id: str = "",
    timeout_s: float = 30.0,
    lease_s: float = 180.0,
) -> AsyncIterator[None]:
    """Async-safe session lock: asyncio.Lock + DB lock via to_thread."""
    key = _lock_key(user_id, session_id)
    async with _async_lock_for(key):
        handle = await asyncio.to_thread(
            _acquire_db_lock,
            user_id=user_id,
            session_id=session_id,
            turn_id=turn_id,
            timeout_s=timeout_s,
            lease_s=lease_s,
        )
        try:
            yield
        finally:
            await asyncio.to_thread(
                _release_db_lock,
                key=handle[0],
                owner=handle[1],
                use_pg=handle[2],
                k1=handle[3],
                k2=handle[4],
            )
