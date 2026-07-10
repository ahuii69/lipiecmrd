from __future__ import annotations

"""Compatibility adapter for legacy filesystem router.

All real filesystem logic lives in :mod:`aihub.fs_tools`; this module only keeps
old function names/signatures so legacy imports do not duplicate implementation.
"""

from typing import Any, Dict, List

from aihub import fs_tools


def list_dir(path: str) -> List[Dict[str, Any]]:
    return fs_tools.list_dir_entries(path)


def read_file(path: str) -> Dict[str, Any]:
    return fs_tools.read_file_base64(path)


def write_file(path: str, content_base64: str) -> Dict[str, Any]:
    return fs_tools.write_file_base64(path, content_base64)


def mkdir(path: str) -> Dict[str, Any]:
    return fs_tools.make_dir(path)


def delete(path: str) -> Dict[str, Any]:
    return fs_tools.delete_path(path)


def move(src: str, dst: str) -> Dict[str, Any]:
    return fs_tools.move_path(src, dst)
