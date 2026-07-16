#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cancel non-actionable meta/small-talk goals (idempotent).

Examples:
  python scripts/cleanup_non_actionable_goals.py --user-id <uuid> --dry-run
  python scripts/cleanup_non_actionable_goals.py --user-id <uuid> --apply
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    parser = argparse.ArgumentParser(
        description="Cleanup non-actionable greeting/meta/small-talk goals"
    )
    parser.add_argument("--user-id", required=True, help="Target user id")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="Scan only; no writes")
    mode.add_argument("--apply", action="store_true", help="Cancel matched goals")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary",
    )
    args = parser.parse_args(argv)

    from aihub.goal_engine import get_goal_engine

    engine = get_goal_engine()
    report = engine.cleanup_non_actionable_goals(
        str(args.user_id).strip(),
        dry_run=bool(args.dry_run),
    )

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"user_id={report['user_id']}")
    print(f"mode={'dry-run' if report['dry_run'] else 'apply'}")
    print(f"cleanup_version={report['cleanup_version']}")
    print(f"scanned={report['scanned']}")
    print(f"matched={report['matched']}")
    print(
        f"cancelled={report['cancelled']}"
        if not report["dry_run"]
        else f"would_cancel={report['would_cancel']}"
    )
    print(f"skipped={report['skipped']}")
    print("--- matched ---")
    for g in report.get("matched_goals") or []:
        print(
            f"  {g.get('goal_id')} | {g.get('status')} | {g.get('goal_type')} | "
            f"{(g.get('title') or '')[:80]} | reason={g.get('reason')}"
        )
    print("--- skipped (sample up to 20) ---")
    for g in (report.get("skipped_goals") or [])[:20]:
        print(
            f"  {g.get('goal_id')} | {g.get('status')} | {g.get('goal_type')} | "
            f"{(g.get('title') or '')[:80]} | reason={g.get('reason')}"
        )
    if report.get("cancelled_goals"):
        print("--- cancelled ---")
        for g in report["cancelled_goals"]:
            print(
                f"  {g.get('goal_id')} | {(g.get('title') or '')[:80]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
