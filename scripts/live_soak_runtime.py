#!/usr/bin/env python3
"""LIVE soak harness against a running AIHub backend.

Requires explicit opt-in:
  AIHUB_LIVE_SOAK=1
  AIHUB_LIVE_SOAK_MINUTES=15   (default 5)
  AIHUB_LIVE_SOAK_BASE=http://127.0.0.1:8080
  AIHUB_LIVE_SOAK_USER=...
  AIHUB_LIVE_SOAK_PASSWORD=...   (or AIHUB_LIVE_SOAK_COOKIE)

Does NOT invent provider success — records real HTTP/status/cost/fail rates.
Cost hard-stop: AIHUB_LIVE_SOAK_MAX_USD (default 2.0).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXTS = [
    "kim jesteś?",
    "elo",
    "Jak nazywa się mój pies Burek w pamięci?",
    "Zaplanuj trzyetapową migrację PostgreSQL i niczego nie wykonuj",
    "aktualna pogoda w Warszawie",
    "Ile to jest 2+2?",
    "Poprawka: lubię herbatę nie kawę",
]


def _req(method: str, url: str, *, data: dict | None = None, headers: dict | None = None) -> tuple[int, dict | str]:
    body = None
    hdrs = dict(headers or {})
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return int(resp.status), json.loads(raw)
            except Exception:
                return int(resp.status), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return int(e.code), json.loads(raw)
        except Exception:
            return int(e.code), raw


def main() -> int:
    if (os.getenv("AIHUB_LIVE_SOAK") or "").strip() not in {"1", "true", "yes", "on"}:
        print(json.dumps({"ok": False, "error": "Set AIHUB_LIVE_SOAK=1 to run live soak"}, indent=2))
        return 2

    base = (os.getenv("AIHUB_LIVE_SOAK_BASE") or "http://127.0.0.1:8080").rstrip("/")
    minutes = float(os.getenv("AIHUB_LIVE_SOAK_MINUTES", "5") or 5)
    max_usd = float(os.getenv("AIHUB_LIVE_SOAK_MAX_USD", "2.0") or 2.0)
    user = (os.getenv("AIHUB_LIVE_SOAK_USER") or "").strip()
    password = (os.getenv("AIHUB_LIVE_SOAK_PASSWORD") or "").strip()
    cookie = (os.getenv("AIHUB_LIVE_SOAK_COOKIE") or "").strip()

    headers: dict[str, str] = {}
    if cookie:
        headers["Cookie"] = cookie
    elif user and password:
        st, login = _req(
            "POST",
            f"{base}/auth/login",
            data={"username": user, "password": password},
        )
        if st >= 400:
            print(json.dumps({"ok": False, "error": "login_failed", "status": st, "body": login}, indent=2))
            return 1
        # Prefer cookie from Set-Cookie if available via manual header; else token field.
        if isinstance(login, dict) and login.get("token"):
            headers["Authorization"] = f"Bearer {login['token']}"
        elif isinstance(login, dict) and login.get("access_token"):
            headers["Authorization"] = f"Bearer {login['access_token']}"

    st, ready = _req("GET", f"{base}/ops/ready")
    if st != 200 or not (isinstance(ready, dict) and ready.get("ready")):
        print(json.dumps({"ok": False, "error": "ops_ready_failed", "status": st, "body": ready}, indent=2))
        return 1

    deadline = time.time() + minutes * 60.0
    turns = 0
    ok_n = 0
    fail_n = 0
    costs: list[float] = []
    errors: list[dict] = []
    i = 0
    while time.time() < deadline:
        text = TEXTS[i % len(TEXTS)]
        i += 1
        payload = {
            "user_id": user or "live_soak",
            "session_id": f"soak-{int(time.time())}",
            "message": text,
            "mode": "chat",
        }
        # Prefer BFF-compatible chat if present; fall back to /chat/turn
        st, body = _req("POST", f"{base}/chat/turn", data=payload, headers=headers)
        turns += 1
        if st >= 400 or (isinstance(body, dict) and body.get("ok") is False):
            fail_n += 1
            if len(errors) < 20:
                errors.append({"status": st, "text": text, "body": body if not isinstance(body, str) else body[:300]})
        else:
            ok_n += 1
            if isinstance(body, dict):
                tr = body.get("trace") if isinstance(body.get("trace"), dict) else {}
                try:
                    costs.append(float(tr.get("cost_usd") or 0.0))
                except Exception:
                    pass
        spent = sum(costs)
        if spent >= max_usd:
            break
        time.sleep(0.35)

    st_cost, cost_body = _req("GET", f"{base}/ops/cost/today?user_id={user or 'live_soak'}", headers=headers)
    out = {
        "ok": fail_n == 0 or (ok_n / max(1, turns) >= 0.85),
        "minutes": minutes,
        "turns": turns,
        "ok_turns": ok_n,
        "fail_turns": fail_n,
        "success_rate": round(ok_n / max(1, turns), 3),
        "cost_usd_sum_trace": round(sum(costs), 6),
        "cost_hard_stop_usd": max_usd,
        "ops_cost_today": cost_body if st_cost == 200 else {"status": st_cost, "body": cost_body},
        "errors_sample": errors,
        "note": "Live soak against real providers; not a unit test substitute.",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
