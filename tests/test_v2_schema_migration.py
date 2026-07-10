#!/usr/bin/env python3
"""SQLite active-stack migrations: legacy schema → full V2 (idempotent)."""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient


def _seed_legacy_v2_core(db_path) -> None:
    """Minimal pre-depth schema: missing most Memory/Psyche V2 columns."""
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE memory_v2_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            importance_score REAL NOT NULL DEFAULT 0.0,
            salience_score REAL NOT NULL DEFAULT 0.72,
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE psyche_v2_profile (
            user_id TEXT PRIMARY KEY,
            relation_trust REAL NOT NULL DEFAULT 0.62,
            relation_sync REAL NOT NULL DEFAULT 0.71,
            updated_ts REAL NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE psyche_v2_state (
            user_id TEXT PRIMARY KEY,
            mood REAL NOT NULL DEFAULT 0.5,
            pressure REAL NOT NULL DEFAULT 0.33,
            updated_ts REAL NOT NULL
        );
        """
    )
    con.commit()
    con.close()


def _finish_agent_and_kg() -> None:
    import aihub.agent_db as agent_db_mod
    import aihub.knowledge_graph as kg_mod

    agent_db_mod.ensure_schema()
    kg_mod._graph.nodes.clear()
    kg_mod._graph.edges.clear()
    kg_mod._graph.relation_index.clear()


@pytest.mark.legacy_sqlite_v2
def test_init_db_migrates_legacy_memory_and_psyche_columns(isolated_db):
    db_file = isolated_db
    _seed_legacy_v2_core(db_file)

    import aihub.db as db_mod

    db_mod.init_db()
    _finish_agent_and_kg()

    health = db_mod.get_active_stack_schema_health()
    assert health.get("backend") == "sqlite"
    assert health["ok"] is True
    assert health["missing_tables"] == []
    assert health["missing_columns"] == []

    with db_mod._conn() as con:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(memory_v2_items)")
        names = {r[1] for r in cur.fetchall()}
        assert "retrieval_priority_score" in names
        assert "stability_tier" in names
        assert "is_suppressed" in names

        cur.execute("PRAGMA table_info(psyche_v2_profile)")
        pcols = {r[1] for r in cur.fetchall()}
        assert "relation_interaction_quality_ema" in pcols
        assert "relation_friction" in pcols

        cur.execute("PRAGMA table_info(psyche_v2_state)")
        scols = {r[1] for r in cur.fetchall()}
        assert "pending_mode" in scols
        assert "pressure_smoothed" in scols


@pytest.mark.legacy_sqlite_v2
def test_migration_preserves_row_data(isolated_db):
    db_file = isolated_db
    _seed_legacy_v2_core(db_file)
    con = sqlite3.connect(str(db_file))
    con.execute(
        """
        INSERT INTO memory_v2_items (
            id, user_id, memory_type, scope, title, content, summary,
            source_kind, importance_score, salience_score, created_ts, updated_ts
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "mem-legacy-1",
            "u-legacy",
            "fact",
            "user",
            "T",
            "body",
            "sum",
            "chat_turn",
            0.4,
            0.55,
            1.0,
            2.0,
        ),
    )
    con.execute(
        "INSERT INTO psyche_v2_profile (user_id, relation_trust, relation_sync, updated_ts) "
        "VALUES (?,?,?,?)",
        ("u-legacy", 0.62, 0.71, 3.0),
    )
    con.execute(
        "INSERT INTO psyche_v2_state (user_id, mood, pressure, updated_ts) VALUES (?,?,?,?)",
        ("u-legacy", 0.44, 0.33, 4.0),
    )
    con.commit()
    con.close()

    import aihub.db as db_mod

    db_mod.init_db()
    _finish_agent_and_kg()

    with db_mod._conn() as con:
        cur = con.cursor()
        cur.execute(
            "SELECT content, salience_score FROM memory_v2_items WHERE id=?",
            ("mem-legacy-1",),
        )
        row = cur.fetchone()
        assert row[0] == "body"
        assert abs(row[1] - 0.55) < 1e-6

        cur.execute(
            "SELECT relation_trust, relation_sync FROM psyche_v2_profile WHERE user_id=?",
            ("u-legacy",),
        )
        pr = cur.fetchone()
        assert abs(pr[0] - 0.62) < 1e-6
        assert abs(pr[1] - 0.71) < 1e-6

        cur.execute(
            "SELECT mood, pressure FROM psyche_v2_state WHERE user_id=?",
            ("u-legacy",),
        )
        sr = cur.fetchone()
        assert abs(sr[0] - 0.44) < 1e-6
        assert abs(sr[1] - 0.33) < 1e-6


