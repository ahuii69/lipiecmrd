#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
from typing import Any, Dict, List, Tuple

from aihub.db import append_event, exec_one, fetch_all, fetch_one, now_ts

logger = logging.getLogger(__name__)


class MetaMemory:
    """
    Meta Memory System - zarządzanie ważnością i relevancją informacji.

    Features:
    - Ranking ważności faktów na podstawie użytkowania
    - Tracking jak często fakt jest retrievowany
    - Adaptive importance scaling
    - Stale data detection (nieużywane przez długi czas)
    - Freshness scoring
    """

    def __init__(self):
        self.access_decay_rate = 0.02  # Decay per hour
        self.recency_weight = 0.3
        self.relevance_weight = 0.4
        self.usage_weight = 0.3

    def _init_tables(self) -> None:
        """Initialize meta memory tables if not exist."""
        try:
            sql = """
            CREATE TABLE IF NOT EXISTS memory_meta (
                fact_id TEXT PRIMARY KEY,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_access REAL NOT NULL DEFAULT 0,
                creation_ts REAL NOT NULL,
                usage_score REAL NOT NULL DEFAULT 0.5,
                importance_score REAL NOT NULL DEFAULT 0.5,
                relevance_score REAL NOT NULL DEFAULT 0.5,
                freshness_score REAL NOT NULL DEFAULT 0.5,
                overall_priority REAL NOT NULL DEFAULT 0.5,
                stale_warning INTEGER NOT NULL DEFAULT 0,
                archived INTEGER NOT NULL DEFAULT 0
            )
            """
            exec_one(sql)

            sql_idx = (
                "CREATE INDEX IF NOT EXISTS idx_meta_priority "
                "ON memory_meta(overall_priority DESC) WHERE archived=0"
            )
            exec_one(sql_idx)
            logger.debug("Meta memory tables initialized")
        except Exception as e:
            logger.warning(f"Error initializing meta memory tables: {e}")

    def track_access(self, fact_id: str) -> None:
        """
        Track że fakt został użyty/accessed.
        """
        try:
            now = now_ts()
            exec_one(
                """
            INSERT INTO memory_meta AS mm(fact_id, access_count, last_access, creation_ts, usage_score)
            VALUES(?,?,?,?,?)
            ON CONFLICT(fact_id) DO UPDATE SET
                access_count = mm.access_count + 1,
                last_access = excluded.last_access,
                usage_score = CASE
                    WHEN mm.usage_score + 0.05 >= 0.99 THEN 0.99
                    ELSE mm.usage_score + 0.05
                END
            """,
                (fact_id, 1, now, now, 0.55),
            )
            logger.debug(f"Tracked access for fact {fact_id}")
        except Exception as e:
            logger.debug(f"Error tracking access: {e}")

    def get_usage_score(self, fact_id: str) -> float:
        """
        Oblicz usage score na podstawie access pattern.
        """
        try:
            row = fetch_one(
                "SELECT access_count, last_access FROM memory_meta WHERE fact_id=?",
                (fact_id,),
            )
            if not row:
                return 0.5

            access_count = int(row["access_count"])
            last_access = float(row["last_access"])
            now = now_ts()

            # Score based on:
            # - How many times accessed (0.0-0.5)
            # - Recency of last access (0.0-0.5)
            access_score = min(0.5, access_count * 0.02)
            recency_score = min(0.5, 0.5 * (1.0 - (now - last_access) / (365 * 86400)))

            return access_score + recency_score
        except Exception as e:
            logger.debug(f"Error calculating usage score: {e}")
            return 0.5

    def get_freshness_score(self, fact_id: str) -> float:
        """
        Oblicz freshness score - jak świeży jest fakt.
        """
        try:
            row = fetch_one(
                "SELECT creation_ts, last_access FROM memory_meta WHERE fact_id=?",
                (fact_id,),
            )
            if not row:
                return 0.5

            creation_ts = float(row["creation_ts"])
            last_access = float(row["last_access"])
            now = now_ts()

            # Hours since creation
            hours_since_created = (now - creation_ts) / 3600.0
            # Hours since last access
            hours_since_access = (now - last_access) / 3600.0

            # Freshness decays over time
            freshness = max(0.0, 0.9 - (hours_since_created / 8760.0) * 0.5)

            # Boost if recently accessed
            if hours_since_access < 24:
                freshness = min(0.99, freshness + 0.1)

            return freshness
        except Exception as e:
            logger.debug(f"Error calculating freshness: {e}")
            return 0.5

    def check_stale(self, user_id: str, days_threshold: int = 60) -> List[str]:
        """
        Detect stale facts (not accessed in X days).
        """
        try:
            threshold_ts = now_ts() - (days_threshold * 86400)

            rows = fetch_all(
                """
            SELECT fact_id FROM memory_meta
            WHERE last_access < ? AND archived=0 AND stale_warning=0
            LIMIT 1000
            """,
                (threshold_ts,),
            )

            stale_ids = [r["fact_id"] for r in rows]

            # Mark as stale
            for fid in stale_ids:
                exec_one(
                    "UPDATE memory_meta SET stale_warning=1 WHERE fact_id=?",
                    (fid,),
                )

            if stale_ids:
                append_event(
                    user_id,
                    "meta_memory.stale_detected",
                    {"count": len(stale_ids), "threshold_days": days_threshold},
                )
                logger.info(f"Detected {len(stale_ids)} stale facts")

            return stale_ids
        except Exception as e:
            logger.error(f"Error checking stale facts: {e}")
            return []

    def compute_overall_priority(
        self, fact_id: str, importance: float, confidence: float
    ) -> float:
        """
        Compute comprehensive priority score combining:
        - Importance (from fact metadata)
        - Confidence (from extraction)
        - Usage score (how often used)
        - Relevance score (contextual relevance)
        - Freshness (age and access patterns)
        """
        try:
            usage = self.get_usage_score(fact_id)
            freshness = self.get_freshness_score(fact_id)

            # Weighted combination
            priority = (
                (importance * 0.3)
                + (confidence * 0.2)
                + (usage * self.usage_weight)
                + (freshness * self.recency_weight)
            )

            # Cap to [0, 1]
            priority = max(0.0, min(1.0, priority))

            # Update in DB
            exec_one(
                """
            UPDATE memory_meta
            SET usage_score=?, freshness_score=?, overall_priority=?
            WHERE fact_id=?
            """,
                (usage, freshness, priority, fact_id),
            )

            return priority
        except Exception as e:
            logger.debug(f"Error computing priority: {e}")
            return importance  # Fallback

    def rank_facts(self, user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get ranked facts by priority.
        """
        try:
            rows = fetch_all(
                """
            SELECT fact_id, access_count, last_access, overall_priority, stale_warning
            FROM memory_meta
            WHERE archived=0
            ORDER BY overall_priority DESC
            LIMIT ?
            """,
                (limit,),
            )

            results = []
            for r in rows:
                results.append(
                    {
                        "fact_id": r["fact_id"],
                        "access_count": int(r["access_count"]),
                        "last_access": float(r["last_access"]),
                        "priority": float(r["overall_priority"]),
                        "stale": bool(r["stale_warning"]),
                    }
                )

            return results
        except Exception as e:
            logger.error(f"Error ranking facts: {e}")
            return []

    def register_fact(self, fact_id: str, importance: float, confidence: float) -> None:
        """
        Register nowy fakt w meta memory.
        """
        try:
            now = now_ts()
            exec_one(
                """
            INSERT INTO memory_meta(
                fact_id, access_count, last_access, creation_ts,
                usage_score, importance_score, overall_priority
            )
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(fact_id) DO NOTHING
            """,
                (fact_id, 0, now, now, 0.5, importance, importance),
            )
            logger.debug(f"Registered fact {fact_id} in meta memory")
        except Exception as e:
            logger.debug(f"Error registering fact: {e}")

    def archive_fact(self, fact_id: str, reason: str = "") -> None:
        """
        Archive fakt (low priority, rarely used).
        """
        try:
            exec_one(
                "UPDATE memory_meta SET archived=1 WHERE fact_id=?",
                (fact_id,),
            )
            logger.debug(f"Archived fact {fact_id}: {reason}")
        except Exception as e:
            logger.debug(f"Error archiving fact: {e}")

    def get_top_facts(self, user_id: str, limit: int = 20) -> List[Tuple[str, float]]:
        """
        Get top priority facts for user.
        """
        try:
            rows = fetch_all(
                """
            SELECT fact_id, overall_priority
            FROM memory_meta
            WHERE archived=0
            ORDER BY overall_priority DESC
            LIMIT ?
            """,
                (limit,),
            )

            return [(r["fact_id"], float(r["overall_priority"])) for r in rows]
        except Exception as e:
            logger.error(f"Error getting top facts: {e}")
            return []

    def generate_report(self, user_id: str) -> Dict[str, Any]:
        """
        Generate meta memory report.
        """
        try:
            stats = fetch_one(
                """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN archived=0 THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN archived=1 THEN 1 ELSE 0 END) as archived_count,
                SUM(CASE WHEN stale_warning=1 THEN 1 ELSE 0 END) as stale,
                AVG(overall_priority) as avg_priority,
                AVG(access_count) as avg_usage,
                MAX(last_access) as last_access_ts
            FROM memory_meta
            """,
                (),
            )

            return {
                "total_tracked": int(stats["total"]) if stats else 0,
                "active_facts": int(stats["active"]) if stats else 0,
                "archived": int(stats["archived_count"]) if stats else 0,
                "stale_warnings": int(stats["stale"]) if stats else 0,
                "avg_priority": float(stats["avg_priority"])
                if stats and stats["avg_priority"]
                else 0.0,
                "avg_usage": float(stats["avg_usage"])
                if stats and stats["avg_usage"]
                else 0.0,
                "last_activity": float(stats["last_access_ts"])
                if stats and stats["last_access_ts"]
                else 0.0,
                "timestamp": now_ts(),
            }
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            return {
                "error": str(e),
                "timestamp": now_ts(),
            }


# Singleton
_meta_memory = MetaMemory()
_meta_memory._init_tables()


def track_access(fact_id: str) -> None:
    """Public API."""
    return _meta_memory.track_access(fact_id)


def touch_nodes(node_ids: List[str]) -> int:
    """Batch-update access_count and last_access for retrieved nodes.

    Returns number of nodes actually touched.
    """
    if not node_ids:
        return 0
    now = now_ts()
    touched = 0
    for nid in node_ids:
        exec_one(
            """
            INSERT INTO memory_meta AS mm(fact_id, access_count, last_access, creation_ts,
                                    usage_score, importance_score, overall_priority)
            VALUES(?, 1, ?, ?, 0.55, 0.5, 0.5)
            ON CONFLICT(fact_id) DO UPDATE SET
                access_count = mm.access_count + 1,
                last_access  = excluded.last_access,
                usage_score  = CASE
                    WHEN mm.usage_score + 0.03 >= 0.99 THEN 0.99
                    ELSE mm.usage_score + 0.03
                END,
                freshness_score = CASE
                    WHEN mm.freshness_score + 0.05 >= 0.99 THEN 0.99
                    ELSE mm.freshness_score + 0.05
                END
            """,
            (nid, now, now),
        )
        touched += 1
    return touched


def register_fact(fact_id: str, importance: float, confidence: float) -> None:
    """Public API."""
    return _meta_memory.register_fact(fact_id, importance, confidence)


def get_usage_score(fact_id: str) -> float:
    """Public API."""
    return _meta_memory.get_usage_score(fact_id)


def check_stale(user_id: str, days_threshold: int = 60) -> List[str]:
    """Public API."""
    return _meta_memory.check_stale(user_id, days_threshold)


def rank_facts(user_id: str, limit: int = 100) -> List[Dict[str, Any]]:
    """Public API."""
    return _meta_memory.rank_facts(user_id, limit)


def generate_report(user_id: str) -> Dict[str, Any]:
    """Public API."""
    return _meta_memory.generate_report(user_id)
