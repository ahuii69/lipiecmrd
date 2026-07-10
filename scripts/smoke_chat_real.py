#!/usr/bin/env python3
"""Real HTTP smoke for /chat/turn.

Uses API_KEY from .env and checks that backend returns a non-empty assistant response.
Does not print secret values.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from scripts.dotenv_tool import parse_dotenv


def _request_json(url: str, payload: dict, api_key: str, timeout: float) -> tuple[int, dict | str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"content-type": "application/json", "x-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - operator-provided URL
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw[:2000]
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:2000]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--user-id", default="doctor_smoke")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--message", default="Odpowiedz jednym krótkim zdaniem: smoke ok.")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--include-debug", action="store_true")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    env_file = Path(args.env)
    if not env_file.is_absolute():
        env_file = repo / env_file
    env = parse_dotenv(env_file)
    api_key = (env.get("API_KEY") or "").strip()
    if not api_key:
        print("FAIL: API_KEY missing in env", file=sys.stderr)
        return 2
    base = (args.base_url or env.get("AIHUB_BASE_URL") or "http://127.0.0.1:8080").rstrip("/")
    session_id = args.session_id or f"doctor-{int(time.time())}"
    payload = {
        "user_id": args.user_id,
        "session_id": session_id,
        "message": args.message,
        "mode": "chat",
        "include_debug": bool(args.include_debug),
        "history": [],
        "attached_file_ids": [],
    }
    status, data = _request_json(f"{base}/chat/turn", payload, api_key, args.timeout)
    if status != 200:
        print(f"FAIL: /chat/turn HTTP {status}", file=sys.stderr)
        print(json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else data, file=sys.stderr)
        return 1
    if not isinstance(data, dict):
        print("FAIL: /chat/turn response is not JSON object", file=sys.stderr)
        return 1
    text = str(data.get("response_text") or data.get("message") or data.get("text") or "").strip()
    if not text:
        print("FAIL: /chat/turn returned empty response text", file=sys.stderr)
        print(json.dumps(data, ensure_ascii=False, indent=2)[:3000], file=sys.stderr)
        return 1
    trace = data.get("trace") if isinstance(data.get("trace"), dict) else {}
    print(json.dumps({
        "ok": True,
        "http_status": status,
        "response_chars": len(text),
        "session_id": session_id,
        "trace_keys": sorted(list(trace.keys()))[:40],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
