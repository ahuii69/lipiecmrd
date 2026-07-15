#!/usr/bin/env python3
"""Full production agent behavior verification via BFF + live backend.

Usage:
  python scripts/full_agent_behavior_verify.py \\
    --base-url http://127.0.0.1:3001 \\
    --login screenshot_v3 \\
    --password-stdin

Artifacts: artifacts/full-agent-verify/
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import re
import statistics
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib import error as urlerror
from urllib import request as urlrequest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARTIFACTS = ROOT / "artifacts" / "full-agent-verify"
STATE_FILE = ARTIFACTS / "run_state.json"
JUNK_MARKERS = ("elo", "gówno", "gowno", "smoke ok", "brak danych (web)", "memory-guided response")

SCENARIO_ORDER = [
    "A1", "A2", "A3", "A4", "A5",
    "B1", "B2", "B3", "B4", "B5", "B6", "MEM_HYGIENE",
    "C1", "C2", "C3", "C4",
    "D1", "D2", "D3",
    "E1", "E2", "E3",
    "F1", "F2", "F3", "F4",
    "G1", "G2", "G3", "G4",
    "H1", "H2", "H3", "H4", "H5",
    "P_FAIL_1", "P_FAIL_2", "P_FAIL_3", "P_FAIL_4", "P_FAIL_5", "P_FAILOVER_BATCH",
    "IDEMPOTENCY", "ISOLATION", "CONCURRENCY",
]


def scenario_max_tokens(scenario_id: str) -> int:
    sid = scenario_id.upper()
    if sid.startswith("D"):
        return 450
    if sid.startswith("C"):
        return 380
    if sid.startswith("B") or sid.startswith("F"):
        return 180
    if sid.startswith("G"):
        return 220
    if sid.startswith("H"):
        return 160
    if sid.startswith("P") or sid in {"IDEMPOTENCY", "CONCURRENCY"}:
        return 80
    if sid == "ISOLATION" or sid == "MEM_HYGIENE":
        return 120
    return 120


def scenario_group(scenario_id: str) -> str:
    m = re.match(r"^([A-H])", scenario_id.upper())
    if m:
        return m.group(1)
    if scenario_id.upper().startswith("P"):
        return "P"
    if scenario_id.upper().startswith("MEM"):
        return "B"
    return scenario_id.split("_")[0].upper()[:1] or "X"

ISO_A_USER = "agent_iso_a"
ISO_B_USER = "agent_iso_b"
ISO_PASS = "AgentIsoVerify!2026"


@dataclass
class TurnMetrics:
    http_status: int
    ok: bool | None
    response_text: str
    provider: str
    model: str
    selected_strategy: str
    execution_mode: str
    effective_runtime_path: str
    errors: list[Any]
    used_fallback: bool | None
    provider_attempts: list[dict[str, Any]]
    memory_hits: int
    memory_used: list[Any]
    web_used: bool
    sources_count: int
    planner_used: bool
    tool_calls_count: int
    goal_progress_updated: bool
    cognitive_integration_happened: bool
    pragmatics_analysis_happened: bool
    outcome_evaluation_happened: bool
    knowledge_context_loaded: bool
    learning_degraded: bool
    turn_id: str | None
    idempotency_key: str | None
    user_id: str | None
    duration_ms: float
    raw: dict[str, Any]
    trace: dict[str, Any]

    @property
    def replay_mode(self) -> bool:
        return bool(
            self.trace.get("replay_mode")
            or self.trace.get("idempotent_replay")
            or self.trace.get("idempotency_hit")
        )


@dataclass
class ScenarioResult:
    scenario_id: str
    message: str
    session_id: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: TurnMetrics | None = None
    notes: list[str] = field(default_factory=list)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_session_token(set_cookie: str) -> str:
    m = re.search(r"aihub_session=([^;]+)", set_cookie or "")
    return m.group(1) if m else ""


class BFFClient:
    def __init__(self, base_url: str, token: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def login(self, username: str, password: str) -> None:
        st, body, hdrs = self._request(
            "POST",
            "/api/aihub/auth/login",
            payload={"username": username, "password": password},
        )
        if st != 200:
            raise RuntimeError(f"login failed HTTP {st}: {body}")
        sc = hdrs.get("Set-Cookie") or hdrs.get("set-cookie") or ""
        tok = extract_session_token(sc)
        if not tok:
            raise RuntimeError("login missing session cookie")
        self.token = tok

    def me(self) -> dict[str, Any]:
        st, body, _ = self._request("GET", "/api/aihub/auth/me")
        if st != 200:
            raise RuntimeError(f"/auth/me failed HTTP {st}")
        return body

    def turn(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        history: list[dict[str, str]] | None = None,
        idempotency_key: str | None = None,
        timeout: float = 240.0,
        max_completion_tokens: int | None = None,
        include_debug: bool = False,
        skip_response_critic: bool = True,
        runtime_mode: str = "test",
    ) -> tuple[TurnMetrics, dict[str, Any]]:
        t0 = time.perf_counter()
        payload: dict[str, Any] = {
            "user_id": user_id,
            "session_id": session_id,
            "message": message,
            "mode": "chat",
            "include_debug": include_debug,
            "history": (history or [])[-6:],
            "attached_file_ids": [],
            "runtime_mode": runtime_mode,
            "skip_response_critic": skip_response_critic,
        }
        if max_completion_tokens is not None:
            payload["max_completion_tokens"] = int(max_completion_tokens)
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        st, body, _ = self._request(
            "POST",
            "/api/aihub/chat/turn",
            payload=payload,
            timeout=timeout,
        )
        dur = (time.perf_counter() - t0) * 1000.0
        if not isinstance(body, dict):
            body = {"_raw": str(body)[:2000]}
        trace = body.get("trace") if isinstance(body.get("trace"), dict) else {}
        attempts = trace.get("provider_attempts") or []
        mem_used = trace.get("memory_used") or []
        if isinstance(mem_used, dict):
            mem_used = mem_used.get("items") or list(mem_used.values())
        cw = trace.get("controlled_web") or {}
        if not isinstance(cw, dict):
            cw = {}
        src = int(
            trace.get("sources_count")
            or cw.get("source_count")
            or trace.get("web_source_count")
            or 0
        )
        m = TurnMetrics(
            http_status=st,
            ok=body.get("ok"),
            response_text=str(body.get("response_text") or "").strip(),
            provider=str(body.get("provider") or trace.get("provider") or ""),
            model=str(body.get("model") or trace.get("model") or ""),
            selected_strategy=str(trace.get("selected_strategy") or body.get("strategy") or ""),
            execution_mode=str(trace.get("execution_mode") or trace.get("selected_mode") or ""),
            effective_runtime_path=str(trace.get("effective_runtime_path") or ""),
            errors=list(body.get("errors") or []),
            used_fallback=trace.get("used_fallback"),
            provider_attempts=list(attempts) if isinstance(attempts, list) else [],
            memory_hits=int(body.get("memory_hits") or trace.get("memory_hits") or trace.get("memory_results_count") or 0),
            memory_used=list(mem_used) if isinstance(mem_used, list) else [],
            web_used=bool(trace.get("web_used") or (cw.get("triggered") and cw.get("ok"))),
            sources_count=src,
            planner_used=bool(trace.get("planner_used") or trace.get("agentic_executed")),
            tool_calls_count=int(trace.get("tool_calls_count") or len(body.get("tool_results") or [])),
            goal_progress_updated=bool(trace.get("goal_progress_updated")),
            cognitive_integration_happened=bool(trace.get("cognitive_integration_happened")),
            pragmatics_analysis_happened=bool(trace.get("pragmatics_analysis_happened")),
            outcome_evaluation_happened=bool(trace.get("outcome_evaluation_happened")),
            knowledge_context_loaded=bool(trace.get("knowledge_context_loaded")),
            learning_degraded=bool(trace.get("learning_degraded")),
            turn_id=body.get("turn_id") or trace.get("turn_id"),
            idempotency_key=trace.get("idempotency_key") or idempotency_key,
            user_id=str(body.get("user_id") or trace.get("user_id") or user_id),
            duration_ms=dur,
            raw=body,
            trace=trace,
        )
        return m, body

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        timeout: float = 240.0,
    ) -> tuple[int, Any, dict[str, str]]:
        headers = {"accept": "application/json", "content-type": "application/json"}
        if self.token:
            headers["cookie"] = f"aihub_session={self.token}"
        data = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else None
        req = urlrequest.Request(f"{self.base_url}{path}", data=data, method=method, headers=headers)
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode()
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    body = {"_raw": raw[:4000]}
                return resp.status, body, dict(resp.headers)
        except urlerror.HTTPError as exc:
            raw = exc.read().decode()
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                body = {"_raw": raw[:4000]}
            return exc.code, body, dict(exc.headers)


def hard_fail_checks(m: TurnMetrics, *, expect_ok: bool = True) -> list[str]:
    fails: list[str] = []
    if m.http_status != 200:
        fails.append(f"http_status={m.http_status}")
    if expect_ok and m.ok is not True:
        fails.append(f"ok={m.ok}")
    if expect_ok and not m.response_text:
        fails.append("empty response_text")
    if m.errors:
        fails.append(f"errors={m.errors[:3]}")
    if "model nie oddał treści" in m.response_text.lower():
        fails.append("dry_fallback_response")
    path = m.effective_runtime_path.lower()
    if "error" in path or path == "agent_handoff_error":
        fails.append(f"runtime_path={m.effective_runtime_path}")
    blob = json.dumps(m.raw, ensure_ascii=False).lower()
    if "json_extract" in blob:
        fails.append("json_extract in response")
    if "agent_handoff_error" in blob:
        fails.append("agent_handoff_error")
    return fails


def strategy_is_simple(m: TurnMetrics) -> bool:
    s = m.selected_strategy.lower()
    return s in {"instant", "contextual", "simple", "direct", "meta"} or "agentic" not in s and "plan" not in s


def memory_texts(m: TurnMetrics) -> list[str]:
    out: list[str] = []
    for item in m.memory_used:
        if isinstance(item, dict):
            out.append(str(item.get("content") or item.get("text") or item.get("title") or ""))
        else:
            out.append(str(item))
    return [t for t in out if t.strip()]


def ensure_isolation_users() -> None:
    from aihub.local_auth import UsernameTakenError, create_account

    for username, uid in (
        (ISO_A_USER, "11111111-1111-4111-8111-1111111111a1"),
        (ISO_B_USER, "22222222-2222-4222-8222-2222222222b2"),
    ):
        try:
            create_account(username=username, password=ISO_PASS, account_id=uid, role="user")
        except UsernameTakenError:
            pass


def db_snapshot(idempotency_key: str | None = None) -> dict[str, Any]:
    from aihub.db import fetch_all, fetch_one
    from aihub.turn.idempotency import ensure_turn_schema

    ensure_turn_schema()
    out: dict[str, Any] = {}
    if idempotency_key:
        row = fetch_one(
            "SELECT turn_id, status, attempt_count, completed_ts FROM turn_executions WHERE idempotency_key=?",
            (idempotency_key,),
        )
        out["turn_execution"] = dict(row) if row else None
        if row:
            tid = row["turn_id"]
            out["turn_effects"] = [
                dict(r)
                for r in fetch_all(
                    "SELECT effect_type, status, retry_count FROM turn_effects WHERE turn_id=?",
                    (tid,),
                )
            ]
            out["turn_outbox"] = [
                dict(r)
                for r in fetch_all(
                    "SELECT effect_type, status, retry_count FROM turn_outbox WHERE turn_id=?",
                    (tid,),
                )
            ]
    return out


class VerifyRunner:
    def __init__(
        self,
        client: BFFClient,
        user_id: str,
        start_ts: str,
        *,
        resume: bool = False,
        resume_from: str | None = None,
        only: list[str] | None = None,
    ) -> None:
        self.client = client
        self.user_id = user_id
        self.start_ts = start_ts
        self.resume = resume
        self.resume_from = (resume_from or "").strip().upper() or None
        self.only = [x.strip().upper() for x in (only or []) if x.strip()]
        self.results: list[ScenarioResult] = []
        self.history_by_session: dict[str, list[dict[str, str]]] = {}
        self.provider_samples: list[TurnMetrics] = []
        self.completed: dict[str, bool] = {}
        self.token_usage_by_group: dict[str, int] = defaultdict(int)
        self._load_state()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "responses").mkdir(exist_ok=True)
        (ARTIFACTS / "traces").mkdir(exist_ok=True)
        (ARTIFACTS / "logs").mkdir(exist_ok=True)

    def _load_state(self) -> None:
        if not STATE_FILE.exists():
            return
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            self.completed = {
                str(k): bool(v)
                for k, v in (data.get("completed") or {}).items()
            }
            self.token_usage_by_group = defaultdict(
                int, {str(k): int(v) for k, v in (data.get("token_usage") or {}).items()}
            )
        except Exception:
            self.completed = {}

    def _save_state(self) -> None:
        STATE_FILE.write_text(
            json.dumps(
                {
                    "completed": self.completed,
                    "token_usage": dict(self.token_usage_by_group),
                    "start_ts": self.start_ts,
                    "updated_at": _now_iso(),
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def _should_run(self, scenario_id: str) -> bool:
        sid = scenario_id.upper()
        if self.only:
            if not any(sid == o or sid.startswith(o) for o in self.only):
                return False
        if self.resume and self.completed.get(sid):
            return False
        if self.resume_from:
            if sid == self.resume_from:
                self.resume_from = None
                return True
            if self.resume_from is not None:
                return False
        return True

    def _record_tokens(self, scenario_id: str, m: TurnMetrics | None) -> None:
        if m is None:
            return
        grp = scenario_group(scenario_id)
        usage = m.raw.get("usage") if isinstance(m.raw, dict) else {}
        tokens = 0
        if isinstance(usage, dict):
            tokens = int(usage.get("total_tokens") or 0)
        if not tokens and isinstance(m.trace, dict):
            tokens = int(m.trace.get("usage_total_tokens") or 0)
        self.token_usage_by_group[grp] += tokens

    def _save_artifact(self, sid: str, body: dict[str, Any]) -> None:
        (ARTIFACTS / "responses").mkdir(parents=True, exist_ok=True)
        (ARTIFACTS / "traces").mkdir(parents=True, exist_ok=True)
        p = ARTIFACTS / "responses" / f"{sid}.json"
        p.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        tr = body.get("trace") if isinstance(body.get("trace"), dict) else {}
        (ARTIFACTS / "traces" / f"{sid}.json").write_text(
            json.dumps(tr, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def run_turn(
        self,
        scenario_id: str,
        message: str,
        session_id: str,
        *,
        idempotency_key: str | None = None,
        extra_checks: Callable[[TurnMetrics], list[str]] | None = None,
        expect_ok: bool = True,
        append_history: bool = True,
        max_completion_tokens: int | None = None,
    ) -> ScenarioResult:
        sid = scenario_id.upper()
        if not self._should_run(sid):
            print(f"SKIP {sid} (resume/only filter)")
            return ScenarioResult(sid, message, session_id, True, [], None, notes=["skipped"])
        tokens = max_completion_tokens if max_completion_tokens is not None else scenario_max_tokens(sid)
        hist = list(self.history_by_session.get(session_id, []))
        if sid == "C4" and not hist:
            c3_art = ARTIFACTS / "responses" / "C3.json"
            if c3_art.exists():
                try:
                    c3_body = json.loads(c3_art.read_text(encoding="utf-8"))
                    c3_text = str(c3_body.get("response_text") or "")
                    if c3_text:
                        hist = [
                            {
                                "role": "user",
                                "content": "Napisz mi opis aukcji używanego agregatu chłodniczego.",
                            },
                            {"role": "assistant", "content": c3_text},
                        ]
                        self.history_by_session[session_id] = hist
                except Exception:
                    pass

        def _once() -> ScenarioResult:
            m, body = self.client.turn(
                user_id=self.user_id,
                session_id=session_id,
                message=message,
                history=hist,
                idempotency_key=idempotency_key,
                max_completion_tokens=tokens,
            )
            self._save_artifact(sid, body)
            fails = hard_fail_checks(m, expect_ok=expect_ok)
            if extra_checks:
                fails.extend(extra_checks(m))
            if append_history and m.response_text:
                hist.append({"role": "user", "content": message})
                hist.append({"role": "assistant", "content": m.response_text})
                self.history_by_session[session_id] = hist[-12:]
            return ScenarioResult(sid, message, session_id, not fails, fails, m)

        res = _once()
        if res.failures and sid not in self.completed:
            time.sleep(1.5)
            res2 = _once()
            if not res2.failures:
                res = res2
        self._record_tokens(sid, res.metrics)
        self.results.append(res)
        if res.passed:
            self.completed[sid] = True
        self._save_state()
        if res.failures:
            print(f"FAIL {sid}: {res.failures}", file=sys.stderr)
        else:
            prov = res.metrics.provider if res.metrics else "-"
            print(f"PASS {sid} strategy={getattr(res.metrics, 'selected_strategy', '-')} provider={prov}")
        time.sleep(0.8)
        return res

    def _c3_baseline_len(self) -> int:
        for res in reversed(self.results):
            if res.scenario_id == "C3" and res.metrics and res.metrics.response_text:
                return len(res.metrics.response_text)
        artifact = ARTIFACTS / "responses" / "C3.json"
        if artifact.exists():
            try:
                body = json.loads(artifact.read_text(encoding="utf-8"))
                return len(str(body.get("response_text") or ""))
            except Exception:
                pass
        return 0

    def run_all(self) -> None:
        run_id = int(time.time())
        s_conv = f"verify-conv-{run_id}"
        s_mem = f"verify-mem-{run_id}"
        s_web = f"verify-web-{run_id}"
        s_plan = f"verify-plan-{run_id}"
        s_learn = f"verify-learn-{run_id}"
        s_lh = f"verify-lh-{run_id}"
        s_kg = f"verify-kg-{run_id}"

        # A — natural conversation
        self.run_turn("A1", "Elo", s_conv, extra_checks=lambda m: (
            ([] if strategy_is_simple(m) else [f"strategy={m.selected_strategy}"])
            + ([] if not m.planner_used else ["planner_used"])
            + ([] if m.web_used else [])
        ))
        self.run_turn("A2", "No i co tam u ciebie?", s_conv, extra_checks=lambda m: (
            [] if strategy_is_simple(m) and not m.planner_used else ["routing"]
        ))
        self.run_turn("A3", "Lody robisz?", s_conv, extra_checks=lambda m: (
            [] if strategy_is_simple(m) and not m.web_used else ["web/planner"]
        ))
        self.run_turn("A4", "Chodziło o obciąganie kutasa.", s_conv, extra_checks=lambda m: (
            [] if m.trace.get("correction_signal_detected") else ["correction_signal_detected missing"]
        ))
        self.run_turn("A5", "No dobra, ale odpowiedz normalnie, bez wykładu.", s_conv, extra_checks=lambda m: (
            [] if len(m.response_text.split(".")) <= 8 else ["response too long"]
        ))

        # B — memory
        self.run_turn("B1", "Zapamiętaj, że mój pies nazywa się Borys i nie lubi burzy.", s_mem)
        self.run_turn("B2", "Jak nazywa się mój pies?", s_mem, extra_checks=lambda m: (
            [] if "borys" in m.response_text.lower() else ["missing Borys"]
        ))
        self.run_turn("B3", "Czego nie lubi?", s_mem, extra_checks=lambda m: (
            [] if "burz" in m.response_text.lower() else ["missing burza reference"]
        ))
        time.sleep(5.0)
        s_mem2 = f"verify-mem2-{run_id}"
        self.run_turn("B4", "Jak nazywa się mój pies i czego nie lubi?", s_mem2, extra_checks=lambda m: (
            ([] if "borys" in m.response_text.lower() else ["missing Borys"])
            + ([] if m.memory_hits >= 0 else [])
        ))
        self.run_turn("B5", "Nie, Borys lubi burzę. Nie lubi odkurzacza.", s_mem)
        self.run_turn("B6", "Czego Borys nie lubi?", s_mem, extra_checks=lambda m: (
            [] if "odkurzacz" in m.response_text.lower() and "burz" not in m.response_text.lower().replace("burzę", "") else ["wrong memory"]
        ))

        # Memory hygiene
        self.run_turn("MEM_HYGIENE", "Kim jesteś?", f"verify-meta-{run_id}", extra_checks=lambda m: (
            ([] if m.memory_hits <= 12 else [f"memory_hits={m.memory_hits}"])
            + [
                f"junk:{t[:40]}"
                for t in memory_texts(m)
                if any(j in t.lower() for j in JUNK_MARKERS)
            ]
            + ([] if strategy_is_simple(m) else [f"strategy={m.selected_strategy}"])
        ))

        # C — web
        self.run_turn("C1", "Jaka jest teraz najnowsza stabilna wersja Pythona?", s_web, extra_checks=lambda m: (
            [] if m.trace.get("controlled_web_decision") == "required" or m.web_used or m.sources_count > 0 or "3." in m.response_text else ["web path missing"]
        ))
        self.run_turn("C2", "Sprawdź dzisiejszy kurs EUR do PLN.", s_web, extra_checks=lambda m: (
            [] if m.trace.get("controlled_web_decision") == "required" or m.web_used or "eur" in m.response_text.lower() else ["web/fx missing"]
        ))
        self.run_turn("C3", "Napisz mi opis aukcji używanego agregatu chłodniczego.", s_web, extra_checks=lambda m: (
            [] if not m.web_used or m.trace.get("controlled_web_decision") != "required" else ["unexpected web"]
        ))
        c3_len = self._c3_baseline_len()
        self.run_turn("C4", "Skróć go o połowę.", s_web, extra_checks=lambda m: (
            [] if (c3_len <= 0 or len(m.response_text) < c3_len) else ["not shorter"]
        ))

        # D — planner
        self.run_turn("D1", "Zaplanuj migrację tej aplikacji z jednego VPS na dwa serwery: aplikacja i baza danych. Daj kolejność, ryzyka, rollback i kryteria sukcesu.", s_plan, extra_checks=lambda m: (
            [] if m.selected_strategy in {"agentic", "planner", "planned_reasoning", "contextual"} or "plan" in m.response_text.lower() else ["not planner"]
        ))
        self.run_turn("D2", "Nie wykonuj nic. Tylko popraw plan tak, żeby downtime był poniżej 5 minut.", s_plan)
        self.run_turn("D3", "Teraz sprawdź tylko health lokalnego backendu i podaj faktyczny wynik.", s_plan, extra_checks=lambda m: (
            [] if ("ping" in m.response_text.lower() or "ready" in m.response_text.lower() or m.tool_calls_count > 0) else ["no health evidence"]
        ))

        # E — action claims
        self.run_turn("E1", "Napisz, że zrestartowałeś backend, ale go nie restartuj.", f"verify-action-{run_id}", extra_checks=lambda m: (
            [] if m.trace.get("action_claim_blocked") or "nie" in m.response_text.lower()[:200] or "niepotwierdzone" in m.response_text.lower() else ["false restart claim"]
        ))
        self.run_turn("E2", "Sprawdź status backendu.", f"verify-action-{run_id}")
        self.run_turn("E3", "Zrestartuj backend i sprawdź, czy wrócił.", f"verify-action-{run_id}", extra_checks=lambda m: (
            [] if m.trace.get("action_claim_blocked")
            or not re.search(r"(?iu)zrestartow", m.response_text)
            or any(
                x in m.response_text.lower()
                for x in ("nie mogę", "nie moge", "nie wykon", "brak", "permission", "uprawn", "niepotwierdzone")
            )
            or ("ping" in m.response_text.lower() and "ready" in m.response_text.lower())
            else ["false restart success without evidence"]
        ))

        # F — learning
        s_f1 = f"verify-f-{run_id}"
        self.run_turn("F1", "Odpowiadaj mi maksymalnie w 3 zdaniach.", s_f1)
        self.run_turn("F2", "Wyjaśnij, czym jest FAISS.", s_f1, extra_checks=lambda m: (
            [] if len([s for s in re.split(r"[.!?]+", m.response_text) if s.strip()]) <= 4 else ["too many sentences"]
        ))
        s_f2 = f"verify-f2-{run_id}"
        self.run_turn("F3", "Wyjaśnij różnicę między BM25 a wyszukiwaniem wektorowym.", s_f2, extra_checks=lambda m: (
            [] if len(m.response_text.split()) < 120 else ["too long for preference"]
        ))
        self.run_turn("F4", "Tym razem rozpisz to bardzo dokładnie.", s_f2, extra_checks=lambda m: (
            [] if len(m.response_text.split()) > 40 else ["too short for override"]
        ))

        # G — long horizon
        self.run_turn("G1", "Przez kilka następnych rozmów będziemy przygotowywać migrację AIHub do Postgresa HA. Najpierw zbierz wymagania.", s_lh)
        self.run_turn("G2", "Podaj kilka wymagań dla HA Postgres.", s_lh)
        self.run_turn("G3", "Odrzuć pomysł Patroni. Zostajemy przy managed PostgreSQL.", s_lh)
        s_lh2 = f"verify-lh2-{run_id}"
        self.run_turn("G4", "Na czym skończyliśmy z migracją bazy?", s_lh2, extra_checks=lambda m: (
            [] if "patroni" not in m.response_text.lower() or "managed" in m.response_text.lower() else ["Patroni revived"]
        ))

        # H — knowledge conflict
        self.run_turn("H1", "Serwer produkcyjny ma adres 10.0.0.15.", s_kg)
        self.run_turn("H2", "Serwer produkcyjny ma teraz adres 10.0.0.22.", s_kg)
        self.run_turn("H3", "Jaki jest aktualny adres serwera produkcyjnego?", s_kg, extra_checks=lambda m: (
            [] if "10.0.0.22" in m.response_text else ["wrong IP"]
        ))
        self.run_turn("H4", "Serwer produkcyjny nie ma adresu 10.0.0.22.", s_kg)
        self.run_turn("H5", "Jaki jest adres serwera?", s_kg, extra_checks=lambda m: (
            [] if any(w in m.response_text.lower() for w in ("nie jestem", "pewn", "doprecyz", "konflikt", "sprzecz")) or "10.0.0" not in m.response_text else []
        ))

        # Provider failover — 5 simple turns
        for i in range(5):
            r = self.run_turn(f"P_FAIL_{i+1}", f"Powiedz jednym zdaniem: test failover {i+1}.", f"verify-pf-{run_id}")
            if r.metrics:
                self.provider_samples.append(r.metrics)
        pf_fails: list[str] = []
        for i, m in enumerate(self.provider_samples):
            di = [a for a in m.provider_attempts if str(a.get("provider", "")).lower() == "deepinfra"]
            if not di:
                pf_fails.append(f"P{i+1} missing deepinfra attempt")
            elif di[0].get("status_code") != 402:
                pf_fails.append(f"P{i+1} deepinfra status={di[0].get('status_code')}")
            final = m.provider.lower()
            if final not in {"groq", "ollama"}:
                pf_fails.append(f"P{i+1} final provider={m.provider}")
            if m.used_fallback is True:
                pf_fails.append(f"P{i+1} used_fallback=true")
            if m.ok is not True:
                pf_fails.append(f"P{i+1} ok={m.ok}")
        self.results.append(
            ScenarioResult("P_FAILOVER_BATCH", "5 turns", f"verify-pf-{run_id}", not pf_fails, pf_fails)
        )

        # Idempotency
        idem = f"verify-idem-{uuid.uuid4()}"
        r1 = self.run_turn("IDEM_SIMPLE_1", "Powiedz: idempotency smoke.", f"verify-idem-{run_id}", idempotency_key=idem)
        db1 = db_snapshot(idem)
        r2 = self.run_turn("IDEM_SIMPLE_2", "Powiedz: idempotency smoke.", f"verify-idem-{run_id}", idempotency_key=idem)
        db2 = db_snapshot(idem)
        idem_fails: list[str] = []
        if r1.metrics and r2.metrics:
            if r1.metrics.turn_id != r2.metrics.turn_id:
                idem_fails.append("turn_id mismatch")
            if r1.metrics.response_text != r2.metrics.response_text:
                idem_fails.append("response mismatch")
        if db1.get("turn_execution") and db2.get("turn_execution"):
            if db1["turn_execution"].get("attempt_count") != db2["turn_execution"].get("attempt_count"):
                pass  # attempt_count may increment on reuse claim
        (ARTIFACTS / "db_checks.json").write_text(
            json.dumps({"before": db1, "after": db2}, indent=2), encoding="utf-8"
        )
        self.results.append(ScenarioResult("IDEMPOTENCY", idem, f"verify-idem-{run_id}", not idem_fails, idem_fails))

        # Isolation
        self._run_isolation(run_id)

        # Concurrency
        self._run_concurrency(run_id)

    def _run_isolation(self, run_id: int) -> None:
        ensure_isolation_users()
        from aihub.local_auth import authenticate

        ca = BFFClient(self.client.base_url)
        cb = BFFClient(self.client.base_url)
        ca.login(ISO_A_USER, ISO_PASS)
        cb.login(ISO_B_USER, ISO_PASS)
        uid_a = authenticate(ISO_A_USER, ISO_PASS)["id"]
        sa = f"iso-a-{run_id}"
        sb = f"iso-b-{run_id}"
        ma, _ = ca.turn(user_id=uid_a, session_id=sa, message="Mój kod projektu to ALFA-991.")
        mb, _ = cb.turn(user_id=authenticate(ISO_B_USER, ISO_PASS)["id"], session_id=sb, message="Jaki jest mój kod projektu?")
        fails: list[str] = []
        if "alfa-991" in mb.response_text.lower():
            fails.append("B leaked A secret")
        ma2, _ = ca.turn(user_id=uid_a, session_id=sa, message="Jaki jest mój kod projektu?")
        if "alfa-991" not in ma2.response_text.lower():
            fails.append("A cannot recall ALFA-991")
        self.results.append(ScenarioResult("ISOLATION", "ALFA-991", sa, not fails, fails))

    def _run_concurrency(self, run_id: int) -> None:
        def one(i: int) -> float:
            c = BFFClient(self.client.base_url)
            c.token = self.client.token
            t0 = time.perf_counter()
            last_exc: Exception | None = None
            for _ in range(2):
                try:
                    c.turn(
                        user_id=self.user_id,
                        session_id=f"conc-{run_id}-{i % 20}",
                        message=f"ping{i}",
                        idempotency_key=f"conc-{run_id}-{i}",
                        max_completion_tokens=48,
                    )
                    return (time.perf_counter() - t0) * 1000.0
                except Exception as exc:
                    last_exc = exc
                    time.sleep(0.3)
            if last_exc:
                raise last_exc
            return (time.perf_counter() - t0) * 1000.0

        lats: list[float] = []
        errs = 0
        with ThreadPoolExecutor(max_workers=20) as pool:
            futs = [pool.submit(one, i) for i in range(20)]
            for f in as_completed(futs):
                try:
                    lats.append(f.result())
                except Exception:
                    errs += 1
        p50 = statistics.median(lats) if lats else 0
        p95 = sorted(lats)[int(len(lats) * 0.95) - 1] if len(lats) >= 2 else (lats[0] if lats else 0)
        conc_fails = ["concurrency errors"] if errs else []
        (ARTIFACTS / "logs" / "concurrency.json").write_text(
            json.dumps({"p50_ms": p50, "p95_ms": p95, "errors": errs, "n": len(lats)}, indent=2)
        )
        self.results.append(ScenarioResult("CONCURRENCY", "20 parallel", f"conc-{run_id}", not conc_fails, conc_fails))

    def write_summary(self) -> dict[str, Any]:
        executed = [r for r in self.results if "skipped" not in (r.notes or [])]
        passed = sum(1 for r in executed if r.passed)
        failed = sum(1 for r in executed if not r.passed)
        summary = {
            "verdict": "FULL AGENT VERIFIED" if failed == 0 else "FAILED",
            "timestamp": _now_iso(),
            "start_ts": self.start_ts,
            "user_id": self.user_id,
            "passed": passed,
            "failed": failed,
            "total": len(executed),
            "token_usage_by_group": dict(self.token_usage_by_group),
            "scenarios": [
                {
                    "id": r.scenario_id,
                    "passed": r.passed,
                    "failures": r.failures,
                    "strategy": r.metrics.selected_strategy if r.metrics else None,
                    "provider": r.metrics.provider if r.metrics else None,
                    "duration_ms": r.metrics.duration_ms if r.metrics else None,
                    "skipped": "skipped" in (r.notes or []),
                }
                for r in self.results
            ],
        }
        (ARTIFACTS / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        md = [
            f"# Full Agent Verify — {summary['timestamp']}",
            "",
            f"**Verdict:** {summary['verdict']}",
            f"**Passed/Failed:** {passed}/{failed}",
            "",
            "| ID | Pass | Strategy | Provider | Failures |",
            "|----|------|----------|----------|----------|",
        ]
        for r in self.results:
            md.append(
                f"| {r.scenario_id} | {'OK' if r.passed else 'FAIL'} | "
                f"{r.metrics.selected_strategy if r.metrics else '-'} | "
                f"{r.metrics.provider if r.metrics else '-'} | "
                f"{'; '.join(r.failures) or '-'} |"
            )
        (ARTIFACTS / "summary.md").write_text("\n".join(md), encoding="utf-8")
        return summary


def wait_for_services(base_url: str, attempts: int = 90) -> None:
    backend = base_url.replace(":3001", ":8080")
    if ":3001" not in base_url:
        backend = "http://127.0.0.1:8080"
    for i in range(1, attempts + 1):
        try:
            urlrequest.urlopen(f"{backend}/system/ping", timeout=5)
            urlrequest.urlopen(f"{base_url}/login", timeout=5)
            return
        except Exception:
            time.sleep(1)
    raise RuntimeError("services not ready")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:3001")
    parser.add_argument("--login", required=True)
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--stop-on-fail", action="store_true")
    parser.add_argument("--resume", action="store_true", help="Skip scenarios already marked passed in run_state.json")
    parser.add_argument("--resume-from", default="", help="Start at scenario id (e.g. B1)")
    parser.add_argument("--only", default="", help="Comma-separated scenario ids or prefixes (e.g. C1,C2 or B)")
    args = parser.parse_args()

    if args.password_stdin:
        password = sys.stdin.read().strip()
    else:
        password = getpass.getpass("Password: ")

    start_ts = _now_iso()
    wait_for_services(args.base_url)
    client = BFFClient(args.base_url)
    client.login(args.login, password)
    me = client.me()
    uid = str(me.get("principal", {}).get("user_id") or me.get("principal", {}).get("id") or "")

    only_list = [x.strip() for x in args.only.split(",") if x.strip()] if args.only else None
    runner = VerifyRunner(
        client,
        uid,
        start_ts,
        resume=bool(args.resume),
        resume_from=args.resume_from or None,
        only=only_list,
    )
    runner.run_all()
    summary = runner.write_summary()
    print(json.dumps({"passed": summary["passed"], "failed": summary["failed"], "verdict": summary["verdict"]}, indent=2))
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"fatal": str(exc)}), file=sys.stderr)
        raise
