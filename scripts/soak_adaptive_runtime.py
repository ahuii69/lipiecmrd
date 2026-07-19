#!/usr/bin/env python3
"""Compressed soak / load harness for adaptive intelligence.

Default: ~2–5 minutes of parallel decision loops (no live LLM).
Optional long mode: AIHUB_SOAK_MINUTES=60 for multi-hour soak.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aihub.turn.adaptive_runtime import plan_adaptive_runtime
from aihub.turn.continuous_self_eval import evaluate_continuous_self
from aihub.turn.prompt_budget import refine_prompt_budget_dynamic, select_prompt_budget
from aihub.turn.turn_signals import compute_turn_signals

TEXTS = [
    "kim jesteś?",
    "elo mordzix",
    "Jak nazywa się mój pies?",
    "Zaplanuj trzyetapową migrację PostgreSQL",
    "aktualna pogoda w Warszawie",
    "Ile to jest 2+2?",
    "Poprawka: nie lubię kawy",
    "Sprawdź dokumentację OpenAPI",
    "Pamiętasz Profile26-abcd1234?",
    "odpowiadaj krótko od teraz",
]


def _one(i: int) -> dict:
    text = TEXTS[i % len(TEXTS)]
    strat = ["instant", "contextual", "research", "agentic"][i % 4]
    web = "required" if strat == "research" else "off"
    t0 = time.perf_counter()
    base = select_prompt_budget(user_text=text, selected_strategy=strat, web_decision=web)
    signals = compute_turn_signals(
        user_text=text,
        selected_strategy=strat,
        web_decision=web,
        strategy_confidence=0.55 + (i % 6) * 0.07,
        budget_profile=base.profile,
        memory_pack_items=i % 5,
    )
    refined = refine_prompt_budget_dynamic(base, signals)
    plan = plan_adaptive_runtime(signals, refined, decision_core={"selected_strategy": strat})
    cse = evaluate_continuous_self(
        message=text,
        response_text="ok " * ((i % 20) + 1),
        trace={
            "budget_profile": refined.profile,
            "strategy_confidence": signals.confidence,
            "response_grounding_mode": "tools_verified" if i % 7 == 0 else "model",
            "usage_total_tokens": 200 + (i % 50) * 10,
        },
        decision_core={"strategy_confidence": signals.confidence, "budget_profile": refined.profile},
        ok=True,
    )
    dt = (time.perf_counter() - t0) * 1000.0
    return {
        "ms": dt,
        "profile": refined.profile,
        "tokens": refined.max_prompt_tokens,
        "skip_reflection": plan.skip_reflection,
        "quality": cse.overall_quality,
    }


def run_soak(*, minutes: float, workers: int = 12) -> dict:
    deadline = time.time() + minutes * 60.0
    results: list[dict] = []
    errors = 0
    i = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        while time.time() < deadline:
            batch = list(range(i, i + workers * 4))
            i += len(batch)
            futs = [pool.submit(_one, n) for n in batch]
            for f in concurrent.futures.as_completed(futs):
                try:
                    results.append(f.result())
                except Exception:
                    errors += 1
    ms = [r["ms"] for r in results]
    return {
        "minutes": minutes,
        "iterations": len(results),
        "errors": errors,
        "throughput_per_s": round(len(results) / max(0.001, minutes * 60.0), 2),
        "latency_p50_ms": round(statistics.median(ms), 3) if ms else None,
        "latency_p95_ms": round(sorted(ms)[int(0.95 * (len(ms) - 1))], 3) if ms else None,
        "profiles": sorted({r["profile"] for r in results}),
        "mean_token_cap": round(statistics.fmean(r["tokens"] for r in results), 1) if results else None,
        "skip_reflection_rate": round(
            sum(1 for r in results if r["skip_reflection"]) / max(1, len(results)), 3
        ),
        "pass": errors == 0 and len(results) > 0,
    }


def main() -> int:
    minutes = float(os.environ.get("AIHUB_SOAK_MINUTES", "0.15"))
    workers = int(os.environ.get("AIHUB_SOAK_WORKERS", "12"))
    out = run_soak(minutes=minutes, workers=workers)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
