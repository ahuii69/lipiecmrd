#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Archive/suppress trivial meta/identity chatter memory (idempotent).

Examples:
  python scripts/cleanup_trivial_meta_memory.py --user-id <uuid> --dry-run
  python scripts/cleanup_trivial_meta_memory.py --user-id <uuid> --apply
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(description="Cleanup trivial meta identity memory items")
    parser.add_argument("--user-id", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args(argv)

    from aihub.db import fetch_all, exec_one
    from aihub.turn.prompt_budget import is_trivial_meta_memory_content, PROMPT_BUDGET_VERSION

    uid = str(args.user_id).strip()
    rows = fetch_all(
        """
        SELECT id, memory_type, title, content, summary, source_kind,
               is_suppressed, is_archived, is_pinned
        FROM memory_v2_items
        WHERE user_id=?
        ORDER BY updated_ts DESC
        LIMIT ?
        """,
        (uid, max(1, min(int(args.limit), 5000))),
    )

    scanned = 0
    matched: list[dict] = []
    skipped: list[dict] = []
    applied = 0

    for r in rows:
        scanned += 1
        row = dict(r)
        mid = str(row.get("id") or "")
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        summary = str(row.get("summary") or "")
        blob = f"{title}\n{content}\n{summary}"
        sk = str(row.get("source_kind") or "")
        mt = str(row.get("memory_type") or "")

        if row.get("is_pinned"):
            skipped.append({"id": mid, "reason": "pinned", "title": title[:80]})
            continue
        if mt == "preference" and sk in ("explicit_learning", "user_correction", "correction"):
            skipped.append({"id": mid, "reason": "real_preference", "title": title[:80]})
            continue
        if row.get("is_archived") and row.get("is_suppressed"):
            # Idempotent: already cleaned-looking rows
            if is_trivial_meta_memory_content(blob, query=title or content):
                skipped.append({"id": mid, "reason": "already_cleaned", "title": title[:80]})
                continue

        if not is_trivial_meta_memory_content(blob, query=title or content):
            skipped.append({"id": mid, "reason": "not_trivial_meta", "title": title[:80]})
            continue

        entry = {
            "id": mid,
            "memory_type": mt,
            "title": title[:100],
            "reason": "trivial_meta_or_identity_chatter",
        }
        matched.append(entry)
        if args.dry_run:
            continue

        exec_one(
            """
            UPDATE memory_v2_items
            SET is_suppressed=1, is_archived=1, updated_ts=?
            WHERE user_id=? AND id=?
            """,
            (time.time(), uid, mid),
        )
        applied += 1

    report = {
        "user_id": uid,
        "dry_run": bool(args.dry_run),
        "scanned": scanned,
        "matched": len(matched),
        "applied": applied if not args.dry_run else 0,
        "would_apply": len(matched) if args.dry_run else 0,
        "skipped": len(skipped),
        "matched_items": matched[:50],
        "skipped_sample": skipped[:20],
        "cleanup_version": PROMPT_BUDGET_VERSION,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"user_id={uid}")
        print(f"mode={'dry-run' if args.dry_run else 'apply'}")
        print(f"scanned={scanned} matched={report['matched']} skipped={report['skipped']}")
        print(
            f"applied={report['applied']}"
            if not args.dry_run
            else f"would_apply={report['would_apply']}"
        )
        print("--- matched ---")
        for m in matched[:30]:
            print(f"  {m['id']} | {m.get('memory_type')} | {m.get('title')}")
        print("--- skipped sample ---")
        for s in skipped[:15]:
            print(f"  {s['id']} | {s.get('reason')} | {s.get('title')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
