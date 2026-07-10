"""Regresja: psyche_rules_like ignoruje reguły heal (tylko endpoint / NULL kind)."""

import sqlite3
import tempfile
from pathlib import Path


def test_psyche_rules_like_excludes_non_endpoint_kind(monkeypatch):
    from aihub import sidecar_db

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "psyche.db"
        with sqlite3.connect(p) as conn:
            conn.execute(
                """
                CREATE TABLE rules (
                    id TEXT PRIMARY KEY,
                    ts INTEGER,
                    kind TEXT,
                    pattern TEXT,
                    weight REAL
                )
                """
            )
            conn.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?)",
                ("e1", 0, "endpoint", "GET:/api/foo:404", 1.0),
            )
            conn.execute(
                "INSERT INTO rules VALUES (?,?,?,?,?)",
                ("h1", 0, "heal", "GET:/api/foo:200", 5.0),
            )
            conn.commit()

        monkeypatch.setattr(sidecar_db, "PSYCHE_DB_PATH", p)
        monkeypatch.setattr(sidecar_db, "is_postgres", lambda: False)

        rows = sidecar_db.psyche_rules_like("GET:/api/foo%")
        assert len(rows) == 1
        assert rows[0][0] == "GET:/api/foo:404"
        assert rows[0][1] == 1.0
