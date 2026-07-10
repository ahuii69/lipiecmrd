#!/usr/bin/env python3

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from aihub.db import _db_backend, append_event, exec_one, fetch_all


def _get_db_path() -> Path:
    """Get DB_PATH dynamically (for test isolation)."""
    from aihub.config import DB_PATH
    return DB_PATH


def _get_snapshot_dir() -> Path:
    """Get SNAPSHOT_DIR dynamically (for test isolation)."""
    from aihub.config import SNAPSHOT_DIR
    return SNAPSHOT_DIR


def create_snapshot(user_id: str, reason: str) -> dict[str, Any]:
    snap_dir = _get_snapshot_dir()
    snap_id = f"{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"
    snap_dir.mkdir(parents=True, exist_ok=True)

    if _db_backend() == "postgres":
        dsn = (os.getenv("POSTGRES_DSN") or "").strip()
        if not dsn:
            raise RuntimeError("POSTGRES_DSN jest wymagany do snapshotu PostgreSQL")
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise RuntimeError(
                "Brak pg_dump w PATH — zainstaluj klienta PostgreSQL (postgresql-client)"
            )
        dst = snap_dir / f"{snap_id}.pgdump.sql"
        cmd = [
            pg_dump,
            "-d",
            dsn,
            "-F",
            "p",
            "--clean",
            "--if-exists",
            "-f",
            str(dst),
        ]
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"pg_dump failed (exit {proc.returncode}): {err[:2000]}")
    else:
        db_path = _get_db_path()
        dst = snap_dir / f"{snap_id}.sqlite3"
        shutil.copy2(db_path, dst)
        wal = Path(str(db_path) + "-wal")
        shm = Path(str(db_path) + "-shm")
        if wal.exists():
            shutil.copy2(wal, snap_dir / f"{snap_id}.sqlite3-wal")
        if shm.exists():
            shutil.copy2(shm, snap_dir / f"{snap_id}.sqlite3-shm")

    exec_one(
        "INSERT INTO snapshots(id, reason, ts, db_path) VALUES(?,?,?,?)",
        (snap_id, reason, time.time(), str(dst)),
    )
    append_event(
        user_id, "system.snapshot.create", {"snapshot_id": snap_id, "reason": reason}
    )
    return {
        "snapshot_id": snap_id,
        "path": str(dst),
        "reason": reason,
        "ts": time.time(),
    }


def list_snapshots() -> list[dict[str, Any]]:
    rows = fetch_all(
        "SELECT id, reason, ts, db_path FROM snapshots ORDER BY ts DESC LIMIT 200"
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "reason": r["reason"],
                "ts": float(r["ts"]),
                "db_path": r["db_path"],
            }
        )
    return out


def restore_snapshot(user_id: str, snapshot_id: str) -> dict[str, Any]:
    snap_dir = _get_snapshot_dir()

    rows = fetch_all("SELECT id, db_path FROM snapshots WHERE id=?", (snapshot_id,))
    if not rows:
        raise FileNotFoundError("snapshot not found")
    src = Path(rows[0]["db_path"])
    if not src.exists():
        raise FileNotFoundError("snapshot file missing")

    safety = create_snapshot(user_id, reason=f"pre-restore:{snapshot_id}")

    if _db_backend() == "postgres":
        if not str(src).endswith(".pgdump.sql"):
            raise RuntimeError(
                "Ten snapshot nie pochodzi z PostgreSQL (oczekiwano pliku .pgdump.sql)"
            )
        dsn = (os.getenv("POSTGRES_DSN") or "").strip()
        if not dsn:
            raise RuntimeError("POSTGRES_DSN jest wymagany do przywrócenia PostgreSQL")
        psql = shutil.which("psql")
        if not psql:
            raise RuntimeError(
                "Brak psql w PATH — zainstaluj klienta PostgreSQL (postgresql-client)"
            )
        proc = subprocess.run(
            [psql, dsn, "-v", "ON_ERROR_STOP=1", "-f", str(src)],
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeError(f"psql restore failed (exit {proc.returncode}): {err[:2000]}")
    else:
        db_path = _get_db_path()
        shutil.copy2(src, db_path)
        src_wal = snap_dir / f"{snapshot_id}.sqlite3-wal"
        src_shm = snap_dir / f"{snapshot_id}.sqlite3-shm"
        wal = Path(str(db_path) + "-wal")
        shm = Path(str(db_path) + "-shm")
        if src_wal.exists():
            shutil.copy2(src_wal, wal)
        else:
            if wal.exists():
                wal.unlink()
        if src_shm.exists():
            shutil.copy2(src_shm, shm)
        else:
            if shm.exists():
                shm.unlink()

    append_event(
        user_id,
        "system.snapshot.restore",
        {"snapshot_id": snapshot_id, "safety_snapshot": safety.get("snapshot_id")},
    )
    return {
        "restored": snapshot_id,
        "safety_snapshot": safety.get("snapshot_id"),
        "ts": time.time(),
    }
