"""ARCHIVED — 06.07 repair sprint (P1 security).

This module is **not part of the ``aihub`` Python package** and is **not importable, mounted,
or reachable from the running application** in any way. Kept here, outside the runtime tree,
purely as historical reference.

Why archived instead of left in ``aihub/api/`` as "unmounted":
- Hardcoded host path ``ROOT_DIR = Path("/root/ai-hub")`` — stale/wrong for this deployment
  (this repo runs from ``/home/ubuntu/mrd``); if ever mounted, `ops_snap`/`ops_rollback` would
  silently operate on the wrong directory or fail in a confusing way.
- ``POST /system/ops/rollback`` extracts an attacker/operator-supplied tarball and then shells
  out (``subprocess.Popen(["bash", "-lc", script])``) to run ``systemctl restart aihub`` — a
  systemd service name that does not necessarily exist on every deployment of this project.
- It did have an explicit safety flag (``AIHUB_ALLOW_OPS``) and tar-extraction path validation,
  which is better than ``ai_compat_router.py``, but "unmounted + one env flag" is not a
  substitute for a reviewed, portable ops design. See ``archive/legacy_routers/README.md``.

Do **not** move this back into ``aihub/`` or add any ``include_router`` for it without fixing
the hardcoded path and reviewing the restart mechanism for the target deployment.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

# LEGACY / ARCHIVED / NOT PART OF THE aihub PACKAGE: see module docstring above and
# archive/legacy_routers/README.md.


router = APIRouter(prefix="/system/ops", tags=["ops"])

ROOT_DIR = Path("/root/ai-hub")  # stale hardcoded path, kept as-is for historical reference only
SNAP_DIR = ROOT_DIR / "data" / "ops_snaps"
SERVICE_NAME = "aihub"

# Control-plane: tego rollback NIE ma dotykać (bo wtedy rollback sam siebie psuje)
EXCLUDE_FROM_ROLLBACK: Set[str] = {
    "aihub/main.py",
    "aihub/api/ops_router.py",
}

# Dodatkowo: żadnych pyc z backupów
EXCLUDE_PREFIXES: tuple[str, ...] = (
    "aihub/__pycache__/",
    "aihub/api/__pycache__/",
)

EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".pyc",
    ".pyo",
)

SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


def _flag(name: str) -> bool:
    v = os.getenv(name, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _require_ops_enabled() -> None:
    if not _flag("AIHUB_ALLOW_OPS"):
        raise HTTPException(
            status_code=403, detail="ops disabled (set AIHUB_ALLOW_OPS=1)"
        )


def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_members(tf: tarfile.TarFile) -> List[tarfile.TarInfo]:
    out: List[tarfile.TarInfo] = []
    for m in tf.getmembers():
        name = m.name

        # normalize
        name = name.lstrip("./")

        # only relative safe ascii-ish paths
        if not name or name.startswith("/") or ".." in Path(name).parts:
            continue
        if not SAFE_PATH_RE.match(name):
            continue

        # only restore aihub/*
        if not name.startswith("aihub/"):
            continue

        # exclude caches
        if name.startswith(EXCLUDE_PREFIXES):
            continue
        if name.endswith(EXCLUDE_SUFFIXES):
            continue

        # exclude control-plane from rollback
        if name in EXCLUDE_FROM_ROLLBACK:
            continue

        out.append(m)
    return out


def _run(cmd: List[str], timeout: int = 60) -> str:
    p = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )
    if p.returncode != 0:
        raise RuntimeError(
            f"command failed rc={p.returncode}: {' '.join(cmd)}\n{p.stdout}"
        )
    return p.stdout


def _schedule_restart_detached(tag: str) -> None:
    # Restart ma zabić proces, więc robimy to w osobnym procesie, po krótkim sleep.
    # setsid + bash -lc dla spójności env/systemctl.
    script = f"""
set -euo pipefail
sleep 0.25
echo "[ops:{tag}] restarting {SERVICE_NAME}..." >&2
systemctl restart {SERVICE_NAME}
"""
    subprocess.Popen(
        ["bash", "-lc", script],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )


class SnapResponse(BaseModel):
    status: str = "ok"
    ts: int
    snap_id: str
    path: str
    size_bytes: int
    sha256: str


class RollbackRequest(BaseModel):
    snap_id: str = Field(..., min_length=3, max_length=64)


class RollbackResponse(BaseModel):
    status: str = "ok"
    ts: int
    snap_id: str
    restored: bool
    restart_scheduled: bool
    note: str


@router.post("/snap", response_model=SnapResponse)
def ops_snap() -> SnapResponse:
    _require_ops_enabled()

    SNAP_DIR.mkdir(parents=True, exist_ok=True)

    snap_id = str(int(time.time()))
    out_path = SNAP_DIR / f"aihub_{snap_id}.tar.gz"

    # Snapshot: pakujemy aktualny kod aihub/ (bez venv, bez data)
    src_dir = ROOT_DIR / "aihub"
    if not src_dir.exists():
        raise HTTPException(status_code=500, detail=f"missing {src_dir}")

    with tarfile.open(out_path, "w:gz") as tf:
        tf.add(src_dir, arcname="aihub", recursive=True)

    size_bytes = out_path.stat().st_size
    sha = _sha256_file(out_path)

    return SnapResponse(
        ts=int(time.time()),
        snap_id=snap_id,
        path=str(out_path),
        size_bytes=size_bytes,
        sha256=sha,
    )


@router.post("/rollback", response_model=RollbackResponse)
def ops_rollback(req: RollbackRequest) -> RollbackResponse:
    _require_ops_enabled()

    snap_id = req.snap_id.strip()
    if not re.fullmatch(r"[0-9]{6,20}", snap_id):
        raise HTTPException(status_code=400, detail="invalid snap_id format")

    snap_path = SNAP_DIR / f"aihub_{snap_id}.tar.gz"
    if not snap_path.exists():
        raise HTTPException(status_code=404, detail=f"snap not found: {snap_path}")

    # Restore: wyciągamy tylko bezpieczne pliki i NIE ruszamy control-plane
    try:
        with tarfile.open(snap_path, "r:gz") as tf:
            members = _safe_members(tf)
            tf.extractall(path=str(ROOT_DIR), members=members)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"restore failed: {e}")

    # Restart detached (bo restart w request zabija proces -> rc=-15)
    try:
        _schedule_restart_detached(tag=f"rollback-{snap_id}")
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"restored but failed to schedule restart: {e}"
        )

    return RollbackResponse(
        ts=int(time.time()),
        snap_id=snap_id,
        restored=True,
        restart_scheduled=True,
        note="rollback applied; restart scheduled (detached); control-plane excluded",
    )
