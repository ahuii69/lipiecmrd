#!/usr/bin/env python3
"""Smoke: legacy V2 SQLite shape → init_db → schema health (no pytest)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _seed_legacy(path: Path) -> None:
    con = sqlite3.connect(str(path))
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


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--db",
        type=Path,
        default=None,
        help="SQLite path (default: temp file)",
    )
    args = p.parse_args()
    if args.db:
        db_path = args.db
    else:
        with tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False) as f:
            db_path = Path(f.name)
    _seed_legacy(db_path)

    import os

    os.environ["DB_PATH"] = str(db_path)
    os.environ.setdefault("DATA_DIR", str(db_path.parent))

    import importlib

    import aihub.config as cfg
    import aihub.db as db_mod

    importlib.reload(cfg)
    importlib.reload(db_mod)
    db_mod._ADAPTER_HOLDER.clear()

    db_mod.init_db()
    health = db_mod.get_active_stack_schema_health()
    if not health.get("ok"):
        print("FAIL", health)
        return 1
    print("OK", db_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
