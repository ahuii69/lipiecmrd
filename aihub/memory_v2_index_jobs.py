#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable indexing outbox for Memory V2 vector indexing.

Memory persistence must not depend on an embedding/vector provider being alive in
that exact request. This module records index work and lets startup, doctor or a
manual reindex script retry pending/failed items without losing state.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from aihub.db import exec_one, fetch_all, fetch_one, now_ts
from aihub.memory_v2_repository import get_memory_item, update_memory_item

logger = logging.getLogger(__name__)

_STATUSES = {"pending", "indexed", "failed", "stale"}


def ensure_index_jobs_table() -> None:
    exec_one(
        """
        CREATE TABLE IF NOT EXISTS memory_v2_index_jobs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            vector_ref TEXT,
            next_attempt_ts REAL NOT NULL DEFAULT 0,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        )
        """,
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_memv2_index_jobs_status_next ON memory_v2_index_jobs(status, next_attempt_ts, updated_ts)",
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_memv2_index_jobs_user_memory ON memory_v2_index_jobs(user_id, memory_id)",
    )


def record_index_job(
    *,
    user_id: str,
    memory_id: str,
    status: str,
    vector_ref: str | None = None,
    error: str | None = None,
) -> str:
    if status not in _STATUSES:
        raise ValueError(f"invalid memory index job status: {status}")
    ensure_index_jobs_table()
    now = float(now_ts())
    existing = fetch_one(
        "SELECT id, attempts FROM memory_v2_index_jobs WHERE user_id=? AND memory_id=? ORDER BY created_ts DESC LIMIT 1",
        (user_id, memory_id),
    )
    attempts = int(existing["attempts"] if existing else 0)
    if status == "failed":
        attempts += 1
    next_attempt_ts = 0.0 if status in {"pending", "indexed"} else now + min(3600.0, 30.0 * max(1, attempts))
    if existing:
        job_id = str(existing["id"])
        exec_one(
            """
            UPDATE memory_v2_index_jobs
            SET status=?, attempts=?, last_error=?, vector_ref=?, next_attempt_ts=?, updated_ts=?
            WHERE id=?
            """,
            (status, attempts, (error or "")[:1000], vector_ref, next_attempt_ts, now, job_id),
        )
        return job_id
    job_id = f"memidx-{uuid.uuid4()}"
    exec_one(
        """
        INSERT INTO memory_v2_index_jobs(
            id, user_id, memory_id, status, attempts, last_error, vector_ref,
            next_attempt_ts, created_ts, updated_ts
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (job_id, user_id, memory_id, status, attempts, (error or "")[:1000], vector_ref, next_attempt_ts, now, now),
    )
    return job_id


def enqueue_index_job(user_id: str, memory_id: str, *, reason: str = "pending") -> str:
    return record_index_job(user_id=user_id, memory_id=memory_id, status="pending", error=reason)


def list_index_jobs(*, status: str | None = None, user_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    ensure_index_jobs_table()
    conditions: list[str] = []
    params: list[Any] = []
    if status:
        conditions.append("status=?")
        params.append(status)
    if user_id:
        conditions.append("user_id=?")
        params.append(user_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = fetch_all(
        f"SELECT * FROM memory_v2_index_jobs{where} ORDER BY updated_ts DESC LIMIT ?",
        tuple([*params, max(1, int(limit or 100))]),
    )
    return [dict(row) for row in rows]


def index_job_summary(user_id: str | None = None) -> dict[str, Any]:
    ensure_index_jobs_table()
    params: tuple[Any, ...] = (user_id,) if user_id else ()
    where = "WHERE user_id=?" if user_id else ""
    rows = fetch_all(
        f"SELECT status, COUNT(*) AS n FROM memory_v2_index_jobs {where} GROUP BY status",
        params,
    )
    counts = {str(row["status"]): int(row["n"]) for row in rows}
    for st in _STATUSES:
        counts.setdefault(st, 0)
    return {"user_id": user_id, "counts": counts, "total": sum(counts.values())}


def _due_jobs(user_id: str | None, limit: int) -> list[dict[str, Any]]:
    ensure_index_jobs_table()
    now = time.time()
    conditions = ["status IN ('pending','failed','stale')", "next_attempt_ts <= ?"]
    params: list[Any] = [now]
    if user_id:
        conditions.append("user_id=?")
        params.append(user_id)
    rows = fetch_all(
        f"""
        SELECT * FROM memory_v2_index_jobs
        WHERE {' AND '.join(conditions)}
        ORDER BY updated_ts ASC
        LIMIT ?
        """,
        tuple([*params, max(1, int(limit or 50))]),
    )
    return [dict(row) for row in rows]


def process_index_jobs(*, user_id: str | None = None, limit: int = 50) -> dict[str, Any]:
    """Retry due Memory V2 vector indexing jobs and return a concrete report."""
    from aihub.memory_v2_hybrid import index_memory_item

    jobs = _due_jobs(user_id, limit)
    indexed = 0
    failed = 0
    missing = 0
    details: list[dict[str, Any]] = []
    for job in jobs:
        mid = str(job["memory_id"])
        uid = str(job["user_id"])
        item = get_memory_item(mid, uid)
        if item is None:
            missing += 1
            record_index_job(user_id=uid, memory_id=mid, status="failed", error="memory item not found")
            details.append({"memory_id": mid, "status": "missing"})
            continue
        try:
            ref = index_memory_item(item)
            if ref:
                item.embedding_vector_ref = ref
                item.updated_ts = now_ts()
                update_memory_item(item)
                record_index_job(user_id=uid, memory_id=mid, status="indexed", vector_ref=ref)
                indexed += 1
                details.append({"memory_id": mid, "status": "indexed", "vector_ref": ref})
            else:
                failed += 1
                record_index_job(user_id=uid, memory_id=mid, status="failed", error="index_memory_item returned empty ref")
                details.append({"memory_id": mid, "status": "failed", "error": "empty vector ref"})
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("memory_v2_index_job_failed memory_id=%s", mid, exc_info=True)
            record_index_job(user_id=uid, memory_id=mid, status="failed", error=str(exc))
            details.append({"memory_id": mid, "status": "failed", "error": str(exc)[:300]})
    return {"ok": failed == 0 and missing == 0, "processed": len(jobs), "indexed": indexed, "failed": failed, "missing": missing, "details": details}


def enqueue_unindexed_items(*, user_id: str | None = None, limit: int = 1000) -> dict[str, Any]:
    ensure_index_jobs_table()
    conditions = ["is_archived=0", "(embedding_vector_ref IS NULL OR embedding_vector_ref='')"]
    params: list[Any] = []
    if user_id:
        conditions.append("user_id=?")
        params.append(user_id)
    rows = fetch_all(
        f"SELECT id, user_id FROM memory_v2_items WHERE {' AND '.join(conditions)} ORDER BY updated_ts DESC LIMIT ?",
        tuple([*params, max(1, int(limit or 1000))]),
    )
    count = 0
    for row in rows:
        enqueue_index_job(str(row["user_id"]), str(row["id"]), reason="missing embedding_vector_ref")
        count += 1
    return {"ok": True, "enqueued": count, "user_id": user_id}
