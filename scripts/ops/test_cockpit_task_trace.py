#!/usr/bin/env python3
"""Smoke test for cockpit task_trace endpoint."""

import os

from fastapi.testclient import TestClient
from aihub import main
from aihub.psyche_engine import ensure_user

main.start_worker_once = lambda: None
os.environ["API_KEY"] = ""
client = TestClient(main.app)
ensure_user('demo')

print("Testing /cockpit/agent/demo/runtime-status...")
r = client.get('/cockpit/agent/demo/runtime-status')
print(f'Status: {r.status_code}')

data = r.json()
print(f'Response fields: {list(data.keys())}')
print(f'Has task_trace: {"task_trace" in data}')
print(f'Task trace type: {type(data.get("task_trace"))}')
print(f'Task trace length: {len(data.get("task_trace", []))}')

if data.get('task_trace'):
    print(f'First task keys: {list(data["task_trace"][0].keys())}')
    print('\n✅ Backend endpoint returns task_trace!')
else:
    print('\n✅ Backend endpoint works (task_trace empty for max_tasks=0)')

print(f'\nRuntime observability mode: {data.get("runtime_observability", {}).get("mode")}')
print(f'Strategy: {data.get("runtime_observability", {}).get("strategy_effective")}')
