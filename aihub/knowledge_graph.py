#!/usr/bin/env python3

"""
Knowledge Graph - Reprezentacja wiedzy i relacji między faktami.

Odpowiada za:
- Mapowanie relacji między faktami
- Semantic reasoning
- Constraint propagation
- Query resolution
"""

import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from aihub.db import exec_one, fetch_all, now_ts

logger = logging.getLogger(__name__)


@dataclass
class KnowledgeNode:
    """Wierzchołek w grafie wiedzy."""

    node_id: str
    node_type: str  # "fact", "entity", "concept"
    content: str
    confidence: float  # 0.0-1.0
    source: str | None = None
    created_ts: float = field(default_factory=now_ts)
    updated_ts: float = field(default_factory=now_ts)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeEdge:
    """Krawędź w grafie wiedzy."""

    source_id: str
    target_id: str
    relation_type: str  # "implies", "contradicts", "refines", "related_to", "part_of"
    weight: float  # 0.0-1.0 (strength of relationship)
    created_ts: float = field(default_factory=now_ts)


class KnowledgeGraph:
    """
    Graf wiedzy dla reprezentacji i reasoningu.

    Przechowuje fakty jako wierzchołki i relacje między nimi as krawędzie.
    Umożliwia semantic queries i inference.
    """

    def __init__(self):
        self.nodes: dict[str, KnowledgeNode] = {}
        self.edges: list[KnowledgeEdge] = []
        self.relation_index: dict[str, list[KnowledgeEdge]] = {}

    def add_node(self, node: KnowledgeNode) -> None:
        """Add or update node."""
        try:
            self.nodes[node.node_id] = node
            logger.debug(f"Added node: {node.node_id} ({node.node_type})")
        except Exception as e:
            logger.error(f"Error adding node: {e}")

    def add_edge(self, edge: KnowledgeEdge) -> None:
        """Add relationship between nodes."""
        try:
            # Validate nodes exist
            if edge.source_id not in self.nodes or edge.target_id not in self.nodes:
                logger.debug(
                    "Cannot add edge: missing nodes %s -> %s",
                    edge.source_id,
                    edge.target_id,
                )
                return

            self.edges.append(edge)

            # Index by relation type
            if edge.relation_type not in self.relation_index:
                self.relation_index[edge.relation_type] = []
            self.relation_index[edge.relation_type].append(edge)

            logger.debug(
                f"Added edge: {edge.source_id} -{edge.relation_type}-> {edge.target_id}"
            )
        except Exception as e:
            logger.error(f"Error adding edge: {e}")

    def get_related_nodes(
        self, node_id: str, relation_type: str | None = None
    ) -> list[KnowledgeNode]:
        """Get all nodes related to given node."""
        try:
            related_ids: set[str] = set()

            for edge in self.edges:
                if edge.source_id == node_id:
                    if relation_type is None or edge.relation_type == relation_type:
                        related_ids.add(edge.target_id)
                elif edge.target_id == node_id:
                    if relation_type is None or edge.relation_type == relation_type:
                        related_ids.add(edge.source_id)

            return [self.nodes[nid] for nid in related_ids if nid in self.nodes]
        except Exception as e:
            logger.error(f"Error getting related nodes: {e}")
            return []

    def find_path(
        self, source_id: str, target_id: str, max_depth: int = 3
    ) -> list[str] | None:
        """Find path between two nodes (BFS)."""
        try:
            if source_id not in self.nodes or target_id not in self.nodes:
                return None

            from collections import deque

            queue: deque = deque([(source_id, [source_id])])
            visited: set[str] = {source_id}

            while queue:
                current, path = queue.popleft()

                if current == target_id:
                    return path

                if len(path) >= max_depth:
                    continue

                for edge in self.edges:
                    if edge.source_id == current:
                        next_id = edge.target_id
                    elif edge.target_id == current:
                        next_id = edge.source_id
                    else:
                        continue

                    if next_id not in visited:
                        visited.add(next_id)
                        queue.append((next_id, path + [next_id]))

            return None
        except Exception as e:
            logger.error(f"Error finding path: {e}")
            return None

    def detect_contradictions(self) -> list[tuple[str, str, str]]:
        """Detect contradictory facts."""
        try:
            contradictions: list[tuple[str, str, str]] = []

            # Look for "contradicts" edges
            contradict_edges = self.relation_index.get("contradicts", [])

            for edge in contradict_edges:
                source = self.nodes.get(edge.source_id)
                target = self.nodes.get(edge.target_id)

                if source and target and edge.weight > 0.7:
                    contradictions.append(
                        (
                            source.node_id,
                            target.node_id,
                            edge.relation_type,
                        )
                    )

            return contradictions
        except Exception as e:
            logger.error(f"Error detecting contradictions: {e}")
            return []

    def merge_nodes(self, node_id1: str, node_id2: str, keep_node_id: str) -> None:
        """Merge two nodes (for deduplication)."""
        try:
            if keep_node_id not in [node_id1, node_id2]:
                logger.warning("keep_node_id must be one of the nodes to merge")
                return

            remove_node_id = node_id2 if keep_node_id == node_id1 else node_id1

            # Update edges pointing to removed node
            for edge in self.edges:
                if edge.source_id == remove_node_id:
                    edge.source_id = keep_node_id
                if edge.target_id == remove_node_id:
                    edge.target_id = keep_node_id

            # Remove duplicate edges
            self.edges = list(
                {
                    (e.source_id, e.target_id, e.relation_type): e for e in self.edges
                }.values()
            )

            # Remove node
            if remove_node_id in self.nodes:
                del self.nodes[remove_node_id]

            logger.info(f"Merged nodes: {remove_node_id} -> {keep_node_id}")
        except Exception as e:
            logger.error(f"Error merging nodes: {e}")

    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "relation_types": len(self.relation_index),
            "contradictions": len(self.detect_contradictions()),
        }


