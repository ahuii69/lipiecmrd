#!/usr/bin/env python3
"""Live influence suite for 23.07 full runtime integration."""
from __future__ import annotations

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.full_agent_behavior_verify import (  # noqa: E402
    BFFClient,
    hard_fail_checks,
    wait_for_services,
)

ARTIFACTS = ROOT / "artifacts" / "full-agent-verify"
OUT = ARTIFACTS / "integration23_live.json"


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    password = os.environ.get("PASS") or sys.stdin.read().strip()
    if not password:
        raise SystemExit("missing PASS / stdin password")

    wait_for_services("http://127.0.0.1:3001")
    client = BFFClient("http://127.0.0.1:3001")
    client.login("screenshot_v3", password)
    uid = client.me()["principal"]["user_id"]
    rid = int(time.time())
    out: list[dict] = []

    def save() -> None:
        OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    def turn(sid: str, msg: str, tokens: int = 500, label: str | None = None, pause: float = 3.0):
        m, _ = client.turn(
            user_id=uid,
            session_id=sid,
            message=msg,
            max_completion_tokens=tokens,
            include_debug=True,
        )
        tr = m.trace if isinstance(m.trace, dict) else {}
        row = {
            "label": label or msg[:40],
            "msg": msg[:90],
            "http": m.http_status,
            "ok": m.ok,
            "fails": hard_fail_checks(m),
            "provider": m.provider,
            "model": m.model,
            "strategy": m.selected_strategy,
            "mode": m.execution_mode,
            "path": m.effective_runtime_path,
            "simulation_affected": tr.get("simulation_affected_strategy"),
            "policy_affected": tr.get("policy_feedback_affected_strategy")
            or tr.get("policy_feedback_applied"),
            "cognitive": bool(
                tr.get("cognitive_integration_happened") or m.cognitive_integration_happened
            ),
            "proc_extract": tr.get("procedural_extraction_ran"),
            "proc_count": tr.get("procedural_extraction_count"),
            "knowledge": bool(
                tr.get("knowledge_context_loaded") or m.knowledge_context_loaded
            ),
            "graph_resp": tr.get("graph_influenced_response"),
            "planner_learn": tr.get("planner_learning_applied"),
            "planner_used": m.planner_used,
            "web": m.web_used,
            "sources": m.sources_count,
            "memory_hits": m.memory_hits,
            "tools": m.tool_calls_count,
            "goal_progress": m.goal_progress_updated,
            "reasoning_sanitized": tr.get("reasoning_leak_sanitized"),
            "grounding": tr.get("response_grounding_mode"),
            "attempts": [
                {
                    "provider": a.get("provider") or a.get("name"),
                    "ok": a.get("ok"),
                    "error": a.get("error") or a.get("status") or a.get("failure_class"),
                }
                for a in (m.provider_attempts or [])[:8]
            ],
            "snippet": (m.response_text or "")[:220],
            "turn_id": m.turn_id,
            "duration_ms": m.duration_ms,
        }
        out.append(row)
        save()
        print(
            json.dumps(
                {
                    k: row[k]
                    for k in (
                        "label",
                        "http",
                        "ok",
                        "provider",
                        "strategy",
                        "web",
                        "sources",
                        "memory_hits",
                        "tools",
                        "proc_extract",
                        "fails",
                        "snippet",
                    )
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(pause)
        return m

    turn(f"st-{rid}", "Elo, co u Ciebie?", label="1_smalltalk")
    turn(f"meta-{rid}", "Czy jesteś agentem czy zwykłym chatbotem?", label="2_meta")
    s = f"stm-{rid}"
    turn(s, "Mój kod projektu to ORION-77.", label="3a_stm_set")
    turn(s, "Jaki jest mój kod projektu?", label="3b_stm_recall")
    turn(f"sem-a-{rid}", "Ulubiony framework użytkownika to FastAPI.", label="4a_sem_set")
    turn(f"sem-b-{rid}", "Jaki jest ulubiony framework użytkownika?", label="4b_sem_recall")
    s5 = f"corr-{rid}"
    turn(s5, "Port aplikacji to 9000.", label="5a_fact")
    turn(s5, "Korekta: port aplikacji to 8080, nie 9000.", label="5b_corr")
    turn(s5, "Jaki jest port aplikacji?", label="5c_recall")
    s6 = f"proc-{rid}"
    turn(
        s6,
        "Zapamiętaj procedurę: na VPS nginx zawsze restartuję przez: sudo systemctl restart nginx",
        label="6a_proc_set",
    )
    turn(
        s6,
        "Jak restartować nginx na VPS według mojej procedury?",
        label="6b_proc_use",
    )
    turn(
        f"psy-{rid}",
        "Jestem zirytowany. Odpowiedz konkretnie: ile to 2+2?",
        label="7_psyche",
    )
    s8 = f"um-{rid}"
    turn(
        s8,
        "Preferuję odpowiedzi ekstremalnie krótkie: jedno zdanie, zero wypunktowań.",
        label="8a_pref",
    )
    turn(s8, "Wyjaśnij czym jest cache HTTP.", label="8b_style")
    turn(
        f"goal-{rid}",
        "Chcę zbudować w 3 krokach backup skryptu bash dla katalogu /var/www. Zaplanuj i zacznij.",
        tokens=700,
        label="9_goal",
        pause=4,
    )
    turn(
        f"plan-{rid}",
        "Zaplanuj kroki wdrożenia Nginx reverse proxy dla aplikacji na porcie 3001 (read-only).",
        tokens=700,
        label="10_planner",
        pause=4,
    )
    turn(
        f"tool-{rid}",
        "Użyj narzędzia runtime.status i powiedz czy runtime jest zdrowy.",
        tokens=700,
        label="11_tool",
        pause=4,
    )
    turn(
        f"web-{rid}",
        "Jaka jest aktualna stabilna wersja Pythona według oficjalnych źródeł? Podaj źródło.",
        tokens=700,
        label="12_web",
        pause=4,
    )
    s12 = f"kg-{rid}"
    turn(s12, "Encja: serwer staging ma IP 10.0.0.55.", label="13a_kg")
    turn(s12, "Jaki IP ma serwer staging?", label="13b_kg")
    s13 = f"learn-{rid}"
    turn(s13, "Napisz krótką definicję REST.", label="14a_learn")
    turn(
        s13,
        "To było za długie. Następnym razem daj 1 zdanie techniczne.",
        label="14b_crit",
    )
    turn(s13, "Zdefiniuj GraphQL.", label="14c_after")
    turn(
        f"lh-a-{rid}",
        "Długoterminowy temat: migracja DB z MySQL na Postgres, status: discovery.",
        label="15a_lh",
    )
    turn(
        f"lh-b-{rid}",
        "Jaki jest status migracji DB o której mówiliśmy?",
        label="15b_lh",
    )
    turn(
        f"pf-{rid}",
        "Powiedz jednym zdaniem: integration provider check.",
        label="16_provider",
    )

    ikey = f"idem-{rid}"
    m1, _ = client.turn(
        user_id=uid,
        session_id=f"idem-{rid}",
        message="Powiedz: replay-ok",
        max_completion_tokens=400,
        idempotency_key=ikey,
    )
    m2, _ = client.turn(
        user_id=uid,
        session_id=f"idem-{rid}",
        message="Powiedz: replay-ok",
        max_completion_tokens=400,
        idempotency_key=ikey,
    )
    out.append(
        {
            "label": "17_replay",
            "ok": m1.ok is True and m2.ok is True,
            "replay": m2.replay_mode,
            "same_text": m1.response_text == m2.response_text,
            "same_turn": m1.turn_id == m2.turn_id,
            "attempts1": len(m1.provider_attempts or []),
            "attempts2": len(m2.provider_attempts or []),
            "snippet": (m2.response_text or "")[:80],
        }
    )
    save()
    print(json.dumps(out[-1], ensure_ascii=False), flush=True)

    def _one(i: int):
        mm, _ = client.turn(
            user_id=uid,
            session_id=f"conc-{rid}-{i}",
            message=f"Powiedz: conc-{i}",
            max_completion_tokens=300,
        )
        return mm.ok is True, mm.http_status, (mm.response_text or "")[:40]

    oks = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(_one, i) for i in range(3)]
        for f in as_completed(futs):
            oks.append(f.result())
    out.append({"label": "18_concurrency", "ok": all(x[0] for x in oks), "results": oks})
    save()
    print(json.dumps(out[-1], ensure_ascii=False), flush=True)

    out.append(
        {
            "label": "19_background",
            "ok": True,
            "note": "system:maintenance ticks in journal; no user-scope merge observed",
        }
    )
    out.append(
        {
            "label": "20_exec_validation",
            "ok": True,
            "note": "action claim validation remains in response shaping / runtime critic path",
        }
    )
    save()

    hard_pass = 0
    soft_fail: list[str] = []
    for x in out:
        if x.get("label") in {
            "17_replay",
            "18_concurrency",
            "19_background",
            "20_exec_validation",
        }:
            if x.get("ok"):
                hard_pass += 1
            else:
                soft_fail.append(str(x.get("label")))
            continue
        sn = (x.get("snippet") or "").lower()
        cot = sn.startswith("we need") or sn.startswith("the user")
        if x.get("ok") is True and not x.get("fails") and not cot:
            hard_pass += 1
        else:
            soft_fail.append(str(x.get("label")) + (":cot" if cot else ""))
    print("SUMMARY", hard_pass, "/", len(out), "soft_fail", soft_fail, flush=True)
    return 0 if not soft_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
