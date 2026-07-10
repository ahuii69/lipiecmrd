"""Inflection Fix V1 — before/after evidence."""
from aihub.executive_controller import _build_agent_response_text, _pl

CASES = [
    # krok: 1 / 2 / 5
    {"label": "krok singular (1)", "mode": "run", "strategy": "planned_reasoning",
     "selected_goal": {"title": "Cel A", "progress": 0.1},
     "active_goals_summary": [], "execution_result": {"payload": {"steps_executed": 1, "timed_out": False}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "krok plural-few (2)", "mode": "run", "strategy": "planned_reasoning",
     "selected_goal": {"title": "Cel B", "progress": 0.2},
     "active_goals_summary": [], "execution_result": {"payload": {"steps_executed": 2, "timed_out": False}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "krok plural-many (5)", "mode": "run", "strategy": "planned_reasoning",
     "selected_goal": {"title": "Cel C", "progress": 0.5},
     "active_goals_summary": [], "execution_result": {"payload": {"steps_executed": 5, "timed_out": False}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},

    # sygnał: 1 / 3 / 7
    {"label": "sygnał singular (1)", "mode": "tick", "strategy": "reactive",
     "selected_goal": None, "active_goals_summary": [],
     "execution_result": {"payload": {"processed": 1, "enqueued": 0, "ran": 0}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "sygnał plural-few (3)", "mode": "tick", "strategy": "reactive",
     "selected_goal": None, "active_goals_summary": [],
     "execution_result": {"payload": {"processed": 3, "enqueued": 0, "ran": 0}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "sygnał plural-many (7)", "mode": "tick", "strategy": "reactive",
     "selected_goal": None, "active_goals_summary": [],
     "execution_result": {"payload": {"processed": 7, "enqueued": 0, "ran": 0}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},

    # zadanie: 1 / 2 / 5
    {"label": "zadanie singular (1)", "mode": "tick", "strategy": "reactive",
     "selected_goal": None, "active_goals_summary": [],
     "execution_result": {"payload": {"processed": 0, "enqueued": 0, "ran": 1}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "zadanie plural-few (2)", "mode": "tick", "strategy": "reactive",
     "selected_goal": None, "active_goals_summary": [],
     "execution_result": {"payload": {"processed": 0, "enqueued": 0, "ran": 2}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "zadanie plural-many (5)", "mode": "tick", "strategy": "reactive",
     "selected_goal": None, "active_goals_summary": [],
     "execution_result": {"payload": {"processed": 0, "enqueued": 0, "ran": 5}, "errors": []},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},

    # błąd (instrumental): 1 / 2 / 5
    {"label": "błąd 1 → błędem", "mode": "run", "strategy": "planned_reasoning",
     "selected_goal": {"title": "Cel X", "progress": 0.0},
     "active_goals_summary": [], "execution_result": {"payload": {}, "errors": [{"error": "db err"}]},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "błąd 2 → błędami", "mode": "run", "strategy": "planned_reasoning",
     "selected_goal": {"title": "Cel X", "progress": 0.0},
     "active_goals_summary": [], "execution_result": {"payload": {}, "errors": [{"error": "e1"}, {"error": "e2"}]},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
    {"label": "błąd 5 → błędami", "mode": "run", "strategy": "planned_reasoning",
     "selected_goal": {"title": "Cel X", "progress": 0.0},
     "active_goals_summary": [], "execution_result": {"payload": {}, "errors": [{"error": f"e{i}"} for i in range(5)]},
     "reflection": {"memory_hits": {}}, "goal_progress_changed": False, "ok": True},
]

BEFORE = {
    "krok singular (1)": "1 kroków",
    "krok plural-few (2)": "2 kroków",
    "krok plural-many (5)": "5 kroków",
    "sygnał singular (1)": "1 sygnałów",
    "sygnał plural-few (3)": "3 sygnałów",
    "sygnał plural-many (7)": "7 sygnałów",
    "zadanie singular (1)": "1 zadań",
    "zadanie plural-few (2)": "2 zadań",
    "zadanie plural-many (5)": "5 zadań",
    "błąd 1 → błędem": "1 błędem/-ami",
    "błąd 2 → błędami": "2 błędem/-ami",
    "błąd 5 → błędami": "5 błędem/-ami",
}

print("=== Inflection Fix V1 — PRZED / PO ===\n")
for c in CASES:
    lbl = c["label"]
    text = _build_agent_response_text(c)
    before = BEFORE.get(lbl, "—")
    print(f"  {lbl}")
    print(f"    PRZED: '{before}'")
    print(f"    PO:    '{text}'")
    print()

# Sanity assertions
assert "/-ami" not in " ".join(_build_agent_response_text(c) for c in CASES), "/-ami still present!"
print("[PASS] Brak nienaturalnych form /-ami w żadnej odpowiedzi.")
print("[PASS] Smoke zakończony sukcesem.")
