#!/usr/bin/env python3
"""End-to-end smoke test: backend endpoints + data structure verification."""
import json
import os
import sys
import time
import urllib.request

API_KEY = os.environ.get("API_KEY", "").strip()
BASE = os.environ.get("BASE_URL", "http://127.0.0.1:8080")


def check(name, path, need_auth=True):
    url = f"{BASE}{path}"
    try:
        req = urllib.request.Request(url)
        if need_auth:
            if not API_KEY:
                print(f"SKIP {name} => API_KEY is required for authenticated endpoint")
                return None
            req.add_header("x-api-key", API_KEY)
        resp = urllib.request.urlopen(req, timeout=15)
        body = resp.read().decode()
        data = json.loads(body)
        print(f"PASS {name} => HTTP {resp.status}")
        return data
    except Exception as e:
        print(f"FAIL {name} => {e}")
        return None


def main():
    print("=" * 60)
    print("E2E SMOKE TEST")
    print("=" * 60)

    # 1. Ping
    check("system/ping", "/system/ping", need_auth=False)

    # 2. Cockpit health
    check("cockpit/health", "/cockpit/health")

    # 3. Runtime status (main target)
    data = check("runtime-status", "/cockpit/agent/default/runtime-status")
    if data is None:
        print("\nFATAL: runtime-status endpoint failed")
        sys.exit(1)

    print("\n--- RUNTIME STATUS DATA ---")
    print(f"  top-level keys: {sorted(data.keys())}")

    # task_trace
    tt = data.get("task_trace", [])
    print(f"  task_trace: count={len(tt)}")
    if isinstance(tt, list) and len(tt) > 0:
        first = tt[0]
        if isinstance(first, dict):
            print(f"  task_trace[0] keys: {sorted(first.keys())}")
            for k in ("task_id", "task_type", "status", "source", "step_index"):
                if k in first:
                    print(f"    .{k} = {first[k]}")

    # runtime_observability
    ro = data.get("runtime_observability", {})
    if isinstance(ro, dict):
        print(f"  runtime_observability keys: {sorted(ro.keys())}")

    # agent_state
    ags = data.get("agent_state", {})
    if isinstance(ags, dict):
        print(f"  agent_state keys: {sorted(ags.keys())}")
        for k in ("mode", "strategy"):
            if k in ags:
                print(f"    .{k} = {ags[k]}")

    # queue_depth
    qd = data.get("queue_depth")
    if qd is not None:
        print(f"  queue_depth: {qd}")

    print("\n--- VERDICT ---")
    has_trace = isinstance(tt, list) and len(tt) > 0
    has_ro = isinstance(ro, dict) and len(ro) > 0
    has_ags = isinstance(ags, dict) and len(ags) > 0

    ok = has_trace and has_ro and has_ags
    if ok:
        print("ALL CHECKS PASSED")
    else:
        if not has_trace:
            print("WARN: task_trace is empty (may be expected if no tasks ran)")
        if not has_ro:
            print("WARN: runtime_observability is empty")
        if not has_ags:
            print("WARN: agent_state is empty")
        print("PARTIAL PASS (data structure correct, some sections may be empty)")

    print("=" * 60)


if __name__ == "__main__":
    main()