@pytest.mark.legacy_sqlite_v2
def test_apply_active_stack_migrations_idempotent(isolated_db):
    db_file = isolated_db
    _seed_legacy_v2_core(db_file)

    import aihub.db as db_mod

    db_mod.init_db()
    _finish_agent_and_kg()

    with db_mod._conn() as con:
        r2 = db_mod.apply_active_stack_migrations_to_connection(con)
        r3 = db_mod.apply_active_stack_migrations_to_connection(con)

    assert r2.get("columns_added") in (None, [])
    assert r3.get("columns_added") in (None, [])
    assert r2.get("indexes_ensured") in (None, [])
    assert r3.get("indexes_ensured") in (None, [])

    h = db_mod.get_active_stack_schema_health()
    assert h["ok"] is True


def test_fresh_db_schema_health_ok(isolated_db):
    import aihub.db as db_mod

    h = db_mod.get_active_stack_schema_health()
    assert h["ok"] is True


@pytest.mark.legacy_sqlite_v2
def test_partial_then_full_migrate_simulation(isolated_db):
    """Simulate half-upgraded DB: memory has new cols, profile missing EMA."""
    import aihub.db as db_mod

    db_path = isolated_db
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE memory_v2_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            scope TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            importance_score REAL NOT NULL DEFAULT 0.0,
            salience_score REAL NOT NULL DEFAULT 0.5,
            retrieval_priority_score REAL NOT NULL DEFAULT 0.1,
            contradiction_state TEXT NOT NULL DEFAULT 'none',
            is_archived INTEGER NOT NULL DEFAULT 0,
            is_suppressed INTEGER NOT NULL DEFAULT 0,
            decay_bucket TEXT NOT NULL DEFAULT 'active',
            stability_tier TEXT NOT NULL DEFAULT 'transient',
            created_ts REAL NOT NULL,
            updated_ts REAL NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE psyche_v2_profile (
            user_id TEXT PRIMARY KEY,
            relation_trust REAL NOT NULL DEFAULT 0.5,
            relation_sync REAL NOT NULL DEFAULT 0.5,
            relation_friction REAL NOT NULL DEFAULT 0.0,
            updated_ts REAL NOT NULL
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE psyche_v2_state (
            user_id TEXT PRIMARY KEY,
            mood REAL NOT NULL DEFAULT 0.5,
            updated_ts REAL NOT NULL
        );
        """
    )
    con.commit()
    con.close()

    db_mod.init_db()
    _finish_agent_and_kg()

    h = db_mod.get_active_stack_schema_health()
    assert h["ok"] is True
    with db_mod._conn() as con:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(psyche_v2_profile)")
        assert "relation_interaction_quality_ema" in {r[1] for r in cur.fetchall()}


@pytest.fixture(autouse=True)
def _disable_auth_for_http(monkeypatch):
    from aihub import main

    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setattr(main, "start_worker_once", lambda: None)


@pytest.mark.legacy_sqlite_v2
def test_http_endpoints_after_legacy_migrate(isolated_db):
    db_file = isolated_db
    _seed_legacy_v2_core(db_file)

    import aihub.db as db_mod

    db_mod.init_db()
    _finish_agent_and_kg()

    from aihub.main import app

    user_id = "legacy-http-user"
    with TestClient(app) as client:
        sh = client.get("/cockpit/schema-health")
        assert sh.status_code == 200
        body = sh.json()
        assert body.get("ok") is True

        from aihub.psyche_engine import ensure_user

        ensure_user(user_id)

        snap = client.get(f"/psyche/v2/{user_id}")
        assert snap.status_code == 200

        mv = client.get(f"/memory/v2/summary/{user_id}")
        assert mv.status_code == 200

        chat = client.post(
            "/chat/turn",
            json={
                "user_id": user_id,
                "session_id": "s-mig",
                "message": "ping migracji sqlite",
                "mode": "chat",
            },
        )
        assert chat.status_code == 200

        ag = client.post(
            "/agent/run",
            json={
                "user_id": user_id,
                "text": "echo: migracja",
            },
        )
        assert ag.status_code == 200
