"""Minimal runtime trace summary (no full app import)."""

from aihub.chat_decision_trace import ROUTE_RESEARCH_ANSWER
from aihub.ops_trace import attach_runtime_trace_summary


def test_runtime_trace_summary_web_and_memory():
    tr: dict = {
        "selected_strategy": "research",
        "selected_route": ROUTE_RESEARCH_ANSWER,
        "memory_lookup_happened": True,
        "used_fallback": False,
        "controlled_web_triggered": False,
    }
    attach_runtime_trace_summary(tr)
    s = tr["runtime_trace_summary"]
    assert s["selected_strategy"] == "research"
    assert s["web_used"] is True
    assert s["memory_used"] is True
    assert s["fallback_used"] is False


def test_runtime_trace_summary_controlled_web_results():
    tr: dict = {
        "selected_strategy": "contextual",
        "selected_route": "contextual_answer",  # ROUTE_CONTEXTUAL_ANSWER
        "controlled_web_triggered": True,
        "controlled_web_has_results": True,
        "memory_used_bool": False,
        "memory_lookup_happened": False,
        "used_fallback": True,
    }
    attach_runtime_trace_summary(tr)
    s = tr["runtime_trace_summary"]
    assert s["web_used"] is True
    assert s["memory_used"] is False
    assert s["fallback_used"] is True
