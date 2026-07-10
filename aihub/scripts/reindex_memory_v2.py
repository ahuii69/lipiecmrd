#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rebuild or repair Memory V2 vector indexing jobs.

Usage:
  python -m aihub.scripts.reindex_memory_v2 USER_ID --process
  python -m aihub.scripts.reindex_memory_v2 --all --enqueue --process --limit 500
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _load_env(repo: Path) -> None:
    env_file = repo / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def main(argv: list[str] | None = None) -> int:
    repo = Path(__file__).resolve().parents[2]
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    _load_env(repo)

    parser = argparse.ArgumentParser(description="Repair/retry Memory V2 vector indexes")
    parser.add_argument("user_id", nargs="?", help="User id to process")
    parser.add_argument("--all", action="store_true", help="Process all users")
    parser.add_argument("--enqueue", action="store_true", help="Enqueue unindexed active Memory V2 items first")
    parser.add_argument("--process", action="store_true", help="Process due pending/failed/stale jobs now")
    parser.add_argument("--limit", type=int, default=500, help="Maximum rows/jobs")
    args = parser.parse_args(argv)

    if not args.all and not args.user_id:
        parser.error("pass USER_ID or --all")
    user_id = None if args.all else args.user_id

    from aihub.db import init_db
    from aihub.memory_v2_index_jobs import enqueue_unindexed_items, index_job_summary, process_index_jobs

    init_db()
    report: dict[str, object] = {"user_id": user_id, "limit": args.limit}
    if args.enqueue:
        report["enqueue"] = enqueue_unindexed_items(user_id=user_id, limit=args.limit)
    if args.process:
        report["process"] = process_index_jobs(user_id=user_id, limit=args.limit)
    report["summary"] = index_job_summary(user_id=user_id)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not (isinstance(report.get("process"), dict) and report["process"].get("failed")) else 2


if __name__ == "__main__":
    raise SystemExit(main())
