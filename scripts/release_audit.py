#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static release audit for AI-Hub.

Fails on things that previously caused broken zips: module collisions, exact
file duplicates, unfinished-code markers in production Python, duplicate FastAPI
method/path routes, and import failures. It does not print secrets.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import pkgutil
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

UNFINISHED_RE = re.compile(r"\b(TODO|FIXME|placeholder|pseudocode|pseudokod|NotImplemented|skeleton)\b", re.IGNORECASE)
PASS_RE = re.compile(r"^\s*pass\s*(#.*)?$", re.MULTILINE)
# Directory names skipped anywhere in the tree. These are build outputs, dependency installs,
# tool caches, virtualenvs and runtime log dirs — never release source. Matching is by path
# component, so a SOURCE file whose name merely contains one of these (e.g. aihub/logs.py) is NOT
# affected; only directories literally named like this are pruned.
SKIP_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "node_modules",
    ".next",
    "dist",
    "build",
    "coverage",
    "playwright-report",
    "test-results",
    ".venv",
    ".venvs",
    "logs",
}
TEXT_MARKER_IGNORE = {
    "scripts/doctor.py",  # detection token list, not unfinished code
    "scripts/release_audit.py",  # this scanner's own regex/list
}
# Runtime artifact subtrees (paths relative to the repo root, POSIX form). Everything under these
# is generated while the app runs or while tests run — user uploads, scratch/tmp, on-disk caches,
# the sandbox filesystem and psyche/state snapshots — and is NEVER part of release source. Two
# byte-identical files here (e.g. the same test fixture image uploaded under two different user
# ids: data/uploads/u_img/... and data/uploads/um/...) are runtime coincidences, not source
# duplication, so they must not fail the audit. This is intentionally scoped to runtime data trees
# only; source/code/docs/config/test trees (aihub/, cockpit/, scripts/, tests/, docs/, config/,
# archive/, root project files, etc.) keep full exact-duplicate detection.
RUNTIME_ARTIFACT_PREFIXES = (
    "data/uploads",
    "data/tmp",
    "data/cache",
    "data/fs",
    "data/snapshots",
)
# Runtime database files (and their SQLite WAL/SHM/journal sidecars) that live under data/. These
# are created by running the app/tests, are binary and per-install, and are never release source.
_RUNTIME_DB_RE = re.compile(r"\.sqlite3?(-wal|-shm|-journal)?$", re.IGNORECASE)


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_runtime_artifact(rel_posix: str) -> bool:
    if any(
        rel_posix == prefix or rel_posix.startswith(prefix + "/")
        for prefix in RUNTIME_ARTIFACT_PREFIXES
    ):
        return True
    # Runtime SQLite databases live under the runtime data/ tree only. Scoping the suffix match to
    # data/ avoids ever ignoring a checked-in *.sqlite test fixture that belongs to tests/ sources.
    if rel_posix.startswith("data/") and _RUNTIME_DB_RE.search(rel_posix):
        return True
    return False


def _files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if _is_runtime_artifact(rel.as_posix()):
            continue
        if path.is_file():
            out.append(path)
    return out


def exact_duplicates(root: Path) -> list[list[str]]:
    groups: dict[tuple[int, str], list[str]] = {}
    for path in _files(root):
        # Empty sentinels are allowed: .gitkeep and package markers are not code duplication.
        if path.stat().st_size == 0 and path.name in {"__init__.py", ".gitkeep"}:
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        groups.setdefault((path.stat().st_size, digest), []).append(_rel(root, path))
    return [sorted(v) for v in groups.values() if len(v) > 1]


def module_collisions(root: Path) -> list[dict[str, str]]:
    collisions: list[dict[str, str]] = []
    for pkg in [root / "aihub"]:
        if not pkg.is_dir():
            continue
        for py in pkg.rglob("*.py"):
            if py.name == "__init__.py":
                continue
            sibling_dir = py.with_suffix("")
            if sibling_dir.is_dir() and (sibling_dir / "__init__.py").exists():
                collisions.append({"file": _rel(root, py), "package": _rel(root, sibling_dir)})
    return collisions


def unfinished_markers(root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for py in (root / "aihub").rglob("*.py"):
        rel = _rel(root, py)
        if rel in TEXT_MARKER_IGNORE:
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), start=1):
            if UNFINISHED_RE.search(line) or PASS_RE.match(line):
                findings.append({"file": rel, "line": line_no, "text": line.strip()[:240]})
    return findings


def import_failures(root: Path) -> dict[str, Any]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("DB_BACKEND", "sqlite")
    audit_tmp = Path(tempfile.gettempdir()) / "aihub_release_audit"
    audit_tmp.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("DB_PATH", str(audit_tmp / "release_audit.sqlite3"))
    os.environ.setdefault("DATA_DIR", str(audit_tmp))
    os.environ.setdefault("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "1")
    os.environ.setdefault("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "1")
    import aihub

    failures: list[dict[str, str]] = []
    count = 0
    for mod in pkgutil.walk_packages(aihub.__path__, aihub.__name__ + "."):
        count += 1
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001
            failures.append({"module": mod.name, "error": repr(exc)[:600]})
    return {"count": count, "failures": failures}


def route_duplicates(root: Path) -> list[dict[str, Any]]:
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from aihub.main import app

    seen: set[tuple[tuple[str, ...], str]] = set()
    dupes: list[dict[str, Any]] = []
    for route in app.routes:
        methods = tuple(sorted(getattr(route, "methods", []) or []))
        path = str(getattr(route, "path", ""))
        key = (methods, path)
        if key in seen:
            dupes.append({"methods": methods, "path": path})
        seen.add(key)
    return dupes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.repo).resolve()
    report = {
        "module_collisions": module_collisions(root),
        "exact_duplicates": exact_duplicates(root),
        "unfinished_markers": unfinished_markers(root),
    }
    imports = import_failures(root)
    report["import_count"] = imports["count"]
    report["import_failures"] = imports["failures"]
    report["route_duplicates"] = route_duplicates(root)
    report["ok"] = not any(
        report[key]
        for key in ["module_collisions", "exact_duplicates", "unfinished_markers", "import_failures", "route_duplicates"]
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for key in ["module_collisions", "exact_duplicates", "unfinished_markers", "import_failures", "route_duplicates"]:
            val = report[key]
            print(f"{key}: {'OK' if not val else 'FAIL'}" + (f" ({len(val)})" if val else ""))
            if val:
                print(json.dumps(val[:20], ensure_ascii=False, indent=2))
        print(f"import_count: {report['import_count']}")
        print("RESULT:", "OK" if report["ok"] else "FAIL")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
