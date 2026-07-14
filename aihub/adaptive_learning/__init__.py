"""Adaptive Learning + Self-Model + Long-Horizon Intelligence."""

from aihub.adaptive_learning.engine import (
    apply_learning_influences_to_decision,
    learning_trace_fields,
    process_turn_learning,
)
from aihub.adaptive_learning.schema import ensure_adaptive_learning_schema

__all__ = [
    "ensure_adaptive_learning_schema",
    "process_turn_learning",
    "apply_learning_influences_to_decision",
    "learning_trace_fields",
]
