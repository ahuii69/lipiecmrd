#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real Memory V2 smoke against a running AI-Hub backend.

Creates a unique memory, checks context-pack retrieval and index-job visibility.
No secrets are printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path

import httpx


def _load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, val = raw.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--user-id", default=f"memory-smoke-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--timeout", type=float, default=45.0)
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    env = _load_env((repo / args.env).resolve() if not Path(args.env).is_absolute() else Path(args.env))
    api_key = env.get("API_KEY") or env.get("AIHUB_API_KEY") or env.get("HUB_API_KEY") or ""
    if not api_key:
        print(json.dumps({"ok": False, "error": "missing API key in env"}, ensure_ascii=False))
        return 2

    unique = f"memory smoke token {uuid.uuid4().hex}"
    headers = {"x-api-key": api_key, "content-type": "application/json", "accept": "application/json"}
    base = args.base_url.rstrip("/")
    with httpx.Client(timeout=args.timeout, headers=headers) as client:
        create = client.post(
            f"{base}/memory/v2/item",
            json={
                "user_id": args.user_id,
                "memory_type": "preference",
                "scope": "user",
                "title": "Memory smoke preference",
                "content": f"User expects real working code and no placeholders. Unique: {unique}",
                "source_kind": "explicit_learning",
                "importance_score": 0.91,
                "confidence_score": 0.93,
            },
        )
        create.raise_for_status()
        memory_id = create.json().get("memory_id")
        if not memory_id:
            raise RuntimeError("memory creation returned no memory_id")

        pack = client.post(
            f"{base}/memory/v2/context-pack",
            json={"user_id": args.user_id, "query": unique, "limit": 10, "max_chars": 6000},
        )
        pack.raise_for_status()
        pack_body = pack.json()
        selected = set(pack_body.get("selected_ids") or [])

        jobs = client.get(f"{base}/memory/v2/index-jobs", params={"user_id": args.user_id})
        jobs.raise_for_status()

    ok = memory_id in selected
    print(json.dumps({
        "ok": ok,
        "user_id": args.user_id,
        "memory_id": memory_id,
        "selected": ok,
        "index_jobs": jobs.json(),
        "ts": time.time(),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
