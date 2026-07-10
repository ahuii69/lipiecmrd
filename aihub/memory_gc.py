#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Memory Garbage Collector - System czyszczenia i optymalizacji pamięci.

Odpowiada za:
- Usuwanie starej pamięci
- Kompresję wiedzy
- Archiwizację faktów
- Optymalizację zasobów
"""

import logging
import sqlite3
from typing import Any, Dict, Optional

from aihub.db import append_event, exec_one, fetch_all, fetch_one, now_ts
from aihub.meta_memory import check_stale

logger = logging.getLogger(__name__)


class MemoryGC:
    """
    Memory Garbage Collector i optimizer.

    Zarządza cyklem życia pamięci, usuwa stare dane,
    archiwizuje i optymalizuje strukturę.
    """

    def __init__(self):
        self._knowledge_evolution_obj: Optional[Any] = None
        self._knowledge_evolution_tried: bool = False

        # Konfiguracja polityki
        self.gc_policies = {
            "stale_threshold_days": 90,  # Delete after 90 days of not using
            "archive_threshold_days": 30,  # Archive after 30 days
            "max_facts_per_user": 5000,  # Max facts in memory
            "compress_above_count": 2000,  # Compress when above 2000 facts
        }

    def collect_garbage(self, user_id: str) -> Dict[str, Any]:
        """
        Run garbage collection cycle.

        Args:
            user_id: User ID

        Returns:
            Stats on cleanup operations
        """
        try:
            logger.info("gc.start user_id=%s", user_id)

            stats = {
                "user_id": user_id,
                "deleted": 0,
                "archived": 0,
                "compressed": 0,
                "optimized_storage": 0,
                "ts": now_ts(),
            }

            # 1. Delete stale facts
            stale = check_stale(
                user_id, days_threshold=self.gc_policies["stale_threshold_days"]
            )
            for fact_id in stale[:100]:  # Limit to 100 per cycle
                self._delete_fact(user_id, fact_id)
                stats["deleted"] += 1
            logger.info(
                "gc.step=delete_stale user_id=%s deleted=%d", user_id, stats["deleted"]
            )

            # 2. Archive old facts
            archived = self._archive_old_facts(user_id)
            stats["archived"] = archived
            logger.info("gc.step=archive user_id=%s archived=%d", user_id, archived)

            # 3. Check memory pressure
            fact_count = self._get_fact_count(user_id)
            if fact_count > self.gc_policies["max_facts_per_user"]:
                over_limit = fact_count - self.gc_policies["max_facts_per_user"]
                removed = self._remove_low_priority_facts(user_id, over_limit)
                stats["deleted"] += removed
                logger.info(
                    "gc.step=pressure_relief user_id=%s removed=%d", user_id, removed
                )

            # 4. Compress if needed — use knowledge evolution for dedup + archival
            if fact_count > self.gc_policies["compress_above_count"]:
                ke = self.knowledge_evolution
                if ke is not None:
                    evolution_result = ke.evolve_all(user_id)
                    stats["compressed"] = evolution_result.get("total_updates", 0)
                    logger.info(
                        "gc.step=evolve user_id=%s compressed=%d",
                        user_id,
                        stats["compressed"],
                    )
                else:
                    logger.warning(
                        "gc.step=evolve skipped user_id=%s reason=knowledge_evolution_unavailable",
                        user_id,
                    )

            # 5. Optimize storage
            optimized = self._optimize_storage(user_id)
            stats["optimized_storage"] = optimized

            # Log GC event
            append_event(user_id, "memory.gc", stats)
            logger.info(
                "gc.complete user_id=%s deleted=%d archived=%d compressed=%d",
                user_id,
                stats["deleted"],
                stats["archived"],
                stats["compressed"],
            )

            return stats

        except (sqlite3.Error, OSError) as e:
            logger.error("gc.error user_id=%s err=%s", user_id, e, exc_info=True)
            append_event(user_id, "memory.gc_error", {"error": str(e)})
            return {
                "user_id": user_id,
                "error": str(e),
                "ts": now_ts(),
            }

    def _delete_fact(self, user_id: str, fact_id: str) -> None:
        """Delete fact from memory (soft-delete for consistency with knowledge_evolution)."""
        try:
            exec_one(
                "UPDATE memory_nodes SET deleted=1 WHERE user_id=? AND id=?",
                (user_id, fact_id),
            )
            logger.debug("gc.soft_delete fact_id=%s", fact_id)
        except sqlite3.Error as e:
            logger.warning("gc.delete_err fact_id=%s err=%s", fact_id, e)

    def _archive_old_facts(self, user_id: str) -> int:
        """Archive facts older than threshold."""
        try:
            threshold_ts = now_ts() - (
                self.gc_policies["archive_threshold_days"] * 86400
            )

            rows = fetch_all(
                """
            SELECT id FROM memory_nodes
            WHERE user_id=? AND ts < ? AND layer NOT IN ('L3_archive','L3')
            AND deleted=0
            LIMIT 1000
            """,
                (user_id, threshold_ts),
            )

            archived_count = 0
            for row in rows:
                exec_one(
                    "UPDATE memory_nodes SET layer='L3_archive' WHERE id=?",
                    (row["id"],),
                )
                archived_count += 1

            logger.info("gc.archived user_id=%s count=%d", user_id, archived_count)
            return archived_count
        except sqlite3.Error as e:
            logger.error("gc.archive_err user_id=%s err=%s", user_id, e, exc_info=True)
            return 0

    def _get_fact_count(self, user_id: str) -> int:
        """Get count of facts for user."""
        try:
            row = fetch_one(
                "SELECT COUNT(*) as cnt FROM memory_nodes WHERE user_id=? AND layer != 'L3_archive' AND deleted=0",
                (user_id,),
            )
            return int(row["cnt"]) if row else 0
        except sqlite3.Error as e:
            logger.warning("gc.count_err user_id=%s err=%s", user_id, e)
            return 0

    def _remove_low_priority_facts(self, user_id: str, count: int) -> int:
        """Remove lowest priority facts."""
        try:
            rows = fetch_all(
                """
            SELECT id FROM memory_nodes
            WHERE user_id=? AND layer != 'L3_archive' AND deleted=0
            ORDER BY importance ASC, ts ASC
            LIMIT ?
            """,
                (user_id, count),
            )

            removed_count = 0
            for row in rows:
                self._delete_fact(user_id, row["id"])
                removed_count += 1

            logger.info(
                "gc.removed_low_priority user_id=%s count=%d", user_id, removed_count
            )
            return removed_count
        except sqlite3.Error as e:
            logger.error("gc.remove_err user_id=%s err=%s", user_id, e, exc_info=True)
            return 0

    def _optimize_storage(self, _user_id: str) -> int:
        """Optimize storage structure."""
        try:
            exec_one("VACUUM")
            logger.debug("gc.vacuum ok")
            return 1
        except sqlite3.Error as e:
            logger.warning("gc.vacuum_err err=%s", e)
            return 0

    def schedule_gc(self, user_id: str, interval_seconds: int = 3600) -> None:
        """Schedule periodic GC (would integrate with scheduler)."""
        logger.info("gc.scheduled user_id=%s interval=%ds", user_id, interval_seconds)

    @property
    def knowledge_evolution(self) -> Optional[Any]:
        """Leniwe ładowanie — brak pliku aihub/knowledge_evolution.py nie psuje importu memory_gc."""
        if self._knowledge_evolution_tried:
            return self._knowledge_evolution_obj
        self._knowledge_evolution_tried = True
        try:
            from aihub.knowledge_evolution import KnowledgeEvolution

            self._knowledge_evolution_obj = KnowledgeEvolution()
        except ImportError:
            self._knowledge_evolution_obj = None
            logger.warning(
                "knowledge_evolution unavailable (ImportError) — GC evolve step will be skipped",
            )
        return self._knowledge_evolution_obj


# Singleton
_gc = MemoryGC()


def collect_garbage(user_id: str) -> Dict[str, Any]:
    """Public API."""
    return _gc.collect_garbage(user_id)


def schedule_gc(user_id: str, interval_seconds: int = 3600) -> None:
    """Public API."""
    return _gc.schedule_gc(user_id, interval_seconds)
