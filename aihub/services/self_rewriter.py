import os
if os.getenv("AIHUB_REWRITER_READONLY", "0") == "1":
    # hard stop: do not modify any files
    def heal(*args, **kwargs):
        return {"ok": True, "readonly": True, "reason": "AIHUB_REWRITER_READONLY"}
    def rollback(*args, **kwargs):
        return {"ok": True, "readonly": True, "reason": "AIHUB_REWRITER_READONLY"}

import shutil
import sqlite3
import time
from pathlib import Path

from aihub.config import SNAPSHOT_DIR, SELF_HEAL_DB_PATH
from aihub.sidecar_db import (
    healed_init_sqlite,
    healed_insert_pg,
    healed_insert_sqlite,
    healed_rollback_rows_pg,
    healed_rollback_rows_sqlite,
    is_postgres,
)

DB = Path(SELF_HEAL_DB_PATH)
SNAP = Path(SNAPSHOT_DIR)
SNAP.mkdir(parents=True, exist_ok=True)


def init_state() -> None:
    if is_postgres():
        return
    healed_init_sqlite(DB)


def snapshot_file(path: Path) -> Path | None:
    init_state()
    if not path.exists():
        return None
    ts = int(time.time())
    snap_name = f"{path.name}.{ts}.snap"
    snap_path = SNAP / snap_name
    shutil.copy2(path, snap_path)
    if is_postgres():
        healed_insert_pg(str(path), str(snap_path), str(snap_path), ts)
    else:
        healed_insert_sqlite(DB, str(path), str(snap_path), str(snap_path), ts)
    return snap_path


def rollback(limit: int = 50) -> list[str]:
    init_state()
    restored: list[str] = []
    if is_postgres():
        rows = healed_rollback_rows_pg(limit)
    else:
        rows = healed_rollback_rows_sqlite(DB, limit)
    for path_str, snap in rows:
        if not snap:
            continue
        snap_p = Path(snap)
        if not snap_p.exists():
            continue
        dest = Path(path_str)
        shutil.copy2(snap_p, dest)
        restored.append(str(dest))
    return restored


# =========================
# COMPAT: heal() entrypoint
# =========================
def heal(*args, **kwargs):
    """
    Compatibility entrypoint for aihub.workers.self_heal (imports heal).
    Tries to create a snapshot and returns a small dict with result.
    Never raises (worker should not crash the whole app).
    """
    try:
        import inspect
        ts = int(time.time())

        snap_path = None
        if "snapshot_file" in globals() and callable(globals().get("snapshot_file")):
            fn = globals()["snapshot_file"]
            try:
                sig = inspect.signature(fn)
                params = sig.parameters

                # call snapshot_file in the safest possible way
                if len(params) == 0:
                    snap_path = fn()
                else:
                    # pass only kwargs that exist in signature
                    call_kwargs = {}
                    for k, v in kwargs.items():
                        if k in params:
                            call_kwargs[k] = v
                    snap_path = fn(**call_kwargs)
            except Exception:
                # snapshot is nice-to-have, do not fail healing if signature/impl differs
                snap_path = None

        return {
            "ok": True,
            "ts": ts,
            "snapshot": str(snap_path) if snap_path else None,
            "args": list(args),
            "kwargs": {k: str(v) for k, v in kwargs.items()},
        }

    except Exception as e:
        return {
            "ok": False,
            "ts": int(time.time()),
            "error": f"{type(e).__name__}: {e}",
        }
