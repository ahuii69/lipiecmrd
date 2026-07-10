#!/usr/bin/env python3
"""Minimal E2E validation: strategia 'agentic' jest osiągalna przez ACTIVE path POST /chat/turn.

Cel: udowodnić, że po naprawie chat_runtime.py (odblokowanie active_goals_summary),
_pre_exec_decision_core() przekazuje realne dane z GoalEngine do select_strategy(),
co skutkuje selected_strategy == 'agentic' w trace odpowiedzi HTTP.

Metoda: ASGI transport (bez sieci), stub LLM provider (bez zewnętrznych wywołań),
realny GoalEngine, realny strategy_selector.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
os.chdir(_REPO_ROOT)

_API_KEY = os.environ.get("API_KEY", "")

TEST_USER = "e2e_agentic_val_001"
REGRESSION_USER = "e2e_regression_plain_002"


async def run() -> None:
    from unittest.mock import AsyncMock

    import httpx
    from httpx import ASGITransport

    from aihub.chat_contracts import ModelResponse
    from aihub.chat_runtime import get_chat_runtime
    from aihub.goal_engine import GoalCandidate, get_goal_engine
    from aihub.main import app

    # ── Stub LLM: zwraca natychmiastową odpowiedź, omijając zewnętrzne API ──
    stub_response = ModelResponse(
        provider="stub",
        model="stub-model",
        content="Rozumiem zadanie wieloetapowe. Zaczynam realizację pierwszego etapu.",
        finish_reason="stop",
    )
    runtime = get_chat_runtime()
    original_generate = runtime._provider.generate
    runtime._provider.generate = AsyncMock(return_value=stub_response)

    try:
        # ══════════════════════════════════════════════════════════════════════
        # KROK 1 — Przygotowanie stanu: aktywny cel z wysoką urgency
        # ══════════════════════════════════════════════════════════════════════
        engine = get_goal_engine()
        gc = GoalCandidate(
            user_id=TEST_USER,
            title="Wdrożenie systemu monitoringu produkcyjnego",
            description=(
                "Zbuduj i wdroż pełen system monitoringu w kilku krokach: "
                "konfiguracja agentów, testy integracyjne, deployment na produkcję."
            ),
            goal_type="long_term_goal",
            source="e2e_validation",
            urgency=0.9,
            priority=0.9,
        )
        goal = engine.create_goal(gc)
        goal = engine.activate_goal(TEST_USER, goal.goal_id)

        active = engine.get_active_goals(TEST_USER)
        assert len(active) >= 1, f"SETUP FAIL: brak aktywnych celów dla {TEST_USER}"
        max_urgency = max(g.urgency for g in active)
        print(
            f"[SETUP]  user={TEST_USER}  active_goals={len(active)}"
            f"  max_urgency={max_urgency:.2f}  goal_id={goal.goal_id}"
        )

        # ══════════════════════════════════════════════════════════════════════
        # KROK 2 — Runtime call: POST /chat/turn (ACTIVE path przez ASGI)
        # ══════════════════════════════════════════════════════════════════════
        headers = {"x-api-key": _API_KEY} if _API_KEY else {}
        payload_agentic = {
            "user_id": TEST_USER,
            "message": (
                "Zbuduj szczegółowy plan wdrożenia systemu monitoringu: "
                "podziel na etapy, przypisz priorytety, określ kolejność kroków "
                "i zacznij realizację pierwszego etapu konfiguracji."
            ),
            "mode": "agent",
            "session_id": "e2e_val_session_agentic",
        }
        print(f"[CALL]   POST /chat/turn  user={TEST_USER}  mode=agent")
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post(
                "/chat/turn", json=payload_agentic, headers=headers, timeout=30.0
            )

        # ══════════════════════════════════════════════════════════════════════
        # KROK 3 — Asercje: trace musi zawierać agentic
        # ══════════════════════════════════════════════════════════════════════
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text[:500]}"
        data = resp.json()
        trace = data.get("trace", {})
        selected_strategy = trace.get("selected_strategy")
        reason_codes = trace.get("reason_codes", [])
        writeback = trace.get("experience_write_back_attempted")
        used_fallback = trace.get("used_fallback")

        print(
            f"[TRACE]  selected_strategy={selected_strategy!r}"
            f"  reason_codes={reason_codes}"
        )
        print(
            f"[TRACE]  ok={data.get('ok')}  used_fallback={used_fallback}"
            f"  writeback_attempted={writeback}"
        )

        assert selected_strategy == "agentic", (
            f"FAIL: selected_strategy={selected_strategy!r}, expected='agentic'\n"
            f"reason_codes={reason_codes}\n"
            f"trace={trace}"
        )
        assert (
            "ACTIVE_GOAL_PRESENT" in reason_codes
        ), f"FAIL: ACTIVE_GOAL_PRESENT nie w reason_codes: {reason_codes}"
        print("[A1] ✓  selected_strategy == 'agentic'")
        print("[A2] ✓  ACTIVE_GOAL_PRESENT w reason_codes")
        print(f"[A3]    writeback_attempted={writeback}")

        # ── Cel B: selected_goal musi być w trace ──
        selected_goal = trace.get("selected_goal")
        assert (
            selected_goal is not None
        ), f"FAIL: trace['selected_goal'] jest None\ntrace={trace}"
        assert (
            "goal_id" in selected_goal and "title" in selected_goal
        ), f"FAIL: selected_goal niekompletny: {selected_goal}"
        print(
            f"[B1] ✓  selected_goal: id={str(selected_goal['goal_id'])[:12]}..."
            f"  urgency={selected_goal['urgency']:.2f}"
        )

        # ── Cel C: reflection na normalnej ścieżce (provider success) ──
        reflection_ran = trace.get("reflection_ran")
        assert reflection_ran is True, (
            f"FAIL: reflection_ran={reflection_ran!r} na normalnej ścieżce, expected=True\n"
            f"reflection_summary={trace.get('reflection_summary')!r}"
        )
        print("[C1] ✓  reflection_ran=True na normalnej ścieżce (provider success)")

        # ══════════════════════════════════════════════════════════════════════
        # KROK 4 — Fallback path: jeśli provider stub nie odpowie (tu nie trafią,
        #          ale trace decision_core jest weryfikowane w obu ścieżkach).
        #          Stub działa → normal path; decision_core zakodowane w trace ✓
        # ══════════════════════════════════════════════════════════════════════

        # ══════════════════════════════════════════════════════════════════════
        # KROK 3b — Regression: prosty user bez aktywnego celu → nie agentic
        # ══════════════════════════════════════════════════════════════════════
        payload_simple = {
            "user_id": REGRESSION_USER,
            "message": "Jaka jest stolica Francji?",
            "mode": "chat",
            "session_id": "e2e_regression_session",
        }
        print(
            f"\n[CALL]   POST /chat/turn  user={REGRESSION_USER}  mode=chat (regression)"
        )
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp2 = await client.post(
                "/chat/turn", json=payload_simple, headers=headers, timeout=30.0
            )

        assert (
            resp2.status_code == 200
        ), f"REGRESSION HTTP {resp2.status_code}: {resp2.text[:500]}"
        trace2 = resp2.json().get("trace", {})
        strat2 = trace2.get("selected_strategy")
        codes2 = trace2.get("reason_codes", [])

        assert (
            strat2 != "agentic"
        ), f"REGRESSION FAIL: prosty user bez celu dostał 'agentic'! strat2={strat2!r}"
        print(f"[R1] ✓  selected_strategy={strat2!r} (nie agentic)")
        print(f"[R2]    reason_codes={codes2}")

        # ══════════════════════════════════════════════════════════════════════
        # KROK 4 — Research strategy dla polskich naturalnych promptów (Cel A)
        # ══════════════════════════════════════════════════════════════════════
        research_prompts = [
            ("sprawdź aktualną cenę bitcoina", "sprawdź+aktualną"),
            ("wyszukaj mi przepis na pizzę margherita", "wyszukaj mi"),
            ("znajdź w internecie informacje o OpenAI", "znajdź+internet"),
        ]
        for prompt_text, label in research_prompts:
            payload_r = {
                "user_id": REGRESSION_USER,
                "message": prompt_text,
                "mode": "chat",
                "session_id": f"e2e_research_{abs(hash(prompt_text)) % 9999}",
            }
            print(f"\n[CALL]   research test: {label!r}")
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp_r = await client.post(
                    "/chat/turn", json=payload_r, headers=headers, timeout=30.0
                )
            assert (
                resp_r.status_code == 200
            ), f"RESEARCH [{label}] HTTP {resp_r.status_code}: {resp_r.text[:500]}"
            trace_r = resp_r.json().get("trace", {})
            strat_r = trace_r.get("selected_strategy")
            codes_r = trace_r.get("reason_codes", [])
            assert strat_r == "research", (
                f"FAIL [{label}]: selected_strategy={strat_r!r}, expected='research'\n"
                f"prompt={prompt_text!r}\nreason_codes={codes_r}"
            )
            print(f"[A_R] ✓  research dla: {label!r}  codes={codes_r}")

        print("\n=== E2E PASS ===")

    finally:
        runtime._provider.generate = original_generate


if __name__ == "__main__":
    asyncio.run(run())
