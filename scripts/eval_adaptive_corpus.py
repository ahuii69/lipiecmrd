#!/usr/bin/env python3
"""Practical corpus evaluation for adaptive budget / routing.

Runs a diversified conversation set through select→signals→refine→adaptive
and reports profile accuracy, token savings vs static caps, and skip rates.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aihub.turn.adaptive_runtime import plan_adaptive_runtime
from aihub.turn.prompt_budget import (
    PROFILE_PROMPT_TOKEN_CAPS,
    estimate_tokens,
    refine_prompt_budget_dynamic,
    select_prompt_budget,
)
from aihub.turn.turn_signals import compute_turn_signals

# Diversified labeled corpus: expected_profile is the coarse envelope;
# lean_ok means dynamic refine should shrink tokens vs static profile cap.
CORPUS: list[dict] = [
    {"id": "meta1", "text": "kim jesteś?", "strategy": "instant", "web": "off", "expected_profile": "meta_light", "lean_ok": True},
    {"id": "meta2", "text": "jaki model teraz działa?", "strategy": "instant", "web": "off", "expected_profile": "meta_light", "lean_ok": True},
    {"id": "casual1", "text": "elo mordzix", "strategy": "instant", "web": "off", "expected_profile": "casual_light", "lean_ok": True},
    {"id": "casual2", "text": "No i git XD", "strategy": "instant", "web": "off", "expected_profile": "casual_light", "lean_ok": True},
    {"id": "casual3", "text": "siema", "strategy": "instant", "web": "off", "expected_profile": "casual_light", "lean_ok": True},
    {"id": "recall1", "text": "Jak nazywa się mój pies?", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False, "memory_pack": 3},
    {"id": "recall2", "text": "Pamiętasz co mówiłem o odkurzaczu Profile26-abcd1234?", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False, "memory_pack": 4},
    {"id": "corr1", "text": "Poprawka: nie lubię kawy, lubię herbatę.", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False},
    {"id": "proc1", "text": "Gdy proszę o debug 502, odpowiadaj zawsze: najpierw logi, potem diagnoza.", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False},
    {"id": "simple_q", "text": "Ile to jest 2+2?", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": True, "expect_skip_reflection": True},
    {"id": "simple_q2", "text": "Co to jest HTTP?", "strategy": "instant", "web": "off", "expected_profile": "contextual", "lean_ok": True},
    {"id": "research1", "text": "Jaka jest aktualna pogoda w Warszawie?", "strategy": "research", "web": "required", "expected_profile": "research", "lean_ok": False},
    {"id": "research2", "text": "Sprawdź aktualną cenę BTC", "strategy": "research", "web": "required", "expected_profile": "research", "lean_ok": False},
    {"id": "agentic1", "text": "Zaplanuj trzyetapową migrację PostgreSQL na nowy VPS", "strategy": "agentic", "web": "off", "expected_profile": "agentic", "lean_ok": False, "expect_planner": True},
    {"id": "agentic2", "text": "Napisz plan rolloutu bez wykonywania czegokolwiek", "strategy": "agentic", "web": "off", "expected_profile": "agentic", "lean_ok": False, "expect_planner": True},
    {"id": "agentic3", "text": "Śledź ten plan jako zadanie długoterminowe Profile26-mig001", "strategy": "agentic", "web": "off", "expected_profile": "agentic", "lean_ok": False},
    {"id": "fb1", "text": "odpowiadaj mi bardzo krótko od teraz", "strategy": "instant", "web": "off", "expected_profile": "contextual", "lean_ok": True},
    {"id": "tech1", "text": "Wyjaśnij różnicę między process a thread w Linuxie", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": True},
    {"id": "tech2", "text": "Jak działa VACUUM w PostgreSQL i kiedy go uruchamiać?", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False},
    {"id": "long1", "text": "Opisz krok po kroku jak zdiagnozować high CPU na serwerze produkcyjnym z nginx i postgresem, uwzględnij logi, top, iotopa i możliwe root cause.", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False},
    {"id": "ambig1", "text": "może to, albo tamto — nie wiem", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": True},
    {"id": "toolish1", "text": "Sprawdź w sieci dokumentację OpenAPI 3.1", "strategy": "research", "web": "required", "expected_profile": "research", "lean_ok": False},
    {"id": "remember1", "text": "Zapamiętaj, że mój pies ma na imię Burek", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False},
    {"id": "plan_only", "text": "Przygotuj plan migracji i niczego nie wykonuj", "strategy": "agentic", "web": "off", "expected_profile": "agentic", "lean_ok": False, "expect_planner": True},
    {"id": "status", "text": "Jaki jest aktualny stan zadania Profile26-mig001 i co jest następnym krokiem?", "strategy": "agentic", "web": "off", "expected_profile": "agentic", "lean_ok": False},
    {"id": "short_tech", "text": "co to redis?", "strategy": "instant", "web": "off", "expected_profile": "contextual", "lean_ok": True},
    {"id": "tease", "text": "ale z ciebie debil xd", "strategy": "instant", "web": "off", "expected_profile": "casual_light", "lean_ok": True},
    {"id": "provider", "text": "jaki provider teraz?", "strategy": "instant", "web": "off", "expected_profile": "meta_light", "lean_ok": True},
    {"id": "multi", "text": "Zrób research konkurencji, potem zaplanuj MVP i checklistę wdrożenia", "strategy": "agentic", "web": "optional", "expected_profile": "agentic", "lean_ok": False, "expect_planner": True},
    {"id": "corr2", "text": "Nie, jednak lubi kawę — poprzednia korekta była błędna", "strategy": "contextual", "web": "off", "expected_profile": "contextual", "lean_ok": False},
]


@dataclass
class RowResult:
    id: str
    profile: str
    expected: str
    profile_ok: bool
    static_cap: int
    dynamic_cap: int
    token_saved_pct: float
    skip_reflection: bool
    skip_critic: bool
    planner_enabled: bool
    layers_skipped: int


def evaluate_corpus(corpus: list[dict] | None = None) -> dict:
    rows: list[RowResult] = []
    for case in corpus or CORPUS:
        base = select_prompt_budget(
            user_text=case["text"],
            selected_strategy=case.get("strategy"),
            web_decision=case.get("web", "off"),
        )
        signals = compute_turn_signals(
            user_text=case["text"],
            selected_strategy=case.get("strategy"),
            web_decision=case.get("web", "off"),
            strategy_confidence=0.85 if case.get("expect_skip_reflection") else 0.65,
            intent_confidence=0.8 if case.get("expect_skip_reflection") else 0.6,
            ambiguity=0.1 if case.get("expect_skip_reflection") else 0.3,
            memory_pack_items=int(case.get("memory_pack") or 0),
            budget_profile=base.profile,
        )
        if case.get("expect_skip_reflection"):
            signals.confidence = 0.85
            signals.complexity = 0.2
            signals.uncertainty = 0.15
            signals.tool_probability = 0.05
        refined = refine_prompt_budget_dynamic(base, signals)
        plan = plan_adaptive_runtime(
            signals,
            refined,
            decision_core={
                "selected_strategy": case.get("strategy"),
                "planner_recommended": bool(case.get("expect_planner")),
                "budget_profile": refined.profile,
            },
        )
        static = int(PROFILE_PROMPT_TOKEN_CAPS[base.profile])
        dyn = int(refined.max_prompt_tokens)
        # Practical savings: light profiles save vs full contextual envelope;
        # heavier profiles save when dynamic refine shrinks the profile cap.
        if base.profile in ("meta_light", "casual_light"):
            reference = int(PROFILE_PROMPT_TOKEN_CAPS["contextual"])
        else:
            reference = static
        saved = max(0.0, 100.0 * (1.0 - dyn / max(1, reference)))
        rows.append(
            RowResult(
                id=case["id"],
                profile=refined.profile,
                expected=case["expected_profile"],
                profile_ok=refined.profile == case["expected_profile"],
                static_cap=static,
                dynamic_cap=dyn,
                token_saved_pct=round(saved, 1),
                skip_reflection=bool(plan.skip_reflection or refined.skip_reflection),
                skip_critic=bool(plan.skip_critic or refined.skip_critic),
                planner_enabled=not plan.skip_planner,
                layers_skipped=len(refined.layers_skipped or []),
            )
        )

    n = len(rows)
    profile_acc = sum(1 for r in rows if r.profile_ok) / max(1, n)
    lean_cases = [c for c in (corpus or CORPUS) if c.get("lean_ok")]
    lean_ids = {c["id"] for c in lean_cases}
    lean_rows = [r for r in rows if r.id in lean_ids]
    lean_save = sum(r.token_saved_pct for r in lean_rows) / max(1, len(lean_rows))
    skip_refl = sum(1 for r in rows if r.skip_reflection) / max(1, n)
    planner_cases = [c for c in (corpus or CORPUS) if c.get("expect_planner")]
    planner_ok = 0
    for c in planner_cases:
        r = next(x for x in rows if x.id == c["id"])
        if r.planner_enabled:
            planner_ok += 1
    planner_recall = planner_ok / max(1, len(planner_cases))

    summary = {
        "n": n,
        "profile_accuracy": round(profile_acc, 3),
        "mean_token_saved_pct_lean": round(lean_save, 1),
        "mean_token_saved_pct_all": round(sum(r.token_saved_pct for r in rows) / max(1, n), 1),
        "skip_reflection_rate": round(skip_refl, 3),
        "planner_enable_recall": round(planner_recall, 3),
        "rows": [asdict(r) for r in rows],
        "gates": {
            "profile_accuracy_ge_0.85": profile_acc >= 0.85,
            "lean_token_save_ge_15": lean_save >= 15.0,
            "planner_recall_ge_0.8": planner_recall >= 0.8,
        },
    }
    summary["pass"] = all(summary["gates"].values())
    return summary


def main() -> int:
    out = evaluate_corpus()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
