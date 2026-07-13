#!/usr/bin/env python3
"""Lightweight turn pipeline load/concurrency smoke (no external LLM required)."""

from __future__ import annotations

import asyncio
import os
import resource
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# Force sqlite for local harness when run standalone
os.environ.setdefault("DB_BACKEND", "sqlite")


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def _fd_count() -> int:
    try:
        return len(os.listdir(f"/proc/{os.getpid()}/fd"))
    except Exception:
        return -1


def main() -> None:
    from aihub.chat_contracts import ChatTurnInput, ChatTurnResult, ModelResponse
    from aihub.turn.application import ChatTurnApplicationService
    from aihub.turn.idempotency import ensure_turn_schema
    from aihub.turn.concurrency import ensure_lock_schema

    ensure_turn_schema()
    ensure_lock_schema()
    run_id = f"load-{int(time.time())}"

    class FakeOps:
        _active_turn_ctx = None
        _active_trace_builder = None

        async def run_turn_core(self, turn: ChatTurnInput) -> ChatTurnResult:
            await asyncio.sleep(0.002)
            return ChatTurnResult(
                ok=True,
                response_text=f"echo:{turn.message[:40]}",
                model="fake",
                provider="fake",
                selected_mode="chat",
                trace={"path": "load"},
            )

    app = ChatTurnApplicationService(FakeOps())
    fd0, rss0, thr0 = _fd_count(), _rss_mb(), threading.active_count()

    async def one(i: int, user: str, session: str, key: str | None = None) -> float:
        t0 = time.perf_counter()
        await app.execute(
            ChatTurnInput(
                user_id=user,
                session_id=session,
                message=f"ping {i}",
                idempotency_key=key or f"{run_id}-{user}-{session}-{i}",
            )
        )
        return (time.perf_counter() - t0) * 1000.0

    async def run_batch() -> list[float]:
        # 200 sequential simple turns
        lat = []
        for i in range(200):
            lat.append(await one(i, "u_load", "s_seq"))
        # 100 parallel different users
        lat.extend(
            await asyncio.gather(
                *[one(i, f"u{i}", "s1") for i in range(100)]
            )
        )
        # same user/session — sequential (serialized by lock)
        for i in range(40):
            lat.append(await one(i, "u_same", "s_same"))
        # parallel same-session (must serialize via async lock)
        lat.extend(
            await asyncio.gather(
                *[one(i, "u_par", "s_par") for i in range(20)]
            )
        )
        # idempotent replay
        fixed = f"{run_id}-fixed-load-key"
        for _ in range(20):
            lat.append(await one(0, "u_idem", "s_idem", key=fixed))
        return lat

    lats = asyncio.run(run_batch())
    lats_sorted = sorted(lats)
    n = len(lats_sorted)
    p50 = lats_sorted[int(0.50 * (n - 1))]
    p95 = lats_sorted[int(0.95 * (n - 1))]
    p99 = lats_sorted[int(0.99 * (n - 1))]
    fd1, rss1, thr1 = _fd_count(), _rss_mb(), threading.active_count()

    print(
        {
            "turns": n,
            "p50_ms": round(p50, 2),
            "p95_ms": round(p95, 2),
            "p99_ms": round(p99, 2),
            "fd_before": fd0,
            "fd_after": fd1,
            "rss_mb_before": round(rss0, 1),
            "rss_mb_after": round(rss1, 1),
            "threads_before": thr0,
            "threads_after": thr1,
        }
    )
    assert fd1 < 0 or fd1 <= fd0 + 20, f"FD leak? {fd0}->{fd1}"
    assert thr1 <= thr0 + 8, f"thread leak? {thr0}->{thr1}"


if __name__ == "__main__":
    main()
