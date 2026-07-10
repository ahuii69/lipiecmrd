#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Structured strategy / escalation payload for experience ingest metadata."""

from __future__ import annotations

from typing import Any


def latency_bucket_from_ms(duration_ms: float) -> str:
    """Deterministic coarse buckets for experience analytics."""
    if duration_ms < 200.0:
        return "<200ms"
    if duration_ms < 800.0:
        return "200-800ms"
    if duration_ms < 2000.0:
        return "800ms-2s"
    return ">2s"


def build_strategy_experience_record(
    *,
    user_input_summary: str,
    selected_strategy: str,
    final_mode: str,
    success: bool,
    latency_bucket: str,
    used_tools: bool,
    fallback_used: bool,
    reflection_hint: str,
) -> dict[str, Any]:
    """Single record merged into ingest metadata (memory write-back)."""
    text = (user_input_summary or "").strip()
    if len(text) > 500:
        text = text[:497] + "..."
    return {
        "user_input_summary": text,
        "selected_strategy": str(selected_strategy or ""),
        "final_mode": str(final_mode or ""),
        "success": bool(success),
        "latency_bucket": str(latency_bucket or ""),
        "used_tools": bool(used_tools),
        "fallback_used": bool(fallback_used),
        "reflection_hint": str(reflection_hint or "")[:400],
    }


def merge_strategy_experience_into_metadata(
    metadata: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    """Attach strategy experience block without breaking existing keys."""
    out = dict(metadata)
    out["strategy_experience"] = dict(record)
    return out
