#!/usr/bin/env python3
"""Isolated PostgreSQL integration test for Memory V2 reinforcement."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    load_dotenv(root / ".env")
    dsn = (os.getenv("POSTGRES_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN is required for the PostgreSQL integration test")

    import psycopg2
    from psycopg2 import sql
    from psycopg2.extensions import make_dsn, parse_dsn

    schema = f"aihub_it_{uuid.uuid4().hex}"
    admin = psycopg2.connect(dsn)
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        admin.commit()

        params = parse_dsn(dsn)
        existing_options = str(params.get("options") or "").strip()
        params["options"] = (
            f"{existing_options} -c search_path={schema}".strip()
        )
        isolated_dsn = make_dsn(**params)
        os.environ["DB_BACKEND"] = "postgres"
        os.environ["POSTGRES_DSN"] = isolated_dsn

        from aihub import db
        from aihub.memory_v2_repository import (
            get_reinforced_memories,
            reinforce_memory_item,
        )
        from aihub.memory_v2_service import MemoryV2Service

        db.dispose_sqlite_engine()
        db.init_db()

        user_id = f"pg-reinforcement-{uuid.uuid4().hex}"
        item = MemoryV2Service().create_memory_item(
            user_id=user_id,
            memory_type="procedural",
            scope="workflow",
            title="PostgreSQL reinforcement integration",
            content="A test record isolated in a temporary schema",
            source_kind="agent_cycle",
            importance_score=0.6,
        )

        if reinforce_memory_item(item.id, "different-owner", success=True):
            raise AssertionError("Cross-owner reinforcement unexpectedly succeeded")
        if not reinforce_memory_item(
            item.id,
            user_id,
            success=True,
            recurrence_boost=0.8,
            salience_boost=0.8,
        ):
            raise AssertionError("First PostgreSQL reinforcement failed")
        if not reinforce_memory_item(
            item.id,
            user_id,
            success=False,
            recurrence_boost=0.8,
            salience_boost=0.8,
        ):
            raise AssertionError("Second PostgreSQL reinforcement failed")

        rows = get_reinforced_memories(user_id, min_reinforcements=2, limit=5)
        if len(rows) != 1:
            raise AssertionError(f"Expected one reinforced row, got {len(rows)}")
        row = rows[0]
        if (
            row.reinforcement_count != 2
            or row.success_reinforcements != 1
            or row.failure_reinforcements != 1
            or row.recurrence_score != 1.0
            or row.salience_score != 1.0
        ):
            raise AssertionError("PostgreSQL reinforcement state is inconsistent")

        print("POSTGRES_REINFORCEMENT_TEST: OK")
        return 0
    finally:
        try:
            from aihub import db

            db.dispose_sqlite_engine()
        except ImportError:
            pass
        admin.rollback()
        with admin.cursor() as cur:
            cur.execute(
                sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                    sql.Identifier(schema)
                )
            )
        admin.commit()
        admin.close()


if __name__ == "__main__":
    raise SystemExit(main())
