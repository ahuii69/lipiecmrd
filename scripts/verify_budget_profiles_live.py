#!/usr/bin/env python3
"""Live BFF budget-profile verification suite (26.07).

Usage:
  printf '%s' "$PASS" | python scripts/verify_budget_profiles_live.py \\
    --username screenshot_v3 --password-stdin \\
    --output artifacts/full-agent-verify/profile26
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.full_agent_behavior_verify import BFFClient, wait_for_services  # noqa: E402
from scripts.validate_turn_trace import validate_doc  # noqa: E402


@dataclass
class Scenario:
    id: str
    group: str
    message: str
    new_session: bool = False
    expect_profile: str | None = None
    expect_web: bool | None = None
    max_tokens: int = 600
    tag: str = ""
    depends_on: str | None = None


SCENARIOS: list[Scenario] = [
    Scenario("M1", "meta", "Powiedz krótko, kim jesteś i jak działasz.", expect_profile="meta_light", max_tokens=256),
    Scenario("M2", "meta", "Jaki provider i model obsłużył tę odpowiedź?", expect_profile="meta_light", max_tokens=256),
    Scenario("M3", "meta", "A jak wcześniej opisywałeś swoje działanie?", expect_profile="meta_light", max_tokens=256),
    Scenario("C1", "casual", "Elo Mordzix", expect_profile="casual_light", max_tokens=256),
    Scenario("C2", "casual", "No i git XD", expect_profile="casual_light", max_tokens=256),
    Scenario("C3", "casual", "Ale ty czasem odpierdalasz.", expect_profile="casual_light", max_tokens=256),
    Scenario("C4", "casual", "Dzięki.", expect_profile="casual_light", max_tokens=256),
    Scenario("X1", "contextual", "Zapamiętaj, że mój testowy pies do tego audytu nazywa się Borys-Profile26.", expect_profile="contextual", max_tokens=400, tag="remember"),
    Scenario("X2", "contextual", "Jak nazywa się mój testowy pies do audytu?", expect_profile="contextual", new_session=True, max_tokens=400, tag="recall"),
    Scenario("X3", "contextual", "Borys-Profile26 nie lubi burzy.", expect_profile="contextual", max_tokens=400),
    Scenario("X4", "contextual", "Nie, poprawka: Borys-Profile26 lubi burzę, ale nie lubi odkurzacza.", expect_profile="contextual", max_tokens=400, tag="correction"),
    Scenario("X5", "contextual", "Czego Borys-Profile26 nie lubi?", expect_profile="contextual", new_session=True, max_tokens=400, tag="recall"),
    Scenario("P1", "procedural", "Dla testów Profile26 zapamiętaj procedurę debugowania serwera: najpierw logi, potem diagnoza, potem poprawka, na końcu komendy weryfikacyjne.", expect_profile="contextual", max_tokens=500, tag="proc_store"),
    Scenario("P2", "procedural", "Backend zwraca 502. Jak mam to zdebugować?", expect_profile="contextual", new_session=True, max_tokens=700, tag="proc_use"),
    Scenario("P3", "procedural", "Zmień procedurę Profile26: najpierw sprawdzenie portów, potem logi, diagnoza, poprawka i weryfikacja.", expect_profile="contextual", max_tokens=500, tag="proc_fix"),
    Scenario("P4", "procedural", "Backend ponownie zwraca 502. Podaj procedurę.", expect_profile="contextual", new_session=True, max_tokens=700, tag="proc_use2"),
    Scenario("U1", "user_model", "W zwykłych rozmowach odpowiadaj mi bardzo krótko.", max_tokens=300, tag="pref"),
    Scenario("U2", "user_model", "Co to jest idempotencja?", new_session=True, max_tokens=400, tag="pref_check"),
    Scenario("U3", "user_model", "W technicznych tematach nie skracaj. Dawaj pełne szczegóły i komendy.", max_tokens=400, tag="pref_tech"),
    Scenario("U4", "user_model", "Jak sprawdzić, jaki proces słucha na porcie 8080 w Linuksie?", new_session=True, max_tokens=700, tag="pref_tech_check"),
    Scenario("R1", "research", "Jaka jest aktualna stabilna wersja Pythona? Sprawdź w aktualnym źródle.", expect_profile="research", expect_web=True, max_tokens=800),
    Scenario("R2", "research", "Jaki jest teraz kurs EUR do PLN? Podaj źródło i czas danych.", expect_profile="research", expect_web=True, max_tokens=800),
    Scenario("R3", "research", "Jaka jest teraz pogoda w Warszawie?", expect_profile="research", expect_web=True, max_tokens=800),
    Scenario("R4", "research", "Jaki jest najnowszy stabilny release FastAPI?", expect_profile="research", expect_web=True, max_tokens=800),
    Scenario("A1", "agentic", "Napisz plan migracji PostgreSQL na nowy VPS: etapy, zależności, ryzyka, rollback i komendy weryfikacyjne. Niczego nie wykonuj.", expect_profile="agentic", max_tokens=1200, tag="plan"),
    Scenario("A2", "agentic", "Śledź ten plan jako zadanie długoterminowe Profile26.", expect_profile="agentic", max_tokens=600, tag="goal"),
    Scenario("A3", "agentic", "Jaki jest aktualny stan zadania Profile26 i co jest następnym krokiem?", max_tokens=600, tag="goal_status"),
    Scenario("AC1", "action_claim", "Wykonaj teraz migrację PostgreSQL na moim serwerze.", max_tokens=800, tag="action"),
    # Uses live provider chain: DeepInfra (often 402) → Groq. No global key mutation.
    Scenario(
        "F1",
        "failover",
        "Powiedz krótko, kim jesteś i jak działasz. (Profile26 failover probe)",
        expect_profile="meta_light",
        max_tokens=256,
        tag="failover",
    ),
]


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    return float(statistics.median(xs))


def _p95(xs: list[float]) -> float | None:
    if not xs:
        return None
    if len(xs) < 5:
        return float(max(xs))
    ys = sorted(xs)
    idx = int(round(0.95 * (len(ys) - 1)))
    return float(ys[idx])


def _metrics(tr: dict[str, Any], duration_ms: float) -> dict[str, Any]:
    return {
        "prompt_tokens": tr.get("prompt_tokens"),
        "completion_tokens": tr.get("completion_tokens"),
        "total_tokens": tr.get("total_tokens") or tr.get("usage_total_tokens"),
        "duration_ms": duration_ms,
        "provider_attempt_count": tr.get("provider_attempt_count"),
        "provider": tr.get("provider"),
        "model": tr.get("model"),
        "budget_profile": tr.get("budget_profile"),
        "turn_value_class": tr.get("turn_value_class"),
        "writeback_policy": tr.get("writeback_policy"),
        "memory_lookup_happened": tr.get("memory_lookup_happened"),
        "web_used": tr.get("web_used") or tr.get("controlled_web_triggered"),
        "planner_used": tr.get("planner_used") or tr.get("agentic_executed"),
        "tool_iterations": tr.get("tool_iterations"),
    }


def _validate_scenario(sc: Scenario, body: dict[str, Any], tr: dict[str, Any]) -> list[str]:
    fails: list[str] = []
    if body.get("ok") is not True:
        fails.append("ok!=true")
    if sc.expect_profile and tr.get("budget_profile") != sc.expect_profile:
        # research/agentic may be downgraded by learning — still require not meta/casual for heavy
        if sc.expect_profile in ("research", "agentic", "contextual"):
            if tr.get("budget_profile") in ("meta_light", "casual_light"):
                fails.append(f"budget_profile={tr.get('budget_profile')} starved")
            elif sc.expect_profile == "research" and not (
                tr.get("web_used") or tr.get("controlled_web_triggered") or tr.get("budget_profile") == "research"
            ):
                fails.append("research_web_missing")
        else:
            fails.append(f"budget_profile={tr.get('budget_profile')}")
    if sc.expect_profile == "meta_light":
        pt = tr.get("prompt_tokens")
        if pt is not None and int(pt) > 1200:
            fails.append(f"prompt_tokens={pt}")
        if int(tr.get("tool_iterations") or 0) != 0:
            fails.append("tool_iterations")
        if tr.get("memory_lookup_happened"):
            fails.append("memory_lookup")
    if sc.expect_profile == "casual_light":
        pt = tr.get("prompt_tokens")
        if pt is not None and int(pt) > 1600:
            fails.append(f"prompt_tokens={pt}")
        if tr.get("memory_lookup_happened"):
            fails.append("memory_lookup")
        if tr.get("writeback_policy") not in ("minimal", None):
            # allow missing only if turn_value trivial implied
            if tr.get("turn_value_class") != "trivial":
                fails.append(f"writeback={tr.get('writeback_policy')}")
    if sc.tag == "action":
        low = str(body.get("response_text") or "").lower()
        false_claim = any(
            x in low
            for x in (
                "wykonałem migrację",
                "wykonano migrację",
                "migracja zakończona sukcesem",
                "successfully migrated",
                "migracja zakończona",
            )
        )
        if false_claim and int(tr.get("tool_calls_executed") or 0) == 0:
            fails.append("false_execution_claim")
        # ok=false with explicit non-execution / ungrounded web is acceptable for AC
        # as long as there is no false success claim.
        fails = [f for f in fails if f != "ok!=true"]
        if body.get("ok") is not True and false_claim:
            fails.append("ok!=true_with_claim")
        if "suchy meldunek" in low:
            fails.append("action_stub_response")
        if int(tr.get("tool_calls_executed") or 0) == 0 and not any(
            x in low
            for x in (
                "nie wykona",
                "nie mam dostępu",
                "nie mam dostepu",
                "brak dostępu",
                "brak dostepu",
                "nie mogę wykonać",
                "nie moge wykonac",
            )
        ):
            fails.append("action_missing_explicit_non_execution")
    if sc.tag == "recall":
        low = str(body.get("response_text") or "").lower()
        # Correction chain X4→X5 must surface the superseded dislike.
        if "borys" in sc.message.lower() and "nie lubi" in sc.message.lower():
            if "odkurz" not in low:
                fails.append("correction_recall_missing_odkurzacz")
            if "nie lubi burz" in low or "nie lubi burzy" in low:
                fails.append("correction_not_superseded_storm_dislike")
    if sc.tag == "failover":
        attempts = tr.get("provider_attempts") or []
        if not isinstance(attempts, list):
            attempts = []
        if int(tr.get("provider_attempt_count") or len(attempts) or 0) < 2:
            fails.append("failover_attempts<2")
        if not tr.get("provider_failover_happened"):
            # Accept ordered fail→success across distinct providers even if flag missing.
            statuses = [a.get("ok") for a in attempts if isinstance(a, dict)]
            providers = [str(a.get("provider") or "") for a in attempts if isinstance(a, dict)]
            if not (False in statuses and True in statuses and len(set(providers)) >= 2):
                fails.append("failover_not_observed")
        if body.get("ok") is not True:
            fails.append("failover_final_not_ok")
    if sc.expect_web:
        if not (tr.get("controlled_web_triggered") or tr.get("web_used") or (tr.get("controlled_web") or {}).get("triggered")):
            # strategy may still be research with failed web — record soft fail
            fails.append("web_not_triggered")
    if sc.tag == "plan" or (sc.expect_profile == "agentic" and sc.id == "A1"):
        if tr.get("budget_profile") != "agentic" and tr.get("selected_strategy") not in (
            "agentic",
        ):
            fails.append(f"agentic_not_selected:{tr.get('budget_profile')}/{tr.get('selected_strategy')}")
        low = str(body.get("response_text") or "").lower()
        if "suchy meldunek" in low:
            fails.append("agentic_plan_stub_response")
        need = ("etap", "ryzyk", "rollback", "weryfik")
        if sum(1 for k in need if k in low) < 2:
            fails.append("agentic_plan_missing_structure")
    if sc.tag == "proc_use":
        low = str(body.get("response_text") or "").lower()
        # First stored procedure is logs-first; response structure must reflect that.
        log_pos = min(
            (i for i in (low.find("log"), low.find("logi"), low.find("journal")) if i >= 0),
            default=-1,
        )
        port_pos = min(
            (i for i in (low.find("port"), low.find("portów"), low.find("portow")) if i >= 0),
            default=-1,
        )
        if log_pos < 0:
            fails.append("proc_use_missing_logs_step")
        elif port_pos >= 0 and port_pos < log_pos:
            fails.append("proc_use_ports_before_logs")
    if sc.tag == "proc_use2":
        low = str(body.get("response_text") or "").lower()
        port_pos = min(
            (i for i in (low.find("port"), low.find("portów"), low.find("portow")) if i >= 0),
            default=-1,
        )
        log_pos = min(
            (i for i in (low.find("log"), low.find("logi")) if i >= 0),
            default=-1,
        )
        if port_pos < 0:
            fails.append("proc_use2_missing_ports_step")
        elif log_pos >= 0 and log_pos < port_pos:
            fails.append("proc_use2_logs_before_ports")
    if sc.tag in ("proc_store", "proc_fix"):
        if not (
            tr.get("procedural_memory_stored")
            or tr.get("procedural_memory_id")
            or "procedural" in (tr.get("writebacks_executed") or [])
        ):
            # Accept semantic/memory write of the instruction text as weaker signal.
            if str(tr.get("turn_value_class") or "") not in ("procedural", "corrective", "informative"):
                fails.append("proc_not_persisted")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:3001")
    ap.add_argument("--username", default="screenshot_v3")
    ap.add_argument("--password-stdin", action="store_true")
    ap.add_argument("--output", default="artifacts/full-agent-verify/profile26")
    ap.add_argument("--only", default="", help="comma groups: meta,casual,contextual,...")
    ap.add_argument("--skip-replay", action="store_true")
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--list-scenarios", action="store_true")
    ap.add_argument("--baseline", action="store_true", help="Write under profile26-baseline")
    args = ap.parse_args()

    if args.list_scenarios:
        for sc in SCENARIOS:
            print(f"{sc.id}\t{sc.group}\t{sc.message[:70]}")
        return 0

    password = ""
    if args.password_stdin:
        password = sys.stdin.read().rstrip("\n")
    if not password:
        print("missing password on stdin", file=sys.stderr)
        return 2

    out_root = Path(args.output)
    if args.baseline:
        out_root = Path("artifacts/full-agent-verify/profile26-baseline")
    out_root.mkdir(parents=True, exist_ok=True)

    only = {p.strip() for p in args.only.split(",") if p.strip()}
    scenarios = [s for s in SCENARIOS if not only or s.group in only]

    wait_for_services(args.base_url if args.base_url.startswith("http") else f"http://{args.base_url}")
    # BFFClient expects host root; strip trailing /api/aihub if provided
    base = args.base_url.rstrip("/")
    if base.endswith("/api/aihub"):
        base = base[: -len("/api/aihub")]
    client = BFFClient(base)
    client.login(args.username, password)
    # Never persist password
    del password
    uid = client.me()["principal"]["user_id"]
    run_id = str(uuid.uuid4())
    marker = f"Profile26-{run_id[:8]}"

    state_path = out_root / "run_state.json"
    done: set[str] = set()
    if args.resume and state_path.exists():
        prev = json.loads(state_path.read_text())
        done = set(prev.get("done") or [])
        if prev.get("run_id"):
            run_id = str(prev["run_id"])
        if prev.get("marker"):
            marker = str(prev["marker"])
        else:
            marker = f"Profile26-{run_id[:8]}"
    else:
        state_path.write_text(
            json.dumps({"done": [], "run_id": run_id, "marker": marker}),
            encoding="utf-8",
        )
    results: list[dict[str, Any]] = []
    session_by_group: dict[str, str] = {}
    overall_fail = False

    for sc in scenarios:
        if sc.id in done:
            continue
        group_dir = out_root / sc.group
        group_dir.mkdir(parents=True, exist_ok=True)
        if sc.new_session or sc.group not in session_by_group:
            session_by_group[sc.group] = f"p26-{sc.group}-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        sid = session_by_group[sc.group]
        ikey = f"p26-{sc.id}-{run_id}-{uuid.uuid4().hex[:10]}"
        msg = sc.message.replace("Profile26", marker)

        req = {
            "scenario_id": sc.id,
            "group": sc.group,
            "user_id": uid,
            "session_id": sid,
            "message": msg,
            "idempotency_key": ikey,
            "max_completion_tokens": sc.max_tokens,
            "test_namespace": "profile26",
            "test_run_id": run_id,
            # no secrets
        }
        (group_dir / f"{sc.id}_request.json").write_text(json.dumps(req, indent=2, ensure_ascii=False), encoding="utf-8")

        t0 = time.perf_counter()
        try:
            m, body = client.turn(
                user_id=uid,
                session_id=sid,
                message=msg,
                idempotency_key=ikey,
                timeout=args.timeout,
                max_completion_tokens=sc.max_tokens,
                include_debug=True,
            )
        except Exception as exc:  # noqa: BLE001
            overall_fail = True
            err = {"ok": False, "error": str(exc)[:500], "scenario": sc.id}
            (group_dir / f"{sc.id}_response.json").write_text(json.dumps(err, indent=2), encoding="utf-8")
            print(json.dumps({"id": sc.id, "fail": ["exception", str(exc)[:120]]}, ensure_ascii=False))
            if args.fail_fast:
                break
            continue
        dur = (time.perf_counter() - t0) * 1000.0
        tr = m.trace if isinstance(m.trace, dict) else {}
        # Persist without duplicating huge body twice when possible
        response_doc = {
            "ok": m.ok,
            "http_status": m.http_status,
            "response_text": m.response_text,
            "provider": m.provider,
            "model": m.model,
            "turn_id": m.turn_id,
            "errors": m.errors,
            "trace": tr,
            "scenario_id": sc.id,
            "test_run_id": run_id,
        }
        (group_dir / f"{sc.id}_response.json").write_text(json.dumps(response_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        (group_dir / f"{sc.id}_trace.json").write_text(json.dumps(tr, indent=2, ensure_ascii=False), encoding="utf-8")
        metrics = _metrics(tr, dur)
        (group_dir / f"{sc.id}_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

        fails = _validate_scenario(sc, response_doc, tr)
        trace_v = validate_doc(response_doc, artifact=f"{sc.group}/{sc.id}")
        validation = {"scenario_fails": fails, "trace_violations": trace_v, "pass": not fails and not trace_v}
        (group_dir / f"{sc.id}_validation.json").write_text(json.dumps(validation, indent=2, ensure_ascii=False), encoding="utf-8")

        effects = {
            "writebacks_executed": tr.get("writebacks_executed"),
            "writebacks_skipped": tr.get("writebacks_skipped"),
            "memory_v2_new_items_count": tr.get("memory_v2_new_items_count"),
            "selected_goal": tr.get("selected_goal"),
            "turn_value_class": tr.get("turn_value_class"),
        }
        (group_dir / f"{sc.id}_effects.json").write_text(json.dumps(effects, indent=2, ensure_ascii=False), encoding="utf-8")

        row = {"id": sc.id, "group": sc.group, "pass": validation["pass"], "fails": fails, "trace_violations": [v["code"] for v in trace_v], **metrics}
        results.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
        if not validation["pass"]:
            overall_fail = True
            if args.fail_fast:
                break

        # Replay representative turns
        if not args.skip_replay and sc.id in {"M1", "C1", "X2", "R1", "A1"}:
            m2, body2 = client.turn(
                user_id=uid,
                session_id=sid,
                message=msg,
                idempotency_key=ikey,
                timeout=args.timeout,
                max_completion_tokens=sc.max_tokens,
                include_debug=True,
            )
            tr2 = m2.trace if isinstance(m2.trace, dict) else {}
            replay = {
                "same_turn": m2.turn_id == m.turn_id,
                "same_text": m2.response_text == m.response_text,
                "idempotency_hit": tr2.get("idempotency_hit") or body2.get("idempotency_hit"),
                "provider_attempt_count": tr2.get("provider_attempt_count"),
                "writebacks_executed": tr2.get("writebacks_executed"),
            }
            (out_root / "replay" ).mkdir(parents=True, exist_ok=True)
            (out_root / "replay" / f"{sc.id}_replay.json").write_text(json.dumps(replay, indent=2), encoding="utf-8")
            if not (replay["same_turn"] and replay.get("idempotency_hit")):
                overall_fail = True
                print(json.dumps({"id": sc.id, "replay_fail": replay}, ensure_ascii=False), flush=True)

        done.add(sc.id)
        state_path.write_text(json.dumps({"done": sorted(done), "run_id": run_id, "marker": marker}), encoding="utf-8")
        time.sleep(1.2)

    # Aggregate metrics per group
    summary: dict[str, Any] = {"run_id": run_id, "marker": marker, "user_id": uid, "overall_pass": not overall_fail, "groups": {}}
    by_g: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        by_g.setdefault(r["group"], []).append(r)
    for g, rows in by_g.items():
        prompts = [float(x["prompt_tokens"]) for x in rows if x.get("prompt_tokens") is not None]
        totals = [float(x["total_tokens"]) for x in rows if x.get("total_tokens") is not None]
        durs = [float(x["duration_ms"]) for x in rows if x.get("duration_ms") is not None]
        summary["groups"][g] = {
            "n": len(rows),
            "passed": sum(1 for x in rows if x.get("pass")),
            "prompt_median": _median(prompts),
            "prompt_p95": _p95(prompts),
            "total_median": _median(totals),
            "total_p95": _p95(totals),
            "duration_median_ms": _median(durs),
            "duration_p95_ms": _p95(durs),
            "samples_note": "p95≈max when n<5" if len(prompts) < 5 else "ok",
        }
    (out_root / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"summary": summary["groups"], "overall_pass": summary["overall_pass"]}, ensure_ascii=False, indent=2))
    return 1 if overall_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
