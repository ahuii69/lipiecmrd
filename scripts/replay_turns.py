#!/usr/bin/env python3
"""Replay historical turns through adaptive learning (no side-effect tools)."""

from __future__ import annotations

import argparse
import json
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay turns for adaptive learning evaluation")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--mode", choices=("evaluation",), default="evaluation")
    parser.add_argument("--repo", default="/home/ubuntu/mrd")
    args = parser.parse_args()
    sys.path.insert(0, args.repo)

    from aihub.adaptive_learning.replay import replay_user_turns
    from aihub.adaptive_learning.schema import ensure_adaptive_learning_schema
    from aihub.db import init_db

    init_db()
    ensure_adaptive_learning_schema()
    out = replay_user_turns(user_id=args.user_id, limit=args.limit, mode=args.mode)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
