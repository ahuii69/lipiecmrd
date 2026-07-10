#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minimalny, czytelny skrót runtime w trace tur czatu (observability / produkt)."""

from __future__ import annotations

from typing import Any

from aihub.chat_decision_trace import ROUTE_RESEARCH_ANSWER


def attach_runtime_trace_summary(trace: dict[str, Any]) -> None:
    """Uzupełnia ``trace['runtime_trace_summary']`` bez zmiany logiki decyzji."""
    if not isinstance(trace, dict):
        return

    route = str(trace.get("selected_route") or "")
    web_used = route == ROUTE_RESEARCH_ANSWER or (
        bool(trace.get("controlled_web_triggered"))
        and trace.get("controlled_web_has_results") is True
    )
    memory_used = bool(trace.get("memory_lookup_happened")) or bool(
        trace.get("memory_used_bool")
    )
    trace["runtime_trace_summary"] = {
        "selected_strategy": trace.get("selected_strategy"),
        "web_used": web_used,
        "memory_used": memory_used,
        "fallback_used": bool(trace.get("used_fallback")),
    }
