#!/usr/bin/env python3
"""Regenerate cockpit/.env from root .env without exposing secrets."""
from __future__ import annotations

import argparse
from pathlib import Path

from scripts.dotenv_tool import cmd_write_cockpit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--out", default="cockpit/.env")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()

    class ToolArgs:
        repo_root = str(repo)
        env_file = str((repo / args.env).resolve() if not Path(args.env).is_absolute() else Path(args.env))
        out_env = str((repo / args.out).resolve() if not Path(args.out).is_absolute() else Path(args.out))
        base_url = args.base_url

    return cmd_write_cockpit(ToolArgs())


if __name__ == "__main__":
    raise SystemExit(main())