# Singleton
_graph = KnowledgeGraph()


def add_node(node: KnowledgeNode) -> None:
    """Public API."""
    return _graph.add_node(node)


def add_edge(edge: KnowledgeEdge) -> None:
    """Public API."""
    return _graph.add_edge(edge)


def get_related_nodes(
    node_id: str, relation_type: str | None = None
) -> list[KnowledgeNode]:
    """Public API."""
    return _graph.get_related_nodes(node_id, relation_type)


def find_path(source_id: str, target_id: str, max_depth: int = 3) -> list[str] | None:
    """Public API."""
    return _graph.find_path(source_id, target_id, max_depth)


def detect_contradictions() -> list[tuple[str, str, str]]:
    """Public API."""
    return _graph.detect_contradictions()


def stats() -> dict[str, Any]:
    """Public API."""
    return _graph.stats()


def persist_node(
    node_id: str,
    node_type: str,
    content: str,
    user_id: str = "",
) -> None:
    """Persist knowledge node in SQLite table knowledge_nodes."""
    exec_one(
        """
        INSERT OR REPLACE INTO knowledge_nodes(id, node_type, content, user_id, created_ts)
        VALUES(?,?,?,?,?)
        """,
        (node_id, node_type, content, user_id or None, now_ts()),
    )


def persist_edge(
    edge_id: str,
    source: str,
    target: str,
    relation: str,
    weight: float,
) -> None:
    """Persist knowledge edge in SQLite table knowledge_edges."""
    exec_one(
        """
        INSERT OR REPLACE INTO knowledge_edges(id, source, target, relation, weight)
        VALUES(?,?,?,?,?)
        """,
        (edge_id, source, target, relation, weight),
    )


def query_nodes(query: str, limit: int = 10, user_id: str = "") -> list[KnowledgeNode]:
    """Search in-memory knowledge nodes by content (case-insensitive), optionally user-scoped."""
    if not str(user_id or "").strip():
        raise ValueError("user_id is required for knowledge_graph.query_nodes")
    q = (query or "").strip().lower()
    max_limit = max(1, min(limit, 200))

    # Best-effort bootstrap from DB when in-memory graph is empty.
    if not _graph.nodes:
        try:
            load_from_db()
        except (sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, OSError):
            logger.debug("query_nodes: load_from_db failed", exc_info=True)

    nodes = list(_graph.nodes.values())
    if not q:
        nodes.sort(
            key=lambda n: (float(n.confidence), float(n.updated_ts)), reverse=True
        )
        return nodes[:max_limit]

    q_tokens = [t for t in q.split() if t]
    scored: list[tuple[float, KnowledgeNode]] = []

    for node in nodes:
        node_user_id = str((node.metadata or {}).get("user_id") or "")
        if user_id and node_user_id != user_id:
            continue
        content = (node.content or "").lower()
        if not content:
            continue

        # lightweight ranking: full match > token coverage > confidence boost
        score = 0.0
        if q in content:
            score += 3.0
        if q_tokens:
            matched = len([t for t in q_tokens if t in content])
            score += float(matched) / float(len(q_tokens))
        score += float(node.confidence) * 0.25

        if score > 0.0:
            scored.append((score, node))

    scored.sort(key=lambda x: (x[0], x[1].confidence, x[1].updated_ts), reverse=True)
    return [node for _, node in scored[:max_limit]]


def load_from_db() -> None:
    """Load graph state from SQLite persistence tables."""
    try:
        node_rows = fetch_all(
            "SELECT id, node_type, content, user_id, created_ts FROM knowledge_nodes"
        )
        edge_rows = fetch_all(
            "SELECT id, source, target, relation, weight FROM knowledge_edges"
        )

        _graph.nodes.clear()
        _graph.edges.clear()
        _graph.relation_index.clear()

        for row in node_rows:
            user_id = row["user_id"] if row["user_id"] is not None else ""
            node = KnowledgeNode(
                node_id=row["id"],
                node_type=row["node_type"],
                content=row["content"],
                confidence=0.7,
                source="db",
                created_ts=float(row["created_ts"]),
                updated_ts=float(row["created_ts"]),
                metadata={"user_id": user_id},
            )
            _graph.add_node(node)

        for row in edge_rows:
            edge = KnowledgeEdge(
                source_id=row["source"],
                target_id=row["target"],
                relation_type=row["relation"],
                weight=float(row["weight"]),
            )
            _graph.add_edge(edge)
    except (sqlite3.Error, RuntimeError, ValueError, TypeError, KeyError, OSError):
        logger.debug("load_from_db failed", exc_info=True)
