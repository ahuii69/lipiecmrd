#!/usr/bin/env python3
from __future__ import annotations

import base64
import binascii
import os
import shutil
from pathlib import Path
from typing import Any

from aihub.config import safe_join
from aihub.db import append_event


def _get_fs_root() -> Path:
    """Get FS_ROOT dynamically for test isolation."""
    from aihub.config import FS_ROOT

    root = Path(FS_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _rel(path: Path, root: Path) -> str:
    value = str(path.relative_to(root))
    return value if value != "." else ""


def _safe(path: str) -> Path:
    return safe_join(_get_fs_root(), path or "/")


def write_file(user_id: str, path: str, content: str, overwrite: bool = True) -> dict[str, Any]:
    root = _get_fs_root()
    p = safe_join(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        raise FileExistsError("file exists and overwrite=false")
    p.write_text(content, encoding="utf-8")
    out = {"path": _rel(p, root), "bytes": p.stat().st_size}
    append_event(user_id, "fs.write", out)
    return out


def read_file(user_id: str, path: str, max_bytes: int) -> dict[str, Any]:
    root = _get_fs_root()
    p = safe_join(root, path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError("file not found")
    data = p.read_bytes()[: max(0, int(max_bytes))]
    out = {
        "path": _rel(p, root),
        "bytes": len(data),
        "content": data.decode("utf-8", errors="replace"),
    }
    append_event(user_id, "fs.read", {"path": out["path"], "bytes": out["bytes"]})
    return out


def list_dir(user_id: str, path: str, recursive: bool, max_items: int) -> dict[str, Any]:
    root = _get_fs_root()
    base = safe_join(root, path)
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError("dir not found")
    iterator = base.rglob("*") if recursive else base.iterdir()
    items: list[dict[str, Any]] = []
    for i, p in enumerate(iterator):
        if i >= max(0, int(max_items)):
            break
        st = p.stat()
        items.append(
            {
                "name": p.name,
                "path": _rel(p, root),
                "type": "dir" if p.is_dir() else "file",
                "is_dir": p.is_dir(),
                "size": int(st.st_size) if p.is_file() else 0,
                "mtime": int(st.st_mtime),
            }
        )
    out = {"path": _rel(base, root), "items": items, "count": len(items)}
    append_event(user_id, "fs.list", {"path": out["path"], "count": out["count"], "recursive": recursive})
    return out


def list_dir_entries(path: str) -> list[dict[str, Any]]:
    return list_dir("_legacy_fs", path, recursive=False, max_items=10_000)["items"]


def read_file_base64(path: str) -> dict[str, Any]:
    root = _get_fs_root()
    p = safe_join(root, path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError("not a file")
    data = p.read_bytes()
    return {"path": path, "size": len(data), "content_base64": base64.b64encode(data).decode("utf-8")}


def write_file_base64(path: str, content_base64: str, overwrite: bool = True) -> dict[str, Any]:
    try:
        data = base64.b64decode((content_base64 or "").encode("utf-8"), validate=True)
    except binascii.Error as exc:
        raise ValueError("invalid base64") from exc
    root = _get_fs_root()
    p = safe_join(root, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and not overwrite:
        raise FileExistsError("file exists and overwrite=false")
    p.write_bytes(data)
    out = {"path": path, "written": True, "size": len(data)}
    append_event("_legacy_fs", "fs.write_base64", {"path": path, "bytes": len(data)})
    return out


def make_dir(path: str) -> dict[str, Any]:
    p = _safe(path)
    p.mkdir(parents=True, exist_ok=True)
    append_event("_legacy_fs", "fs.mkdir", {"path": path})
    return {"path": path, "created": True}


def delete_path(path: str) -> dict[str, Any]:
    p = _safe(path)
    if not p.exists():
        raise FileNotFoundError("not found")
    if p.is_dir():
        try:
            p.rmdir()
        except OSError:
            shutil.rmtree(p)
    else:
        p.unlink()
    append_event("_legacy_fs", "fs.delete", {"path": path})
    return {"path": path, "deleted": True}


def move_path(src: str, dst: str) -> dict[str, Any]:
    root = _get_fs_root()
    s = safe_join(root, src)
    d = safe_join(root, dst)
    if not s.exists():
        raise FileNotFoundError("not found")
    d.parent.mkdir(parents=True, exist_ok=True)
    os.replace(s, d)
    append_event("_legacy_fs", "fs.move", {"src": src, "dst": dst})
    return {"src": src, "dst": dst, "moved": True}
