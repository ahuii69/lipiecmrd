#!/usr/bin/env python3
"""Offline functional smoke for the mounted AI-Hub API surface.

This is not a static audit. It starts the FastAPI app through lifespan, initializes
SQLite schema, exercises core GET/POST routes, Memory V2 write/search/context-pack,
Psyche V2 event/runtime and ops readiness. It deliberately avoids live LLM/web calls
unless explicitly requested by separate real smoke scripts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any


def _set_offline_runtime(repo: Path, db_path: str | None) -> None:
    data_dir = repo / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("API_KEY", "functional-smoke-key")
    os.environ.setdefault("AIHUB_TOKEN_SECRET", "functional-smoke-secret")
    os.environ["DB_BACKEND"] = "sqlite"
    os.environ["DB_PATH"] = db_path or str(data_dir / "functional_smoke.sqlite3")
    os.environ.setdefault("DATA_DIR", str(data_dir))
    os.environ.setdefault("LOG_DIR", str(repo / "logs"))
    os.environ["AGENT_AUTOSTART"] = "0"
    os.environ["AIHUB_BACKGROUND_AGENT_LOOP_ENABLED"] = "0"
    os.environ.setdefault("FS_ROOT", str(data_dir / "fs"))
    os.environ.setdefault("SNAPSHOT_DIR", str(data_dir / "snapshots"))
    # Offline smoke verifies routing/schema/lifecycle without external providers.
    os.environ["AIHUB_DISABLE_REMOTE_EMBEDDINGS"] = "1"
    os.environ["AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK"] = "1"
    os.environ["AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK"] = "1"
    os.environ.setdefault("LLM_API_KEY", "functional-smoke-llm-key")
    os.environ.setdefault("LLM_BASE_URL", "https://example.invalid/v1")


def _path_for_template(path: str) -> str:
    out = path
    replacements = {
        "{user_id}": "smoke-user",
        "{session_id}": "smoke-session",
        "{goal_id}": "missing-goal",
        "{task_id}": "missing-task",
    }
    for old, new in replacements.items():
        out = out.replace(old, new)
    return re.sub(r"\{[^}]+\}", "x", out)


def _assert_okish(resp: Any, *, label: str, allow: set[int] | None = None) -> None:
    allowed = allow or {200, 201, 202, 204, 404, 410, 422}
    if resp.status_code >= 500 or resp.status_code not in allowed:
        raise AssertionError(f"{label} HTTP {resp.status_code}: {resp.text[:600]}")


def run(repo: Path, *, db_path: str | None = None, json_output: bool = False) -> int:
    repo = repo.resolve()
    sys.path.insert(0, str(repo))
    _set_offline_runtime(repo, db_path)

    from fastapi.testclient import TestClient
    from aihub.main import app

    headers = {"x-api-key": os.environ.get("API_KEY", "functional-smoke-key")}
    report: dict[str, Any] = {"ok": True, "get_checked": 0, "post_checked": [], "failures": []}

    try:
        with TestClient(app) as client:
            for route in app.routes:
                methods = set(getattr(route, "methods", []) or [])
                path = str(getattr(route, "path", ""))
                if "GET" not in methods or path.startswith("/docs") or path in {"/openapi.json", "/redoc"}:
                    continue
                if path.startswith("/sse"):
                    continue
                p = _path_for_template(path)
                resp = client.get(p, headers=headers)
                _assert_okish(resp, label=f"GET {p}")
                report["get_checked"] += 1

            # Memory V2 full local lifecycle: create -> search -> context pack -> index jobs.
            create_payload = {
                "user_id": "smoke-user",
                "memory_type": "fact",
                "scope": "user",
                "title": "Functional smoke memory",
                "content": "Mordo wymaga realnie działającego projektu bez szkieletów i bez udawania.",
                "source_kind": "explicit_learning",
                "source_ref": "functional_endpoint_smoke",
                "importance_score": 0.9,
                "confidence_score": 0.9,
            }
            resp = client.post("/memory/v2/item", json=create_payload, headers=headers)
            _assert_okish(resp, label="POST /memory/v2/item", allow={200})
            body = resp.json()
            mem_id = body.get("memory_id")
            if not mem_id:
                raise AssertionError(f"memory item response missing memory_id: {body}")
            report["post_checked"].append("/memory/v2/item")

            for path, payload in [
                ("/memory/v2/search", {"user_id": "smoke-user", "query": "realny projekt bez szkieletów", "limit": 5}),
                ("/memory/v2/context-pack", {"user_id": "smoke-user", "query": "co user wymaga od projektu", "limit": 8, "max_chars": 4000}),
                ("/psyche/v2/event", {"user_id": "smoke-user", "event_type": "interaction_start", "reason_text": "functional smoke", "metadata": {"source": "functional_endpoint_smoke"}}),
                ("/chat/capabilities", None),
                ("/ops/health", None),
                ("/ops/ready", None),
                ("/ops/capabilities", None),
                ("/web/health", None),
                ("/psyche/v2/runtime/smoke-user", None),
                ("/psyche/runtime/smoke-user", None),
            ]:
                if payload is None:
                    resp = client.get(path, headers=headers)
                    label = f"GET {path}"
                else:
                    resp = client.post(path, json=payload, headers=headers)
                    label = f"POST {path}"
                _assert_okish(resp, label=label, allow={200, 503})
                if path == "/ops/ready" and resp.status_code == 503:
                    # Offline smoke may report degraded providers, but route must be honest JSON, not explode.
                    ready = resp.json()
                    if not isinstance(ready.get("blocking"), list):
                        raise AssertionError(f"/ops/ready malformed degraded response: {ready}")
                report["post_checked"].append(path)

            pack = client.post(
                "/memory/v2/context-pack",
                json={"user_id": "smoke-user", "query": "realnie działający projekt", "limit": 8, "max_chars": 4000},
                headers=headers,
            )
            _assert_okish(pack, label="POST /memory/v2/context-pack verify", allow={200})
            pack_body = pack.json()
            packed_text = json.dumps(pack_body, ensure_ascii=False).lower()
            if "realnie" not in packed_text and "projekt" not in packed_text:
                raise AssertionError(f"context pack did not include smoke memory: {pack_body}")
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["failures"].append(repr(exc))

    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("FUNCTIONAL_SMOKE:", "OK" if report["ok"] else "FAIL")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--db-path", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    return run(Path(args.repo), db_path=args.db_path, json_output=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
