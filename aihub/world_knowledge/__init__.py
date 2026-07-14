"""World Knowledge: Evidence + Claims + KG + Execution Graph.

Canonical layer for provenance-aware facts. Bridges existing
`aihub.knowledge_graph` (legacy fact nodes) without replacing Memory V2,
planner, or agent runtimes.
"""

from aihub.world_knowledge.engine import (
    apply_knowledge_influences_to_decision,
    knowledge_trace_fields,
    process_turn_knowledge,
)
from aihub.world_knowledge.action_guard import apply_action_claim_guard
from aihub.world_knowledge.schema import ensure_world_knowledge_schema

__all__ = [
    "ensure_world_knowledge_schema",
    "process_turn_knowledge",
    "apply_knowledge_influences_to_decision",
    "knowledge_trace_fields",
    "apply_action_claim_guard",
]
