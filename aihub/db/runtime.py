#!/usr/bin/env python3

import json
import logging
import os
import re
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional, Union

try:
    import psycopg2
    import psycopg2.extensions
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover - optional dependency
    psycopg2 = None  # type: ignore
    RealDictCursor = None  # type: ignore

logger = logging.getLogger(__name__)
_DB_LOCK = threading.Lock()


def _db_backend() -> str:
    return (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip()


class PgExecResult:
    """Wynik execute na psycopg2 (fetch / rowcount)."""

    __slots__ = ("_cur",)

    def __init__(self, cur: Any) -> None:
        self._cur = cur

    def fetchall(self) -> list[Any]:
        return self._cur.fetchall()

    def fetchone(self) -> Any:
        return self._cur.fetchone()

    @property
    def rowcount(self) -> int:
        return int(self._cur.rowcount or 0)

    @property
    def lastrowid(self) -> int:
        return 0


class PgConnectionWrapper:
    """Psycopg2 z API zbliżonym do sqlite3.Connection (execute + commit)."""

    __slots__ = ("_raw",)

    def __init__(self, raw: Any) -> None:
        self._raw = raw

    def cursor(self, *args: Any, **kwargs: Any) -> Any:
        if RealDictCursor is None:
            raise RuntimeError("psycopg2 nie jest zainstalowany")
        return self._raw.cursor(cursor_factory=RealDictCursor)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> Union[PgExecResult, Any]:
        if RealDictCursor is None:
            raise RuntimeError("psycopg2 nie jest zainstalowany")
        pg_sql = sql.replace("?", "%s")
        cur = self._raw.cursor(cursor_factory=RealDictCursor)
        cur.execute(pg_sql, params)
        return PgExecResult(cur)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def __enter__(self) -> "PgConnectionWrapper":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self._raw.close()


def _get_db_path():
    """Get DB_PATH dynamically from config each time (for test isolation)."""
    from aihub.config import DB_PATH

    return Path(DB_PATH)


_GOALS_REQUIRED_COLUMNS: dict[str, str] = {
    "goal_id": "TEXT",
    "user_id": "TEXT",
    "title": "TEXT",
    "description": "TEXT",
    "goal_type": "TEXT",
    "source": "TEXT",
    "status": "TEXT DEFAULT 'proposed'",
    "priority": "REAL DEFAULT 0.5",
    "urgency": "REAL DEFAULT 0.5",
    "importance": "REAL DEFAULT 0.5",
    "confidence": "REAL DEFAULT 0.5",
    "created_at": "REAL DEFAULT 0",
    "updated_at": "REAL DEFAULT 0",
    "expires_at": "REAL",
    "parent_goal_id": "TEXT",
    "tags": "TEXT DEFAULT '[]'",
    "success_criteria": "TEXT DEFAULT '[]'",
    "failure_criteria": "TEXT DEFAULT '[]'",
    "progress": "REAL DEFAULT 0",
    "metadata": "TEXT DEFAULT '{}'",
}


def _table_exists(cur: sqlite3.Cursor, table_name: str) -> bool:
    row = cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _table_columns(cur: sqlite3.Cursor, table_name: str) -> set[str]:
    if not _table_exists(cur, table_name):
        return set()
    rows = cur.execute(f"PRAGMA table_info({table_name})").fetchall()
    return {str(r[1]) for r in rows}


def _is_legacy_goals_schema(columns: set[str]) -> bool:
    legacy = {"id", "user_id", "goal", "priority", "created"}
    return "goal_id" not in columns and legacy.issubset(columns)


def _create_goals_table(cur: sqlite3.Cursor) -> None:
    cur.execute(
        """
    CREATE TABLE IF NOT EXISTS goals (
        goal_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT NOT NULL,
        goal_type TEXT NOT NULL,
        source TEXT NOT NULL,
        status TEXT NOT NULL,
        priority REAL NOT NULL,
        urgency REAL NOT NULL,
        importance REAL NOT NULL,
        confidence REAL NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        expires_at REAL,
        parent_goal_id TEXT,
        tags TEXT NOT NULL,
        success_criteria TEXT NOT NULL,
        failure_criteria TEXT NOT NULL,
        progress REAL NOT NULL,
        metadata TEXT NOT NULL
    );
    """
    )


def _migrate_legacy_goals_table(cur: sqlite3.Cursor) -> None:
    legacy_rows = cur.execute(
        "SELECT id, user_id, goal, priority, created FROM goals"
    ).fetchall()
    backup_table = f"goals_legacy_v1_{int(time.time())}"
    cur.execute(f"ALTER TABLE goals RENAME TO {backup_table}")

    _create_goals_table(cur)

    migrated = 0
    for row in legacy_rows:
        user_id = str(row[1] or "default")
        goal_text = str(row[2] or "")
        priority = float(row[3] if row[3] is not None else 0.5)
        created = float(row[4] if row[4] is not None else now_ts())
        goal_id = (
            f"legacy-{int(row[0])}" if row[0] is not None else f"legacy-{migrated}"
        )

        cur.execute(
            """
            INSERT INTO goals(
                goal_id,user_id,title,description,goal_type,source,status,
                priority,urgency,importance,confidence,
                created_at,updated_at,expires_at,parent_goal_id,
                tags,success_criteria,failure_criteria,progress,metadata
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                goal_id,
                user_id,
                goal_text[:200] or f"Legacy goal {goal_id}",
                goal_text,
                "task",
                "legacy_goals_engine",
                "active",
                max(0.0, min(priority, 1.0)),
                max(0.0, min(priority, 1.0)),
                max(0.0, min(priority, 1.0)),
                0.5,
                created,
                created,
                None,
                None,
                json.dumps(["legacy"]),
                json.dumps([]),
                json.dumps([]),
                0.0,
                json.dumps(
                    {
                        "legacy_table": backup_table,
                        "legacy_id": row[0],
                        "migrated_at": now_ts(),
                    }
                ),
            ),
        )
        migrated += 1

    logger.warning(
        "Migrated legacy goals schema to v2: rows=%d backup_table=%s",
        migrated,
        backup_table,
    )


def _ensure_goals_schema(cur: sqlite3.Cursor) -> None:
    columns = _table_columns(cur, "goals")

    if not columns:
        _create_goals_table(cur)
        return

    if _is_legacy_goals_schema(columns):
        _migrate_legacy_goals_table(cur)
        return

    for name, ddl in _GOALS_REQUIRED_COLUMNS.items():
        if name in columns:
            continue
        cur.execute(f"ALTER TABLE goals ADD COLUMN {name} {ddl}")


_MEMORY_V2_ITEMS_ADD_COLUMNS: list[tuple[str, str]] = [
    ("session_id", "TEXT"),
    ("memory_type", "TEXT NOT NULL DEFAULT 'fact'"),
    ("scope", "TEXT NOT NULL DEFAULT 'user'"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("content", "TEXT NOT NULL DEFAULT ''"),
    ("summary", "TEXT NOT NULL DEFAULT ''"),
    ("source_kind", "TEXT NOT NULL DEFAULT 'chat_turn'"),
    ("source_ref", "TEXT"),
    ("importance_score", "REAL NOT NULL DEFAULT 0.0"),
    ("salience_score", "REAL NOT NULL DEFAULT 0.0"),
    ("emotional_weight", "REAL NOT NULL DEFAULT 0.0"),
    ("recurrence_score", "REAL NOT NULL DEFAULT 0.0"),
    ("confidence_score", "REAL NOT NULL DEFAULT 0.0"),
    ("freshness_score", "REAL NOT NULL DEFAULT 0.0"),
    ("identity_relevance_score", "REAL NOT NULL DEFAULT 0.0"),
    ("relation_relevance_score", "REAL NOT NULL DEFAULT 0.0"),
    ("outcome_reinforcement_score", "REAL NOT NULL DEFAULT 0.0"),
    ("source_reliability_score", "REAL NOT NULL DEFAULT 0.7"),
    ("retrieval_priority_score", "REAL NOT NULL DEFAULT 0.0"),
    ("contradiction_state", "TEXT NOT NULL DEFAULT 'none'"),
    ("valid_from_ts", "REAL"),
    ("valid_to_ts", "REAL"),
    ("last_accessed_ts", "REAL"),
    ("last_reinforced_ts", "REAL"),
    ("reinforcement_count", "INTEGER NOT NULL DEFAULT 0"),
    ("success_reinforcements", "INTEGER NOT NULL DEFAULT 0"),
    ("failure_reinforcements", "INTEGER NOT NULL DEFAULT 0"),
    ("decay_bucket", "TEXT NOT NULL DEFAULT 'active'"),
    ("stability_tier", "TEXT NOT NULL DEFAULT 'transient'"),
    ("is_pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("is_archived", "INTEGER NOT NULL DEFAULT 0"),
    ("is_suppressed", "INTEGER NOT NULL DEFAULT 0"),
    ("embedding_vector_ref", "TEXT"),
    ("created_ts", "REAL NOT NULL DEFAULT 0"),
    ("updated_ts", "REAL NOT NULL DEFAULT 0"),
]

_PSYCHE_V2_PROFILE_ADD_COLUMNS: list[tuple[str, str]] = [
    ("core_directness", "REAL NOT NULL DEFAULT 0.5"),
    ("core_patience", "REAL NOT NULL DEFAULT 0.5"),
    ("core_curiosity", "REAL NOT NULL DEFAULT 0.5"),
    ("core_caution", "REAL NOT NULL DEFAULT 0.5"),
    ("core_assertiveness", "REAL NOT NULL DEFAULT 0.5"),
    ("core_formality", "REAL NOT NULL DEFAULT 0.5"),
    ("core_warmth", "REAL NOT NULL DEFAULT 0.5"),
    ("core_initiative", "REAL NOT NULL DEFAULT 0.5"),
    ("core_skepticism", "REAL NOT NULL DEFAULT 0.5"),
    ("core_creativity", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_trust", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_familiarity", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_sync", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_friction", "REAL NOT NULL DEFAULT 0.0"),
    ("relation_warmth", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_directness_tolerance", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_collaboration_confidence", "REAL NOT NULL DEFAULT 0.5"),
    ("relation_interaction_quality_ema", "REAL NOT NULL DEFAULT 0.5"),
    ("stress_load", "REAL NOT NULL DEFAULT 0.0"),
    ("confidence_baseline", "REAL NOT NULL DEFAULT 0.5"),
    ("adaptation_velocity", "REAL NOT NULL DEFAULT 0.2"),
    ("last_reflection_ts", "REAL"),
    ("updated_ts", "REAL NOT NULL DEFAULT 0"),
]

_PSYCHE_V2_STATE_ADD_COLUMNS: list[tuple[str, str]] = [
    ("mood", "REAL NOT NULL DEFAULT 0.5"),
    ("energy", "REAL NOT NULL DEFAULT 0.5"),
    ("focus", "REAL NOT NULL DEFAULT 0.5"),
    ("pressure", "REAL NOT NULL DEFAULT 0.0"),
    ("stability", "REAL NOT NULL DEFAULT 0.5"),
    ("certainty", "REAL NOT NULL DEFAULT 0.5"),
    ("social_openness", "REAL NOT NULL DEFAULT 0.5"),
    ("task_aggression", "REAL NOT NULL DEFAULT 0.5"),
    ("verbosity_bias", "REAL NOT NULL DEFAULT 0.5"),
    ("tool_bias", "REAL NOT NULL DEFAULT 0.5"),
    ("web_bias", "REAL NOT NULL DEFAULT 0.5"),
    ("current_mode", "TEXT NOT NULL DEFAULT 'neutral'"),
    ("pending_mode", "TEXT NOT NULL DEFAULT ''"),
    ("mode_streak", "INTEGER NOT NULL DEFAULT 0"),
    ("pressure_smoothed", "REAL NOT NULL DEFAULT 0.0"),
    ("updated_ts", "REAL NOT NULL DEFAULT 0"),
]

_SQLITE_ACTIVE_STACK_INDEX_DDL: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_memv2_user_type "
    "ON memory_v2_items(user_id, memory_type, created_ts DESC) WHERE is_archived=0;",
    "CREATE INDEX IF NOT EXISTS idx_memv2_user_salience "
    "ON memory_v2_items(user_id, salience_score DESC, created_ts DESC) WHERE is_archived=0;",
    "CREATE INDEX IF NOT EXISTS idx_memv2_user_retrieval_priority "
    "ON memory_v2_items(user_id, retrieval_priority_score DESC, created_ts DESC) "
    "WHERE is_archived=0 AND is_suppressed=0;",
    "CREATE INDEX IF NOT EXISTS idx_memv2_user_scope "
    "ON memory_v2_items(user_id, scope, created_ts DESC) WHERE is_archived=0;",
    "CREATE INDEX IF NOT EXISTS idx_memv2_contradictions "
    "ON memory_v2_items(user_id, contradiction_state, created_ts DESC) "
    "WHERE contradiction_state != 'none';",
    "CREATE INDEX IF NOT EXISTS idx_memv2_decay "
    "ON memory_v2_items(user_id, decay_bucket, last_accessed_ts) WHERE is_archived=0;",
]


def _sqlite_add_column_if_missing(
    cur: sqlite3.Cursor,
    table: str,
    column: str,
    decl: str,
    report: dict[str, Any],
) -> None:
    if not _table_exists(cur, table):
        return
    present = _table_columns(cur, table)
    if column in present:
        return
    cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    report.setdefault("columns_added", []).append(f"{table}.{column}")


def _sqlite_migrate_memory_v2_item_columns(
    cur: sqlite3.Cursor, report: dict[str, Any]
) -> None:
    for col, decl in _MEMORY_V2_ITEMS_ADD_COLUMNS:
        _sqlite_add_column_if_missing(cur, "memory_v2_items", col, decl, report)


def _sqlite_migrate_psyche_v2_profile_columns(
    cur: sqlite3.Cursor, report: dict[str, Any]
) -> None:
    for col, decl in _PSYCHE_V2_PROFILE_ADD_COLUMNS:
        _sqlite_add_column_if_missing(cur, "psyche_v2_profile", col, decl, report)


def _sqlite_migrate_psyche_v2_state_columns(
    cur: sqlite3.Cursor, report: dict[str, Any]
) -> None:
    for col, decl in _PSYCHE_V2_STATE_ADD_COLUMNS:
        _sqlite_add_column_if_missing(cur, "psyche_v2_state", col, decl, report)


def _sqlite_backfill_active_stack(cur: sqlite3.Cursor, report: dict[str, Any]) -> None:
    if _table_exists(cur, "memory_v2_items"):
        cur.execute(
            """
            UPDATE memory_v2_items SET stability_tier = 'transient'
            WHERE stability_tier IS NULL OR TRIM(COALESCE(stability_tier, '')) = ''
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"memory_v2_items.stability_tier cleared_null rows={cur.rowcount}"
            )
        cur.execute(
            """
            UPDATE memory_v2_items SET decay_bucket = 'active'
            WHERE decay_bucket IS NULL OR TRIM(COALESCE(decay_bucket, '')) = ''
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"memory_v2_items.decay_bucket cleared_null rows={cur.rowcount}"
            )
        cur.execute(
            """
            UPDATE memory_v2_items SET retrieval_priority_score = CASE
                WHEN salience_score * 0.92 < 0.0 THEN 0.0
                WHEN salience_score * 0.92 > 1.0 THEN 1.0
                ELSE salience_score * 0.92
            END
            WHERE (retrieval_priority_score IS NULL OR retrieval_priority_score <= 0.001)
              AND salience_score > 0.05
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"memory_v2_items.retrieval_priority_score from_salience rows={cur.rowcount}"
            )
        cur.execute(
            """
            UPDATE memory_v2_items SET outcome_reinforcement_score = 0.5
            WHERE outcome_reinforcement_score IS NULL
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"memory_v2_items.outcome_reinforcement_score null_fix rows={cur.rowcount}"
            )

    if _table_exists(cur, "psyche_v2_profile"):
        cur.execute(
            """
            UPDATE psyche_v2_profile SET relation_interaction_quality_ema = CASE
                WHEN (relation_trust * 0.55 + relation_sync * 0.45) < 0.0 THEN 0.0
                WHEN (relation_trust * 0.55 + relation_sync * 0.45) > 1.0 THEN 1.0
                ELSE (relation_trust * 0.55 + relation_sync * 0.45)
            END
            WHERE relation_interaction_quality_ema IS NULL
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"psyche_v2_profile.relation_interaction_quality_ema null_fix rows={cur.rowcount}"
            )
        cur.execute(
            """
            UPDATE psyche_v2_profile SET relation_interaction_quality_ema = CASE
                WHEN (relation_trust * 0.55 + relation_sync * 0.45) < 0.0 THEN 0.0
                WHEN (relation_trust * 0.55 + relation_sync * 0.45) > 1.0 THEN 1.0
                ELSE (relation_trust * 0.55 + relation_sync * 0.45)
            END
            WHERE ABS(relation_interaction_quality_ema - 0.5) < 1e-6
              AND (ABS(relation_trust - 0.5) > 0.02 OR ABS(relation_sync - 0.5) > 0.02)
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"psyche_v2_profile.relation_interaction_quality_ema seeded_from_trust_sync rows={cur.rowcount}"
            )

    if _table_exists(cur, "psyche_v2_state"):
        cur.execute(
            "UPDATE psyche_v2_state SET pending_mode = '' WHERE pending_mode IS NULL"
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"psyche_v2_state.pending_mode null_fix rows={cur.rowcount}"
            )
        cur.execute(
            """
            UPDATE psyche_v2_state SET pressure_smoothed = pressure
            WHERE (pressure_smoothed IS NULL OR pressure_smoothed = 0.0) AND pressure > 0.0
            """
        )
        if cur.rowcount:
            report.setdefault("backfills", []).append(
                f"psyche_v2_state.pressure_smoothed from_pressure rows={cur.rowcount}"
            )


def _sqlite_index_exists(cur: sqlite3.Cursor, index_name: str) -> bool:
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1",
        (index_name,),
    )
    return cur.fetchone() is not None


def _sqlite_ensure_active_stack_indexes(
    cur: sqlite3.Cursor, report: dict[str, Any]
) -> None:
    if not _table_exists(cur, "memory_v2_items"):
        return
    cols = _table_columns(cur, "memory_v2_items")
    required_for_priority_idx = {
        "user_id",
        "retrieval_priority_score",
        "created_ts",
        "is_archived",
        "is_suppressed",
    }
    if not required_for_priority_idx.issubset(cols):
        return
    for ddl in _SQLITE_ACTIVE_STACK_INDEX_DDL:
        parts = ddl.split()
        idx_name = ""
        for i, p in enumerate(parts):
            if p.upper() == "EXISTS" and i + 1 < len(parts):
                idx_name = parts[i + 1]
                break
        if not idx_name:
            continue
        existed = _sqlite_index_exists(cur, idx_name)
        cur.execute(ddl)
        if not existed and _sqlite_index_exists(cur, idx_name):
            report.setdefault("indexes_ensured", []).append(idx_name)


def _log_active_stack_migration(report: dict[str, Any]) -> None:
    if report.get("skipped"):
        return
    chunks: list[str] = []
    if report.get("columns_added"):
        chunks.append(f"columns_added={report['columns_added']}")
    if report.get("backfills"):
        chunks.append(f"backfills={report['backfills']}")
    if report.get("indexes_ensured"):
        chunks.append(f"indexes_ensured={report['indexes_ensured']}")
    if chunks:
        logger.info("active_stack_sqlite_migration %s", "; ".join(chunks))


def apply_active_stack_migrations_to_connection(con: Any) -> dict[str, Any]:
    """
    Idempotent migrations for Memory V2 + Psyche V2 on SQLite.

    Na PostgreSQL schemat pochodzi z ``postgres_bootstrap.sql`` — migracji
    specyficznych dla SQLite tutaj nie uruchamiamy.
    """
    if isinstance(con, PgConnectionWrapper):
        return {"skipped": "postgres_bootstrap"}

    report: dict[str, Any] = {
        "columns_added": [],
        "backfills": [],
        "indexes_ensured": [],
    }
    cur = con.cursor()
    _sqlite_migrate_memory_v2_item_columns(cur, report)
    _sqlite_migrate_psyche_v2_profile_columns(cur, report)
    _sqlite_migrate_psyche_v2_state_columns(cur, report)
    _sqlite_backfill_active_stack(cur, report)
    _sqlite_ensure_active_stack_indexes(cur, report)
    return report


EXPECTED_ACTIVE_STACK_TABLES: frozenset[str] = frozenset(
    {
        "memory_v2_items",
        "memory_v2_links",
        "memory_v2_consolidations",
        "memory_v2_procedures",
        "memory_v2_lessons",
        "psyche_v2_profile",
        "psyche_v2_state",
        "psyche_v2_events",
        "psyche_v2_behavior_rules",
        "psyche_v2_habits",
    }
)

EXPECTED_MEMORY_V2_ITEMS_COLUMNS: frozenset[str] = frozenset(
    c for c, _ in _MEMORY_V2_ITEMS_ADD_COLUMNS
) | {"id", "user_id"}

EXPECTED_PSYCHE_V2_PROFILE_COLUMNS: frozenset[str] = frozenset(
    c for c, _ in _PSYCHE_V2_PROFILE_ADD_COLUMNS
) | {"user_id"}

EXPECTED_PSYCHE_V2_STATE_COLUMNS: frozenset[str] = frozenset(
    c for c, _ in _PSYCHE_V2_STATE_ADD_COLUMNS
) | {"user_id"}


def inspect_active_stack_schema_sqlite(con: sqlite3.Connection) -> dict[str, Any]:
    """Read-only schema health for Memory/Psyche V2 (no mutations)."""
    cur = con.cursor()
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for t in sorted(EXPECTED_ACTIVE_STACK_TABLES):
        if not _table_exists(cur, t):
            missing_tables.append(t)
    for t, expected in (
        ("memory_v2_items", EXPECTED_MEMORY_V2_ITEMS_COLUMNS),
        ("psyche_v2_profile", EXPECTED_PSYCHE_V2_PROFILE_COLUMNS),
        ("psyche_v2_state", EXPECTED_PSYCHE_V2_STATE_COLUMNS),
    ):
        if not _table_exists(cur, t):
            continue
        cols = _table_columns(cur, t)
        for c in sorted(expected):
            if c not in cols:
                missing_columns.append(f"{t}.{c}")
    return {
        "backend": os.getenv("DB_BACKEND", "sqlite").lower().strip(),
        "ok": len(missing_tables) == 0 and len(missing_columns) == 0,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


def inspect_active_stack_schema_postgres(raw: Any) -> dict[str, Any]:
    """To samo co SQLite, ale przez information_schema (PostgreSQL)."""
    cur = raw.cursor()
    missing_tables: list[str] = []
    for t in sorted(EXPECTED_ACTIVE_STACK_TABLES):
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (t,),
        )
        if cur.fetchone() is None:
            missing_tables.append(t)
    missing_columns: list[str] = []
    for t, expected in (
        ("memory_v2_items", EXPECTED_MEMORY_V2_ITEMS_COLUMNS),
        ("psyche_v2_profile", EXPECTED_PSYCHE_V2_PROFILE_COLUMNS),
        ("psyche_v2_state", EXPECTED_PSYCHE_V2_STATE_COLUMNS),
    ):
        if t in missing_tables:
            continue
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = %s",
            (t,),
        )
        have = {str(r[0]) for r in cur.fetchall()}
        for c in sorted(expected):
            if c not in have:
                missing_columns.append(f"{t}.{c}")
    cur.close()
    return {
        "backend": "postgres",
        "ok": len(missing_tables) == 0 and len(missing_columns) == 0,
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
    }


def get_active_stack_schema_health() -> dict[str, Any]:
    """Inspect current DB connection (read-only)."""
    with _DB_LOCK, _conn() as con:
        if isinstance(con, PgConnectionWrapper):
            return inspect_active_stack_schema_postgres(con._raw)
        return inspect_active_stack_schema_sqlite(con)


def cognitive_memory_schema_status() -> tuple[dict[str, str], list[str]]:
    """Sprawdza obecność ``memory_nodes``, ``memory_facts`` (widok), ``memory_meta`` — SQLite i PostgreSQL."""
    alerts: list[str] = []
    if _db_backend() == "postgres":
        rows = fetch_all(
            """
            SELECT table_name AS name,
                   CASE WHEN table_type = 'VIEW' THEN 'view' ELSE 'table' END AS typ
            FROM information_schema.tables
            WHERE table_schema = current_schema()
              AND table_name IN ('memory_nodes', 'memory_facts', 'memory_meta')
            """
        )
        schema_ok = {str(r["name"]): str(r["typ"]) for r in rows}
    else:
        rows = fetch_all(
            "SELECT name, type FROM sqlite_master WHERE name IN "
            "('memory_nodes','memory_facts','memory_meta')"
        )
        schema_ok = {str(r["name"]): str(r["type"]).lower() for r in rows}

    if "memory_nodes" not in schema_ok:
        alerts.append("MISSING_TABLE: memory_nodes")
    if "memory_facts" not in schema_ok:
        alerts.append("MISSING_VIEW: memory_facts")
    if "memory_meta" not in schema_ok:
        alerts.append("MISSING_TABLE: memory_meta")
    return schema_ok, alerts


class DBAdapter(ABC):
    """Adapter bazy: SQLite (domyślnie) lub PostgreSQL."""

    @abstractmethod
    def connect(self) -> Any:
        """Połączenie używane przez warstwę ``_conn()`` (sqlite lub wrapper PG)."""


class SQLiteAdapter(DBAdapter):
    """SQLite adapter used by default runtime."""

    def __init__(self, db_path):
        self.db_path = Path(db_path)

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        path_str = str(self.db_path)
        last_exc: sqlite3.OperationalError | None = None
        con: sqlite3.Connection | None = None
        for attempt in range(4):
            try:
                con = sqlite3.connect(path_str, check_same_thread=False, timeout=30.0)
                break
            except sqlite3.OperationalError as e:
                last_exc = e
                if "locked" not in str(e).lower() or attempt >= 3:
                    raise
                time.sleep(0.05 * (attempt + 1))
        if con is None:
            raise last_exc or sqlite3.OperationalError("sqlite connect failed")
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA temp_store=MEMORY;")
        con.execute("PRAGMA foreign_keys=ON;")
        con.execute("PRAGMA busy_timeout=30000;")
        return con


class PostgresAdapter(DBAdapter):
    """PostgreSQL przez psycopg2; ``connect`` zwraca :class:`PgConnectionWrapper`."""

    def __init__(self, dsn: str) -> None:
        self.dsn = (dsn or "").strip()

    def connect(self) -> PgConnectionWrapper:
        if psycopg2 is None:
            raise RuntimeError(
                "DB_BACKEND=postgres wymaga pakietu psycopg2-binary (pip install psycopg2-binary)"
            )
        if not self.dsn:
            raise RuntimeError("DB_BACKEND=postgres wymaga niepustego POSTGRES_DSN w środowisku")
        raw = psycopg2.connect(self.dsn)
        raw.autocommit = False
        return PgConnectionWrapper(raw)


_ADAPTER_HOLDER: list[DBAdapter] = []


def _get_adapter() -> DBAdapter:
    """Zwraca adapter SQLite lub Postgres wg ``DB_BACKEND``."""
    current = _ADAPTER_HOLDER[0] if _ADAPTER_HOLDER else None
    backend = _db_backend()

    if backend == "postgres":
        dsn = os.getenv("POSTGRES_DSN", "").strip()
        if not isinstance(current, PostgresAdapter):
            current = PostgresAdapter(dsn)
            if _ADAPTER_HOLDER:
                _ADAPTER_HOLDER[0] = current
            else:
                _ADAPTER_HOLDER.append(current)
        elif isinstance(current, PostgresAdapter) and current.dsn != dsn and dsn:
            current = PostgresAdapter(dsn)
            _ADAPTER_HOLDER[0] = current
        return current  # type: ignore[return-value]

    if backend not in ("", "sqlite"):
        raise RuntimeError(
            f"Nieobsługiwany DB_BACKEND={backend!r}. Użyj sqlite lub postgres."
        )

    current_db_path = _get_db_path()
    if not isinstance(current, SQLiteAdapter):
        current = SQLiteAdapter(current_db_path)
        if _ADAPTER_HOLDER:
            _ADAPTER_HOLDER[0] = current
        else:
            _ADAPTER_HOLDER.append(current)

    if isinstance(current, SQLiteAdapter) and current.db_path != current_db_path:
        current = SQLiteAdapter(current_db_path)
        _ADAPTER_HOLDER[0] = current

    return current


@contextmanager
def _conn() -> Iterator[Any]:
    """Open one DB connection for the block and always close it."""
    adapter = _get_adapter()
    con = adapter.connect()
    try:
        yield con
    finally:
        try:
            con.close()
        except Exception:
            logger.debug("sqlite/pg connection close failed", exc_info=True)


def active_sqlite_adapter_path() -> Path | None:
    """Return the SQLite path held by the cached adapter, if any."""
    with _DB_LOCK:
        adapter = _ADAPTER_HOLDER[0] if _ADAPTER_HOLDER else None
        if isinstance(adapter, SQLiteAdapter):
            return Path(adapter.db_path)
    return None


def dispose_sqlite_engine() -> None:
    """Checkpoint WAL and clear adapter cache.

    Used between tests and when switching DB_PATH so temp files can be removed
    and ``database is locked`` does not persist across pytest cases.

    Adapter cache is cleared before WAL checkpoint so concurrent ``_get_adapter``
    calls cannot resurrect a stale path while the temp directory is being removed.
    """
    with _DB_LOCK:
        adapter: DBAdapter | None = _ADAPTER_HOLDER[0] if _ADAPTER_HOLDER else None
        _ADAPTER_HOLDER.clear()
        if isinstance(adapter, PostgresAdapter):
            return
        if not isinstance(adapter, SQLiteAdapter):
            return
        p = adapter.db_path
        parent = p.parent
        if not parent.exists() or not p.exists():
            return
        aux: sqlite3.Connection | None = None
        try:
            aux = sqlite3.connect(str(p), timeout=30.0)
            aux.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        except (OSError, sqlite3.Error):
            logger.debug(
                "dispose_sqlite_engine: checkpoint failed for %s",
                p,
                exc_info=True,
            )
        finally:
            if aux is not None:
                try:
                    aux.close()
                except Exception:
                    logger.debug(
                        "dispose_sqlite_engine: aux close failed for %s",
                        p,
                        exc_info=True,
                    )


def init_db() -> None:
    with _DB_LOCK, _conn() as con:
        if isinstance(con, PgConnectionWrapper):
            from aihub.pg_bootstrap import run_postgres_bootstrap

            run_postgres_bootstrap(con._raw)
            try:
                from aihub.sqlite_pg_import import run_sqlite_import_if_enabled

                run_sqlite_import_if_enabled()
            except Exception as e:  # noqa: BLE001
                logger.error("sqlite_pg_import nie powiódł się: %s", e, exc_info=True)
            return

        cur = con.cursor()

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS memory_nodes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            layer TEXT NOT NULL,
            content TEXT NOT NULL,
            tags TEXT NOT NULL,
            meta TEXT NOT NULL,
            ts REAL NOT NULL,
            importance REAL NOT NULL,
            confidence REAL NOT NULL,
            deleted INTEGER NOT NULL DEFAULT 0
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_user_layer_ts ON memory_nodes(user_id, layer, ts DESC) WHERE deleted=0;"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_nodes_user_imp ON memory_nodes(user_id, importance DESC, confidence DESC, ts DESC) WHERE deleted=0;"
        )

        try:
            cur.execute(
                """
            CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
            USING fts5(content, user_id UNINDEXED, layer UNINDEXED, node_id UNINDEXED);
            """
            )
        except sqlite3.OperationalError as exc:
            logger.debug("SQLite FTS5 table unavailable; keyword fallback will be used: %s", exc)

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS stm_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            meta TEXT NOT NULL,
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_stm_user_ts ON stm_messages(user_id, ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS psyche_state (
            user_id TEXT PRIMARY KEY,
            mood REAL NOT NULL,
            energy REAL NOT NULL,
            focus REAL NOT NULL,
            style TEXT NOT NULL,
            temperature REAL NOT NULL,
            traits TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS event_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            data TEXT NOT NULL,
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_user_ts ON event_log(user_id, ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS chat_sessions (
            user_id TEXT NOT NULL,
            id TEXT NOT NULL,
            title TEXT NOT NULL,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at REAL,
            PRIMARY KEY (user_id, id)
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_updated "
            "ON chat_sessions(user_id, updated_at DESC);"
        )
        _chat_sessions_mig: dict[str, Any] = {}
        _sqlite_add_column_if_missing(
            cur,
            "chat_sessions",
            "archived",
            "INTEGER NOT NULL DEFAULT 0",
            _chat_sessions_mig,
        )
        _sqlite_add_column_if_missing(
            cur,
            "chat_sessions",
            "archived_at",
            "REAL",
            _chat_sessions_mig,
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_archived "
            "ON chat_sessions(user_id, archived, updated_at DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS chat_uploaded_files (
            file_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            stored_path TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            extracted_text TEXT,
            extract_status TEXT NOT NULL,
            extract_error TEXT,
            created_at REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_uploads_session "
            "ON chat_uploaded_files(user_id, session_id, created_at DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS user_vault_entries (
            user_id TEXT NOT NULL,
            alias_key TEXT NOT NULL,
            ciphertext BLOB NOT NULL,
            updated_ts REAL NOT NULL,
            PRIMARY KEY (user_id, alias_key)
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_vault_user "
            "ON user_vault_entries(user_id);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS chat_session_messages (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_sess_msg_session "
            "ON chat_session_messages(user_id, session_id, created_at ASC, id ASC);"
        )
        _chat_sess_mig: dict[str, Any] = {}
        _sqlite_add_column_if_missing(
            cur,
            "chat_session_messages",
            "meta",
            "TEXT NOT NULL DEFAULT '{}'",
            _chat_sess_mig,
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            ts REAL NOT NULL,
            db_path TEXT NOT NULL
        );
        """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_snap_ts ON snapshots(ts DESC);")

        cur.execute(
            """
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
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_meta_priority "
            "ON memory_meta(overall_priority DESC) WHERE archived=0;"
        )

        _ensure_goals_schema(cur)
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_goals_user_status "
            "ON goals(user_id, status, updated_at DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_goals_user_type "
            "ON goals(user_id, goal_type, updated_at DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_goals_user_expires "
            "ON goals(user_id, expires_at);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS goal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data TEXT NOT NULL,
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_events_goal_ts "
            "ON goal_events(goal_id, ts DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_events_user_ts "
            "ON goal_events(user_id, ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS goal_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            payload TEXT NOT NULL,
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_goal_links_goal_ts "
            "ON goal_links(goal_id, ts DESC);"
        )

        cur.execute("DROP VIEW IF EXISTS memory_facts;")
        cur.execute(
            """
        CREATE VIEW memory_facts AS
        SELECT id, user_id, layer, content, tags, meta, ts AS created_ts,
               importance, confidence, deleted
        FROM memory_nodes;
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS knowledge_nodes (
            id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            content TEXT NOT NULL,
            user_id TEXT,
            created_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS knowledge_edges (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            relation TEXT NOT NULL,
            weight REAL NOT NULL
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS experiences (
            experience_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            trace_id TEXT,
            goal_id TEXT,
            created_at REAL NOT NULL,
            user_input_summary TEXT NOT NULL,
            selected_strategy TEXT NOT NULL,
            reason_codes TEXT NOT NULL,
            tools_needed INTEGER NOT NULL DEFAULT 0,
            tools_executed INTEGER NOT NULL DEFAULT 0,
            research_needed INTEGER NOT NULL DEFAULT 0,
            research_executed INTEGER NOT NULL DEFAULT 0,
            planner_recommended INTEGER NOT NULL DEFAULT 0,
            planner_executed INTEGER NOT NULL DEFAULT 0,
            agentic_recommended INTEGER NOT NULL DEFAULT 0,
            agentic_executed INTEGER NOT NULL DEFAULT 0,
            outcome_type TEXT NOT NULL,
            success INTEGER NOT NULL,
            failure_type TEXT,
            fallback_flag INTEGER NOT NULL DEFAULT 0,
            degraded_flag INTEGER NOT NULL DEFAULT 0,
            latency_ms REAL,
            content_hash TEXT NOT NULL,
            embedding_provider TEXT NOT NULL,
            embedding_model TEXT NOT NULL,
            embedding_dimension INTEGER NOT NULL,
            embedding_input_type TEXT NOT NULL,
            semantic_embedding TEXT,
            short_lesson_learned TEXT,
            reflection_seed TEXT,
            metadata TEXT NOT NULL DEFAULT '{}',
            deleted INTEGER NOT NULL DEFAULT 0
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiences_user_ts "
            "ON experiences(user_id, created_at DESC) WHERE deleted=0;"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiences_user_strategy "
            "ON experiences(user_id, selected_strategy, created_at DESC) WHERE deleted=0;"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiences_content_hash "
            "ON experiences(content_hash) WHERE deleted=0;"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiences_trace_id "
            "ON experiences(trace_id) WHERE deleted=0 AND trace_id IS NOT NULL;"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiences_session_id "
            "ON experiences(session_id, created_at DESC) WHERE deleted=0 AND session_id IS NOT NULL;"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS strategy_decision_bias (
            user_id TEXT PRIMARY KEY,
            bias_instant REAL NOT NULL DEFAULT 0,
            bias_contextual REAL NOT NULL DEFAULT 0,
            bias_research REAL NOT NULL DEFAULT 0,
            bias_agentic REAL NOT NULL DEFAULT 0,
            updated_at REAL NOT NULL,
            metrics_snapshot TEXT NOT NULL DEFAULT '{}'
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS consistency_checks (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            fact_text TEXT NOT NULL,
            classification TEXT NOT NULL,
            confidence REAL NOT NULL,
            matched_node_id TEXT,
            matched_content TEXT,
            similarity_score REAL NOT NULL DEFAULT 0,
            reasoning TEXT NOT NULL DEFAULT '',
            suggested_action TEXT NOT NULL DEFAULT '',
            metadata TEXT NOT NULL DEFAULT '{}',
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_consistency_user_ts "
            "ON consistency_checks(user_id, ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS reflections (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            action_type TEXT NOT NULL,
            outcome TEXT NOT NULL,
            outcome_score REAL NOT NULL,
            lesson_learned TEXT NOT NULL DEFAULT '',
            policy_signal TEXT NOT NULL DEFAULT 'neutral',
            policy_weight REAL NOT NULL DEFAULT 0.3,
            recommended_adjustment TEXT NOT NULL DEFAULT '',
            patterns_detected TEXT NOT NULL DEFAULT '[]',
            metadata TEXT NOT NULL DEFAULT '{}',
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_reflections_user_ts "
            "ON reflections(user_id, ts DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_reflections_user_action "
            "ON reflections(user_id, action_type, ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS policy_profiles (
            user_id TEXT PRIMARY KEY,
            hints TEXT NOT NULL DEFAULT '[]',
            reliability_index REAL NOT NULL DEFAULT 0.5,
            total_reflections INTEGER NOT NULL DEFAULT 0,
            ts REAL NOT NULL
        );
        """
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS simulations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            variants_evaluated INTEGER NOT NULL,
            best_action TEXT NOT NULL DEFAULT '',
            best_score REAL NOT NULL DEFAULT 0,
            ranked_data TEXT NOT NULL DEFAULT '[]',
            simulation_time_ms REAL NOT NULL DEFAULT 0,
            metadata TEXT NOT NULL DEFAULT '{}',
            ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_simulations_user_ts "
            "ON simulations(user_id, ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS memory_v2_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            session_id TEXT,
            memory_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_ref TEXT,
            importance_score REAL NOT NULL DEFAULT 0.0,
            salience_score REAL NOT NULL DEFAULT 0.0,
            emotional_weight REAL NOT NULL DEFAULT 0.0,
            recurrence_score REAL NOT NULL DEFAULT 0.0,
            confidence_score REAL NOT NULL DEFAULT 0.0,
            freshness_score REAL NOT NULL DEFAULT 0.0,
            identity_relevance_score REAL NOT NULL DEFAULT 0.0,
            relation_relevance_score REAL NOT NULL DEFAULT 0.0,
            outcome_reinforcement_score REAL NOT NULL DEFAULT 0.0,
            source_reliability_score REAL NOT NULL DEFAULT 0.7,
            retrieval_priority_score REAL NOT NULL DEFAULT 0.0,
            contradiction_state TEXT NOT NULL DEFAULT 'none',
            valid_from_ts REAL,
            valid_to_ts REAL,
            last_accessed_ts REAL,
            last_reinforced_ts REAL,
            reinforcement_count INTEGER NOT NULL DEFAULT 0,
            success_reinforcements INTEGER NOT NULL DEFAULT 0,
            failure_reinforcements INTEGER NOT NULL DEFAULT 0,
            decay_bucket TEXT NOT NULL DEFAULT 'active',
            stability_tier TEXT NOT NULL DEFAULT 'transient',
            is_pinned INTEGER NOT NULL DEFAULT 0,
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_suppressed INTEGER NOT NULL DEFAULT 0,
            embedding_vector_ref TEXT,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
        )
        _log_active_stack_migration(apply_active_stack_migrations_to_connection(con))

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS memory_v2_links (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            from_memory_id TEXT NOT NULL,
            to_memory_id TEXT NOT NULL,
            link_type TEXT NOT NULL,
            weight REAL NOT NULL DEFAULT 0.0,
            created_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memv2_links_from "
            "ON memory_v2_links(from_memory_id, created_ts DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memv2_links_to "
            "ON memory_v2_links(to_memory_id, created_ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS memory_v2_consolidations (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            consolidation_type TEXT NOT NULL,
            input_memory_ids_json TEXT NOT NULL,
            output_memory_id TEXT NOT NULL,
            compression_ratio REAL NOT NULL DEFAULT 0.0,
            created_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memv2_consolidations_user "
            "ON memory_v2_consolidations(user_id, created_ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS memory_v2_procedures (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            trigger_pattern TEXT NOT NULL,
            recommended_strategy TEXT NOT NULL,
            recommended_tools_json TEXT NOT NULL,
            avoid_patterns_json TEXT NOT NULL,
            success_rate REAL NOT NULL DEFAULT 0.0,
            failure_rate REAL NOT NULL DEFAULT 0.0,
            confidence_score REAL NOT NULL DEFAULT 0.0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            last_validated_ts REAL,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memv2_procedures_user "
            "ON memory_v2_procedures(user_id, confidence_score DESC, created_ts DESC);"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memv2_procedures_trigger "
            "ON memory_v2_procedures(user_id, trigger_pattern);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS memory_v2_lessons (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            lesson_scope TEXT NOT NULL,
            lesson_text TEXT NOT NULL,
            applies_when_json TEXT NOT NULL,
            avoid_when_json TEXT NOT NULL,
            strength_score REAL NOT NULL DEFAULT 0.0,
            evidence_count INTEGER NOT NULL DEFAULT 0,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_memv2_lessons_user "
            "ON memory_v2_lessons(user_id, strength_score DESC, created_ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS psyche_v2_profile (
            user_id TEXT PRIMARY KEY,
            core_directness REAL NOT NULL DEFAULT 0.5,
            core_patience REAL NOT NULL DEFAULT 0.5,
            core_curiosity REAL NOT NULL DEFAULT 0.5,
            core_caution REAL NOT NULL DEFAULT 0.5,
            core_assertiveness REAL NOT NULL DEFAULT 0.5,
            core_formality REAL NOT NULL DEFAULT 0.5,
            core_warmth REAL NOT NULL DEFAULT 0.5,
            core_initiative REAL NOT NULL DEFAULT 0.5,
            core_skepticism REAL NOT NULL DEFAULT 0.5,
            core_creativity REAL NOT NULL DEFAULT 0.5,
            relation_trust REAL NOT NULL DEFAULT 0.5,
            relation_familiarity REAL NOT NULL DEFAULT 0.5,
            relation_sync REAL NOT NULL DEFAULT 0.5,
            relation_friction REAL NOT NULL DEFAULT 0.0,
            relation_warmth REAL NOT NULL DEFAULT 0.5,
            relation_directness_tolerance REAL NOT NULL DEFAULT 0.5,
            relation_collaboration_confidence REAL NOT NULL DEFAULT 0.5,
            relation_interaction_quality_ema REAL NOT NULL DEFAULT 0.5,
            stress_load REAL NOT NULL DEFAULT 0.0,
            confidence_baseline REAL NOT NULL DEFAULT 0.5,
            adaptation_velocity REAL NOT NULL DEFAULT 0.2,
            last_reflection_ts REAL,
            updated_ts REAL NOT NULL
        );
        """
        )
        _log_active_stack_migration(apply_active_stack_migrations_to_connection(con))

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS psyche_v2_state (
            user_id TEXT PRIMARY KEY,
            mood REAL NOT NULL DEFAULT 0.5,
            energy REAL NOT NULL DEFAULT 0.5,
            focus REAL NOT NULL DEFAULT 0.5,
            pressure REAL NOT NULL DEFAULT 0.0,
            stability REAL NOT NULL DEFAULT 0.5,
            certainty REAL NOT NULL DEFAULT 0.5,
            social_openness REAL NOT NULL DEFAULT 0.5,
            task_aggression REAL NOT NULL DEFAULT 0.5,
            verbosity_bias REAL NOT NULL DEFAULT 0.5,
            tool_bias REAL NOT NULL DEFAULT 0.5,
            web_bias REAL NOT NULL DEFAULT 0.5,
            current_mode TEXT NOT NULL DEFAULT 'neutral',
            pending_mode TEXT NOT NULL DEFAULT '',
            mode_streak INTEGER NOT NULL DEFAULT 0,
            pressure_smoothed REAL NOT NULL DEFAULT 0.0,
            updated_ts REAL NOT NULL
        );
        """
        )
        _log_active_stack_migration(apply_active_stack_migrations_to_connection(con))

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS psyche_v2_events (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            delta_json TEXT NOT NULL,
            reason_text TEXT NOT NULL,
            source_ref TEXT,
            created_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_psychev2_events_user "
            "ON psyche_v2_events(user_id, created_ts DESC);"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS psyche_v2_behavior_rules (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            rule_name TEXT NOT NULL,
            trigger_json TEXT NOT NULL,
            behavior_adjustment_json TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_psychev2_rules_user "
            "ON psyche_v2_behavior_rules(user_id, priority DESC, is_active) WHERE is_active=1;"
        )

        cur.execute(
            """
        CREATE TABLE IF NOT EXISTS psyche_v2_habits (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            habit_name TEXT NOT NULL,
            habit_type TEXT NOT NULL,
            intensity REAL NOT NULL DEFAULT 0.0,
            reinforcement_count INTEGER NOT NULL DEFAULT 0,
            last_reinforced_ts REAL NOT NULL,
            context_json TEXT NOT NULL,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_psychev2_habits_user "
            "ON psyche_v2_habits(user_id, intensity DESC, last_reinforced_ts DESC);"
        )

        _log_active_stack_migration(apply_active_stack_migrations_to_connection(con))
        con.commit()

    # Turn execution / idempotency / outbox (always ensure after core schema)
    try:
        from aihub.turn.idempotency import ensure_turn_schema
        from aihub.turn.concurrency import ensure_lock_schema
        from aihub.durable_jobs import ensure_schema as ensure_durable_jobs_schema

        ensure_turn_schema()
        ensure_lock_schema()
        ensure_durable_jobs_schema()
    except Exception:
        logger.exception("turn/durable schema ensure failed")

    try:
        from aihub.adaptive_learning.schema import ensure_adaptive_learning_schema

        ensure_adaptive_learning_schema()
    except Exception:
        logger.exception("adaptive learning schema ensure failed")

    try:
        from aihub.world_knowledge.schema import ensure_world_knowledge_schema

        ensure_world_knowledge_schema()
    except Exception:
        logger.exception("world knowledge schema ensure failed")


def now_ts() -> float:
    return time.time()


def json_dumps(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, separators=(",", ":"))


def json_loads(s: str) -> Any:
    return json.loads(s) if s else None


def exec_one(sql: str, params: tuple[Any, ...] = ()) -> None:
    with _DB_LOCK, _conn() as con:
        con.execute(sql, params)
        con.commit()


def exec_one_rowcount(sql: str, params: tuple[Any, ...] = ()) -> int:
    """Execute one atomic write and return the affected-row count."""
    with _DB_LOCK, _conn() as con:
        result = con.execute(sql, params)
        con.commit()
        return int(result.rowcount or 0)


def fetch_all(sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    with _DB_LOCK, _conn() as con:
        return con.execute(sql, params).fetchall()


def fetch_one(sql: str, params: tuple[Any, ...] = ()) -> Any:
    with _DB_LOCK, _conn() as con:
        return con.execute(sql, params).fetchone()


def upsert_psyche(
    user_id: str,
    mood: float,
    energy: float,
    focus: float,
    style: str,
    temperature: float,
    traits: dict[str, Any],
) -> None:
    ts = now_ts()
    exec_one(
        """
    INSERT INTO psyche_state(user_id, mood, energy, focus, style, temperature, traits, updated_at)
    VALUES(?,?,?,?,?,?,?,?)
    ON CONFLICT(user_id) DO UPDATE SET
      mood=excluded.mood,
      energy=excluded.energy,
      focus=excluded.focus,
      style=excluded.style,
      temperature=excluded.temperature,
      traits=excluded.traits,
      updated_at=excluded.updated_at
    """,
        (user_id, mood, energy, focus, style, temperature, json_dumps(traits), ts),
    )


def get_psyche(user_id: str) -> dict[str, Any] | None:
    row = fetch_one("SELECT * FROM psyche_state WHERE user_id=?", (user_id,))
    if not row:
        return None
    return {
        "user_id": row["user_id"],
        "mood": float(row["mood"]),
        "energy": float(row["energy"]),
        "focus": float(row["focus"]),
        "style": row["style"],
        "temperature": float(row["temperature"]),
        "traits": json_loads(row["traits"]) or {},
        "updated_at": float(row["updated_at"]),
    }


def append_event(user_id: str, typ: str, data: dict[str, Any]) -> int:
    ts = now_ts()
    with _DB_LOCK, _conn() as con:
        if isinstance(con, PgConnectionWrapper):
            cur = con.execute(
                "INSERT INTO event_log(user_id, type, data, ts) VALUES(?,?,?,?) "
                "RETURNING id",
                (user_id, typ, json_dumps(data), ts),
            )
            row = cur.fetchone()
            con.commit()
            return int(row["id"]) if row and "id" in row else 0
        cur = con.execute(
            "INSERT INTO event_log(user_id, type, data, ts) VALUES(?,?,?,?)",
            (user_id, typ, json_dumps(data), ts),
        )
        con.commit()
        return int(cur.lastrowid or 0)


def fetch_recent_events_by_type(
    user_id: str, typ: str, limit: int = 40
) -> list[dict[str, Any]]:
    """Najnowsze zdarzenia danego typu (id malejąco)."""
    rows = fetch_all(
        "SELECT id, type, data, ts FROM event_log "
        "WHERE user_id=? AND type=? ORDER BY id DESC LIMIT ?",
        (user_id, typ, int(limit)),
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "type": r["type"],
                "data": json_loads(r["data"]) or {},
                "ts": float(r["ts"]),
            }
        )
    return out


def get_events_since(
    user_id: str, last_id: int, limit: int = 200
) -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT id, type, data, ts FROM event_log WHERE user_id=? AND id>? ORDER BY id ASC LIMIT ?",
        (user_id, last_id, limit),
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": int(r["id"]),
                "type": r["type"],
                "data": json_loads(r["data"]) or {},
                "ts": float(r["ts"]),
            }
        )
    return out


def insert_stm_message(
    msg_id: str, user_id: str, role: str, content: str, meta: dict[str, Any]
) -> None:
    exec_one(
        "INSERT INTO stm_messages(id, user_id, role, content, meta, ts) VALUES(?,?,?,?,?,?)",
        (msg_id, user_id, role, content, json_dumps(meta), now_ts()),
    )


def get_stm(user_id: str, limit: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT id, role, content, meta, ts FROM stm_messages WHERE user_id=? ORDER BY ts DESC LIMIT ?",
        (user_id, limit),
    )
    rows.reverse()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "meta": json_loads(r["meta"]) or {},
                "ts": float(r["ts"]),
            }
        )
    return out


def prune_stm(user_id: str, keep: int) -> int:
    with _DB_LOCK, _conn() as con:
        if isinstance(con, PgConnectionWrapper):
            cur = con.execute(
                """
            DELETE FROM stm_messages
            WHERE id IN (
                SELECT id FROM stm_messages WHERE user_id=? ORDER BY ts DESC OFFSET ?
            )
            """,
                (user_id, keep),
            )
        else:
            cur = con.execute(
                """
            DELETE FROM stm_messages
            WHERE id IN (
                SELECT id FROM stm_messages WHERE user_id=? ORDER BY ts DESC LIMIT -1 OFFSET ?
            )
            """,
                (user_id, keep),
            )
        con.commit()
        return cur.rowcount


def upsert_node(
    node_id: str,
    user_id: str,
    layer: str,
    content: str,
    tags: list[str],
    meta: dict[str, Any],
    ts: float,
    importance: float,
    confidence: float,
) -> None:
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
        INSERT INTO memory_nodes(id, user_id, layer, content, tags, meta, ts, importance, confidence, deleted)
        VALUES(?,?,?,?,?,?,?,?,?,0)
        ON CONFLICT(id) DO UPDATE SET
          content=excluded.content,
          tags=excluded.tags,
          meta=excluded.meta,
          ts=excluded.ts,
          importance=excluded.importance,
          confidence=excluded.confidence,
          deleted=0
        """,
            (
                node_id,
                user_id,
                layer,
                content,
                json_dumps(tags),
                json_dumps(meta),
                ts,
                importance,
                confidence,
            ),
        )

        if isinstance(con, PgConnectionWrapper):
            con.execute(
                """
            INSERT INTO memory_fts(node_id, content, user_id, layer)
            VALUES(?,?,?,?)
            ON CONFLICT(node_id) DO UPDATE SET
              content=excluded.content,
              user_id=excluded.user_id,
              layer=excluded.layer
            """,
                (node_id, content, user_id, layer),
            )
        else:
            try:
                con.execute(
                    """
                INSERT OR REPLACE INTO memory_fts(rowid, content, user_id, layer, node_id)
                VALUES(
                  (SELECT rowid FROM memory_fts WHERE node_id=?),
                  ?,?,?,?
                )
                """,
                    (node_id, content, user_id, layer, node_id),
                )
            except sqlite3.OperationalError as exc:
                logger.debug("SQLite FTS update skipped; FTS5 unavailable: %s", exc)

        con.commit()


def search_nodes_fts(
    user_id: str, layer: str, query: str, limit: int
) -> list[dict[str, Any]]:
    # PostgreSQL nie ma FTS5 (to jest SQLite); odpowiednik: tsvector + GIN (bootstrap: content_tsv).
    if _db_backend() == "postgres":
        q = (query or "").strip()
        if not q:
            return []

        def _pg_fts(expr: str) -> list[Any]:
            return fetch_all(
                """
            SELECT mn.id, mn.layer, mn.content, mn.tags, mn.meta, mn.ts, mn.importance, mn.confidence,
                   ts_rank_cd(mf.content_tsv, plainto_tsquery('simple', %s)) AS rank
            FROM memory_fts mf
            INNER JOIN memory_nodes mn ON mn.id = mf.node_id
            WHERE mf.user_id = %s AND mf.layer = %s AND mn.deleted = 0
              AND mf.content_tsv @@ plainto_tsquery('simple', %s)
            ORDER BY rank DESC
            LIMIT %s
            """,
                (expr, user_id, layer, expr, limit),
            )

        def _pg_like_tokens(tokens: list[str]) -> list[Any]:
            if not tokens:
                return []
            # Prefer distinctive tokens (IDs, nouns) over stopwords.
            clauses = []
            params: list[Any] = [user_id, layer]
            for tok in tokens[:6]:
                clauses.append("content ILIKE %s")
                params.append(f"%{tok}%")
            params.append(limit)
            sql = f"""
            SELECT id, layer, content, tags, meta, ts, importance, confidence,
                   0.0 AS rank
            FROM memory_nodes
            WHERE user_id=%s AND layer=%s AND deleted=0
              AND ({' OR '.join(clauses)})
            ORDER BY importance DESC, confidence DESC, ts DESC
            LIMIT %s
            """
            return fetch_all(sql, tuple(params))

        # plainto_tsquery ANDs all tokens — Polish question wrappers ("Czego…?")
        # zero out hits when stopwords are absent from stored facts.
        stop = {
            "czego",
            "jaki",
            "jaka",
            "jakie",
            "jak",
            "czy",
            "ile",
            "gdzie",
            "kiedy",
            "ktory",
            "który",
            "ktora",
            "która",
            "the",
            "what",
            "who",
            "how",
            "does",
            "did",
            "is",
            "are",
            "nie",
            "lub",
            "oraz",
            "dla",
            "do",
            "na",
            "po",
            "od",
            "za",
            "to",
            "ten",
            "ta",
            "te",
        }
        raw_tokens = re.findall(r"[A-Za-z0-9ĄąĆćĘęŁłŃńÓóŚśŹźŻż_-]{3,}", q)
        distinctive = [
            t
            for t in raw_tokens
            if t.lower() not in stop and not t.isdigit()
        ]
        variants: list[str] = [q]
        if distinctive:
            # OR-ish: plainto still ANDs; use space-joined distinctive only.
            variants.append(" ".join(distinctive[:8]))
            # Also try the single most specific token (IDs / proper nouns).
            distinctive_sorted = sorted(distinctive, key=len, reverse=True)
            variants.append(distinctive_sorted[0])

        rows: list[Any] = []
        seen_q: set[str] = set()
        try:
            for variant in variants:
                key = variant.strip().lower()
                if not key or key in seen_q:
                    continue
                seen_q.add(key)
                cand = _pg_fts(variant)
                # Prefer hits that look like declarative facts, not question echoes.
                useful = [
                    r
                    for r in cand
                    if not str(r.get("content") or "").strip().endswith("?")
                ]
                rows = useful or cand
                if useful:
                    break
            if not rows and distinctive:
                rows = _pg_like_tokens(distinctive)
            elif distinctive:
                # Merge lexical hits so a question-shaped FTS hit cannot hide real facts.
                like_rows = _pg_like_tokens(distinctive)
                seen_ids = {str(r.get("id")) for r in rows}
                for r in like_rows:
                    rid = str(r.get("id"))
                    if rid and rid not in seen_ids:
                        rows.append(r)
                        seen_ids.add(rid)
        except Exception:
            logger.exception("search_nodes_fts postgres FTS failed; falling back to LIKE")
            rows = _pg_like_tokens(distinctive or raw_tokens or [q[:80]])
    else:
        from aihub.db.fts5_query import build_fts5_match_query, build_lexical_like_pattern

        q = (query or "").strip()
        fts_expr = build_fts5_match_query(q).expression
        try:
            rows = fetch_all(
                """
            SELECT mn.id, mn.layer, mn.content, mn.tags, mn.meta, mn.ts, mn.importance, mn.confidence, bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memory_nodes mn ON mn.id = memory_fts.node_id
            WHERE memory_fts MATCH ? AND mn.user_id=? AND mn.layer=? AND mn.deleted=0
            ORDER BY rank ASC
            LIMIT ?
            """,
                (fts_expr, user_id, layer, limit),
            )
        except sqlite3.OperationalError:
            logger.debug(
                "search_nodes_fts sqlite FTS failed for %r; using LIKE fallback",
                q,
                exc_info=True,
            )
            like_pat = build_lexical_like_pattern(q)
            rows = fetch_all(
                """
            SELECT id, layer, content, tags, meta, ts, importance, confidence,
                   0.0 AS rank
            FROM memory_nodes
            WHERE user_id=? AND layer=? AND deleted=0 AND content LIKE ? ESCAPE '\\'
            ORDER BY importance DESC, confidence DESC, ts DESC
            LIMIT ?
            """,
                (user_id, layer, like_pat, limit),
            )

    out = []
    for r in rows:
        rank = float(r["rank"]) if "rank" in r.keys() else 0.0
        out.append(
            {
                "id": r["id"],
                "layer": r["layer"],
                "content": r["content"],
                "tags": json_loads(r["tags"]) or [],
                "meta": json_loads(r["meta"]) or {},
                "ts": float(r["ts"]),
                "importance": float(r["importance"]),
                "confidence": float(r["confidence"]),
                "rank": rank,
            }
        )
    return out


def list_recent_nodes(user_id: str, layer: str, limit: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        """
    SELECT id, layer, content, tags, meta, ts, importance, confidence
    FROM memory_nodes
    WHERE user_id=? AND layer=? AND deleted=0
    ORDER BY ts DESC
    LIMIT ?
    """,
        (user_id, layer, limit),
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "layer": r["layer"],
                "content": r["content"],
                "tags": json_loads(r["tags"]) or [],
                "meta": json_loads(r["meta"]) or {},
                "ts": float(r["ts"]),
                "importance": float(r["importance"]),
                "confidence": float(r["confidence"]),
            }
        )
    return out


def soft_delete_node(node_id: str) -> None:
    exec_one("UPDATE memory_nodes SET deleted=1 WHERE id=?", (node_id,))


_STRATEGY_DECISION_BIAS_KEYS: tuple[str, str, str, str] = (
    "instant",
    "contextual",
    "research",
    "agentic",
)


def default_strategy_decision_bias() -> dict[str, float]:
    return {k: 0.0 for k in _STRATEGY_DECISION_BIAS_KEYS}


def get_strategy_decision_bias(user_id: str) -> dict[str, float]:
    uid = (user_id or "").strip()
    if not uid:
        return default_strategy_decision_bias()
    row = fetch_one(
        "SELECT bias_instant, bias_contextual, bias_research, bias_agentic "
        "FROM strategy_decision_bias WHERE user_id=?",
        (uid,),
    )
    if not row:
        return default_strategy_decision_bias()
    return {
        "instant": float(row["bias_instant"] or 0.0),
        "contextual": float(row["bias_contextual"] or 0.0),
        "research": float(row["bias_research"] or 0.0),
        "agentic": float(row["bias_agentic"] or 0.0),
    }


def user_has_persisted_strategy_bias(user_id: str) -> bool:
    uid = (user_id or "").strip()
    if not uid:
        return False
    row = fetch_one(
        "SELECT 1 AS ok FROM strategy_decision_bias WHERE user_id=? LIMIT 1",
        (uid,),
    )
    return row is not None


def get_strategy_decision_bias_updated_at(user_id: str) -> float | None:
    uid = (user_id or "").strip()
    if not uid:
        return None
    row = fetch_one(
        "SELECT updated_at FROM strategy_decision_bias WHERE user_id=?",
        (uid,),
    )
    if not row:
        return None
    return float(row["updated_at"])


def save_strategy_decision_bias(
    user_id: str,
    bias: dict[str, Any],
    metrics_snapshot: dict[str, Any] | None = None,
) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    snap = json_dumps(metrics_snapshot if metrics_snapshot is not None else {})
    ts = now_ts()
    bi = float(bias.get("instant", 0.0) or 0.0)
    bc = float(bias.get("contextual", 0.0) or 0.0)
    br = float(bias.get("research", 0.0) or 0.0)
    ba = float(bias.get("agentic", 0.0) or 0.0)
    exec_one(
        """
        INSERT INTO strategy_decision_bias (
            user_id, bias_instant, bias_contextual, bias_research, bias_agentic,
            updated_at, metrics_snapshot
        ) VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            bias_instant=excluded.bias_instant,
            bias_contextual=excluded.bias_contextual,
            bias_research=excluded.bias_research,
            bias_agentic=excluded.bias_agentic,
            updated_at=excluded.updated_at,
            metrics_snapshot=excluded.metrics_snapshot
        """,
        (uid, bi, bc, br, ba, ts, snap),
    )


def reset_strategy_decision_bias(user_id: str) -> None:
    uid = (user_id or "").strip()
    if not uid:
        return
    exec_one("DELETE FROM strategy_decision_bias WHERE user_id=?", (uid,))


def reset_all_strategy_decision_bias() -> None:
    exec_one("DELETE FROM strategy_decision_bias")


def write_experience(
    experience_id: str,
    user_id: str,
    user_input_summary: str,
    selected_strategy: str,
    reason_codes: list[str],
    tools_needed: bool,
    tools_executed: bool,
    research_needed: bool,
    research_executed: bool,
    planner_recommended: bool,
    planner_executed: bool,
    agentic_recommended: bool,
    agentic_executed: bool,
    outcome_type: str,
    success: bool,
    failure_type: str | None = None,
    fallback_flag: bool = False,
    degraded_flag: bool = False,
    latency_ms: float | None = None,
    content_hash: str = "",
    embedding_provider: str = "unknown",
    embedding_model: str = "unknown",
    embedding_dimension: int = 0,
    embedding_input_type: str = "document",
    semantic_embedding: list[float] | None = None,
    short_lesson_learned: str = "",
    reflection_seed: str = "",
    session_id: str | None = None,
    trace_id: str | None = None,
    goal_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    """
    Write experience to database with all metadata.

    Returns True if written, False if skipped (duplicate or error).
    """
    try:
        if content_hash:
            existing = fetch_one(
                "SELECT experience_id FROM experiences WHERE content_hash=? AND selected_strategy=? AND deleted=0 LIMIT 1",
                (content_hash, selected_strategy),
            )
            if existing:
                logger.debug(
                    "Skipping duplicate experience: hash=%s strategy=%s",
                    content_hash,
                    selected_strategy,
                )
                return False

        ts = now_ts()
        meta_dict = dict(metadata or {})

        embedding_json = None
        if semantic_embedding is not None:
            try:
                embedding_json = json_dumps(semantic_embedding)
            except (ValueError, TypeError) as exc:
                logger.warning("Failed to serialize embedding: %s", exc)

        exec_one(
            """
        INSERT INTO experiences(
            experience_id, user_id, session_id, trace_id, goal_id,
            created_at, user_input_summary, selected_strategy, reason_codes,
            tools_needed, tools_executed,
            research_needed, research_executed,
            planner_recommended, planner_executed,
            agentic_recommended, agentic_executed,
            outcome_type, success, failure_type,
            fallback_flag, degraded_flag, latency_ms,
            content_hash, embedding_provider, embedding_model,
            embedding_dimension, embedding_input_type, semantic_embedding,
            short_lesson_learned, reflection_seed, metadata
        ) VALUES (
            ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
        )
        """,
            (
                experience_id,
                user_id,
                session_id,
                trace_id,
                goal_id,
                ts,
                user_input_summary,
                selected_strategy,
                json_dumps(reason_codes),
                int(tools_needed),
                int(tools_executed),
                int(research_needed),
                int(research_executed),
                int(planner_recommended),
                int(planner_executed),
                int(agentic_recommended),
                int(agentic_executed),
                outcome_type,
                int(success),
                failure_type or "",
                int(fallback_flag),
                int(degraded_flag),
                latency_ms,
                content_hash,
                embedding_provider,
                embedding_model,
                embedding_dimension,
                embedding_input_type,
                embedding_json,
                short_lesson_learned,
                reflection_seed,
                json_dumps(meta_dict),
            ),
        )
        logger.debug(
            "Experience written: id=%s user=%s strategy=%s",
            experience_id,
            user_id,
            selected_strategy,
        )
        return True

    except (sqlite3.Error, OSError) as exc:
        logger.error("Failed to write experience: %s", exc)
        return False


def get_experiences_by_session(
    session_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Retrieve experiences for a session."""
    rows = fetch_all(
        """
    SELECT * FROM experiences
    WHERE session_id=? AND deleted=0
    ORDER BY created_at DESC
    LIMIT ?
    """,
        (session_id, limit),
    )
    return [_row_to_experience_dict(r) for r in rows]


def get_experiences_by_trace(
    trace_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Retrieve experiences for a trace."""
    rows = fetch_all(
        """
    SELECT * FROM experiences
    WHERE trace_id=? AND deleted=0
    ORDER BY created_at DESC
    LIMIT ?
    """,
        (trace_id, limit),
    )
    return [_row_to_experience_dict(r) for r in rows]


def get_experiences_by_user(
    user_id: str,
    limit: int = 100,
    strategy_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve recent experiences for a user, optionally filtered by strategy."""
    if strategy_filter:
        rows = fetch_all(
            """
        SELECT * FROM experiences
        WHERE user_id=? AND selected_strategy=? AND deleted=0
        ORDER BY created_at DESC
        LIMIT ?
        """,
            (user_id, strategy_filter, limit),
        )
    else:
        rows = fetch_all(
            """
        SELECT * FROM experiences
        WHERE user_id=? AND deleted=0
        ORDER BY created_at DESC
        LIMIT ?
        """,
            (user_id, limit),
        )
    return [_row_to_experience_dict(r) for r in rows]


def _row_to_experience_dict(row: sqlite3.Row) -> dict[str, Any]:
    """Convert SQLite row to experience dictionary."""
    embedding_vec = None
    if row["semantic_embedding"]:
        try:
            embedding_vec = json_loads(row["semantic_embedding"])
        except (ValueError, TypeError):
            embedding_vec = None

    latency_raw = row["latency_ms"]
    latency_value = float(latency_raw) if latency_raw is not None else None

    return {
        "experience_id": row["experience_id"],
        "user_id": row["user_id"],
        "session_id": row["session_id"],
        "trace_id": row["trace_id"],
        "goal_id": row["goal_id"],
        "created_at": float(row["created_at"]),
        "user_input_summary": row["user_input_summary"],
        "selected_strategy": row["selected_strategy"],
        "reason_codes": json_loads(row["reason_codes"]) or [],
        "tools_needed": bool(row["tools_needed"]),
        "tools_executed": bool(row["tools_executed"]),
        "research_needed": bool(row["research_needed"]),
        "research_executed": bool(row["research_executed"]),
        "planner_recommended": bool(row["planner_recommended"]),
        "planner_executed": bool(row["planner_executed"]),
        "agentic_recommended": bool(row["agentic_recommended"]),
        "agentic_executed": bool(row["agentic_executed"]),
        "outcome_type": row["outcome_type"],
        "success": bool(row["success"]),
        "failure_type": row["failure_type"],
        "fallback_flag": bool(row["fallback_flag"]),
        "degraded_flag": bool(row["degraded_flag"]),
        "latency_ms": latency_value,
        "content_hash": row["content_hash"],
        "embedding_provider": row["embedding_provider"],
        "embedding_model": row["embedding_model"],
        "embedding_dimension": int(row["embedding_dimension"]),
        "embedding_input_type": row["embedding_input_type"],
        "semantic_embedding": embedding_vec,
        "short_lesson_learned": row["short_lesson_learned"],
        "reflection_seed": row["reflection_seed"],
        "metadata": json_loads(row["metadata"]) or {},
    }


def ensure_chat_session_row(user_id: str, session_id: str) -> None:
    """Upewnij się, że wiersz ``chat_sessions`` istnieje (np. przed zapisem transkryptu)."""
    uid = (user_id or "").strip() or "default"
    sid = (session_id or "").strip() or "default"
    ts = now_ts()
    row = fetch_one(
        "SELECT id FROM chat_sessions WHERE user_id=? AND id=?",
        (uid, sid),
    )
    if row:
        return
    exec_one(
        """
        INSERT INTO chat_sessions(user_id, id, title, created_at, updated_at, archived, archived_at)
        VALUES(?,?,?,?,?,0,NULL)
        """,
        (uid, sid, "Nowa rozmowa", ts, ts),
    )


def insert_chat_session_message_pair(
    user_id: str,
    session_id: str,
    user_content: str,
    assistant_content: str,
    *,
    user_meta: dict[str, Any] | None = None,
) -> None:
    """Atomowo: wiadomość user + odpowiedź asystenta (kolejność chronologiczna)."""
    import time as _time
    import uuid

    uid = (user_id or "").strip() or "default"
    sid = (session_id or "").strip() or "default"
    base = _time.time()
    u_msg_id = str(uuid.uuid4())
    a_msg_id = str(uuid.uuid4())
    u_meta = json_dumps(user_meta if user_meta else {})
    exec_one(
        """
        INSERT INTO chat_session_messages(
            id, user_id, session_id, role, content, created_at, meta
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (u_msg_id, uid, sid, "user", user_content, base, u_meta),
    )
    exec_one(
        """
        INSERT INTO chat_session_messages(
            id, user_id, session_id, role, content, created_at, meta
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (a_msg_id, uid, sid, "assistant", assistant_content, base + 1e-6, "{}"),
    )
    ts = now_ts()
    exec_one(
        "UPDATE chat_sessions SET updated_at=? WHERE user_id=? AND id=?",
        (ts, uid, sid),
    )


def fetch_chat_session_messages(
    user_id: str,
    session_id: str,
    *,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    uid = (user_id or "").strip() or "default"
    sid = (session_id or "").strip() or "default"
    rows = fetch_all(
        """
        SELECT id, role, content, created_at, meta FROM chat_session_messages
        WHERE user_id=? AND session_id=?
        ORDER BY created_at ASC, id ASC
        LIMIT ?
        """,
        (uid, sid, int(limit)),
    )
    return [dict(r) for r in rows]


def delete_chat_session_messages(user_id: str, session_id: str) -> None:
    uid = (user_id or "").strip() or "default"
    sid = (session_id or "").strip() or "default"
    exec_one(
        "DELETE FROM chat_session_messages WHERE user_id=? AND session_id=?",
        (uid, sid),
    )
