#!/usr/bin/env python3
"""Deterministic micro-benchmarks for world-class runtime upgrades.

Measures local module paths (no live LLM). Prints JSON summary to stdout.
"""

from __future__ import annotations

import json
import statistics
import time
import uuid


def _timed(fn, n: int = 50) -> dict:
    samples: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {
        "n": n,
        "p50_ms": round(statistics.median(samples), 3),
        "p95_ms": round(sorted(samples)[max(0, int(0.95 * (n - 1)))], 3),
        "mean_ms": round(statistics.fmean(samples), 3),
    }


def main() -> int:
    from aihub.adaptive_learning.models import LongHorizonTask
    from aihub.adaptive_learning import store as learn_store
    from aihub.planner_engine import build_task_graph
    from aihub.turn.prompt_budget import (
        AGENTIC_BOUNDED_SYSTEM_PROMPT,
        build_agentic_bounded_system_prompt,
        estimate_tokens,
        select_prompt_budget,
    )

    uid = f"bench-{uuid.uuid4().hex[:8]}"
    marker = f"Profile26-{uuid.uuid4().hex[:8]}"
    task = LongHorizonTask(
        task_id=str(uuid.uuid4()),
        user_id=uid,
        session_id="sess-a",
        title=f"{marker}: migracja",
        objective="Migracja PG",
        pending_steps=["Backup", "Restore", "Verify"],
        next_best_action="Backup",
        status="active",
        confidence=0.9,
        created_at=time.time(),
        updated_at=time.time(),
    )
    learn_store.save_long_horizon_task(task)

    def lht_lookup():
        found = learn_store.get_active_long_horizon_task(user_id=uid, session_id="sess-b")
        assert found and found.task_id == task.task_id
        brief = learn_store.format_long_horizon_brief(found)
        assert "Backup" in brief

    def marker_lookup():
        assert learn_store.find_long_horizon_task_by_marker(user_id=uid, marker=marker)

    agentic = select_prompt_budget(
        user_text="Przygotuj plan migracji i niczego nie wykonuj.",
        selected_strategy="agentic",
        web_decision="off",
    )
    bounded = build_agentic_bounded_system_prompt(
        long_horizon_brief=learn_store.format_long_horizon_brief(task),
        planner_brief="1) Backup 2) Restore 3) Verify",
    )
    # Approximate legacy handbook floor used before bounded path (~2.5k+ tokens).
    legacy_floor = 2500
    bounded_tokens = estimate_tokens(bounded)
    core_only = estimate_tokens(AGENTIC_BOUNDED_SYSTEM_PROMPT)

    def planner_graph():
        result = build_task_graph(
            message="Migracja PostgreSQL Profile26 na nowy VPS bez downtime — napisz plan, niczego nie wykonuj.",
            user_id=uid,
        )
        assert result is not None and result.graph is not None

    def budget_select():
        select_prompt_budget(
            user_text="co to jest?",
            selected_strategy="instant",
            web_decision="off",
        )

    out = {
        "long_horizon_cross_session_lookup": _timed(lht_lookup, 80),
        "long_horizon_marker_lookup": _timed(marker_lookup, 80),
        "planner_graph": _timed(planner_graph, 25),
        "prompt_budget_select": _timed(budget_select, 200),
        "prompt_budget": {
            "profile": agentic.profile,
            "agentic_bounded_core_tokens": core_only,
            "agentic_bounded_system_tokens": bounded_tokens,
            "legacy_handbook_floor_tokens": legacy_floor,
            "token_reduction_pct_vs_legacy_floor": round(
                100.0 * (1.0 - bounded_tokens / legacy_floor), 1
            ),
        },
        "memory_ranking": "exact_marker=+0.55 correction_boost=+0.35 (unit-covered)",
        "replay": "unit suite in full pytest",
        "provider_routing_cache": "unit suite in full pytest",
        "reasoning": "agentic bounded + planner_brief injection (unit-covered)",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
