#!/usr/bin/env python3
"""Validate turn response/trace consistency (26.07).

Exit non-zero when any TRACE00x rule fires.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data.get("trace"), dict):
        return data
    if isinstance(data.get("response"), dict) and isinstance(data["response"].get("trace"), dict):
        return data["response"]
    # bare trace.json
    if "budget_profile" in data or "selected_strategy" in data or "provider" in data:
        return {"ok": data.get("ok", True), "response_text": data.get("response_text", ""), "trace": data}
    return data if isinstance(data, dict) else {}


def _trace(doc: dict[str, Any]) -> dict[str, Any]:
    tr = doc.get("trace")
    return tr if isinstance(tr, dict) else {}


def validate_doc(doc: dict[str, Any], *, artifact: str) -> list[dict[str, Any]]:
    tr = _trace(doc)
    violations: list[dict[str, Any]] = []

    def fail(code: str, path: str, expected: str, actual: Any) -> None:
        violations.append(
            {
                "code": code,
                "field_path": path,
                "expected": expected,
                "actual": actual,
                "artifact": artifact,
            }
        )

    mem_lookup = tr.get("memory_lookup_happened")
    mem_hits = tr.get("memory_hits")
    if mem_hits is None:
        mem_hits = tr.get("memory_results_count")
    try:
        mem_hits_i = int(mem_hits or 0)
    except (TypeError, ValueError):
        mem_hits_i = 0
    if mem_lookup is False and mem_hits_i > 0:
        fail("TRACE001", "memory_lookup_happened/memory_hits", "hits==0 when lookup false", mem_hits_i)

    web_used = tr.get("web_used")
    if web_used is None:
        web_used = bool(tr.get("controlled_web_triggered") and tr.get("controlled_web_ok"))
    sources = tr.get("sources_count")
    if sources is None:
        sources = tr.get("controlled_web_source_count") or tr.get("web_source_count") or 0
    try:
        sources_i = int(sources or 0)
    except (TypeError, ValueError):
        sources_i = 0
    if web_used is False and sources_i > 0:
        fail("TRACE002", "web_used/sources_count", "sources==0 when web false", sources_i)

    planner = tr.get("planner_used")
    if planner is None:
        planner = tr.get("agentic_executed") or tr.get("planner_executed")
    if planner is False and tr.get("execution_graph_built") and tr.get("execution_graph_created_this_turn"):
        fail("TRACE003", "planner_used/execution_graph", "no new graph when planner false", True)

    tool_iters = int(tr.get("tool_iterations") or 0)
    tool_exec = int(tr.get("tool_calls_executed") or 0)
    web_prefetch_tools = int(tr.get("controlled_web_source_count") or 0) > 0 or bool(
        tr.get("controlled_web_triggered")
    )
    if tool_iters == 0 and tool_exec > 0 and not web_prefetch_tools:
        fail("TRACE004", "tool_iterations/tool_calls_executed", "executed==0 when iterations==0", tool_exec)

    if tr.get("budget_profile") == "meta_light" and int(tr.get("tool_schema_chars") or (tr.get("prompt_budget") or {}).get("tool_schema_chars") or 0) > 0:
        fail("TRACE005", "prompt_budget.tool_schema_chars", "0 schemas when meta_light", tr.get("prompt_budget"))

    if (tr.get("idempotency_hit") or tr.get("replay_mode") or doc.get("idempotency_hit")) and int(tr.get("provider_attempt_count") or 0) > 0:
        fail("TRACE006", "idempotency_hit/provider_attempt_count", "0 attempts on replay", tr.get("provider_attempt_count"))

    if tr.get("writeback_policy") == "minimal":
        heavy = {"memory_v2", "knowledge", "learning", "reflection", "procedural_extraction", "success_patterns"}
        executed = set(tr.get("writebacks_executed") or [])
        bad = executed & heavy
        # Override only when feedback user_model etc. — not lessons
        if bad:
            fail("TRACE007", "writebacks_executed", "no heavy writebacks under minimal", sorted(bad))

    if tr.get("selected_goal") not in (None, "", False) and int(tr.get("actionable_goal_count") or 0) == 0 and tr.get("actionable_goal_count") is not None:
        fail("TRACE008", "selected_goal/actionable_goal_count", "goal null when actionable_count=0", tr.get("selected_goal"))

    try:
        succ = int(tr.get("provider_success_count") or 0)
        att = int(tr.get("provider_attempt_count") or 0)
        gen = int(tr.get("provider_generation_count") or 0)
    except (TypeError, ValueError):
        succ = att = gen = 0
    if succ > att and att >= 0 and tr.get("provider_attempt_count") is not None:
        fail("TRACE009", "provider_success_count", "success <= attempts", {"success": succ, "attempts": att})
    if gen > succ and tr.get("provider_generation_count") is not None and tr.get("provider_success_count") is not None:
        fail("TRACE010", "provider_generation_count", "generation <= success", {"generation": gen, "success": succ})

    attempts = tr.get("provider_attempts") or []
    providers = []
    if isinstance(attempts, list):
        for a in attempts:
            if isinstance(a, dict):
                providers.append(str(a.get("provider") or a.get("name") or ""))
    distinct = {p for p in providers if p}
    if tr.get("provider_failover_happened") is False and len(distinct) > 1:
        fail("TRACE011", "provider_failover_happened", "true when multiple providers attempted", distinct)

    tvc = str(tr.get("turn_value_class") or "")
    if tvc == "trivial":
        msg = str(doc.get("response_text") or "")
        # Heuristic: if writebacks show memory creation flags
        if tr.get("memory_v2_writeback_succeeded") or int(tr.get("memory_v2_new_items_count") or 0) > 0:
            fail("TRACE012", "turn_value_class", "not trivial when memory persisted", tvc)

    if tr.get("memory_used") is True or (isinstance(tr.get("memory_used"), list) and tr.get("memory_used")):
        if not tr.get("memory_used") and not tr.get("memory_used_ids") and not (tr.get("memory_context_pack") or {}).get("selected_ids"):
            # memory_used bool true without ids
            if tr.get("memory_used") is True and mem_hits_i == 0:
                fail("TRACE013", "memory_used", "ids/pack/influence required", tr.get("memory_used"))

    if tr.get("affected_response") is True and not (
        tr.get("influence_reason") or tr.get("influence_reason_codes") or tr.get("memory_influenced_strategy")
    ):
        fail("TRACE014", "affected_response", "influence reason required", True)

    executed_stages = set(tr.get("stages_executed") or tr.get("stages_included") or [])
    skipped_stages = set(tr.get("stages_skipped") or [])
    both = executed_stages & skipped_stages
    if both:
        fail("TRACE015", "stages_executed∩stages_skipped", "disjoint sets", sorted(both))

    wb_ex = set(tr.get("writebacks_executed") or [])
    wb_sk = set(tr.get("writebacks_skipped") or [])
    both_wb = wb_ex & wb_sk
    if both_wb:
        fail("TRACE016", "writebacks_executed∩skipped", "disjoint sets", sorted(both_wb))

    if (doc.get("errors") == [] or tr.get("errors") == []) and tr.get("provider_ok") is False:
        fail("TRACE017", "errors/provider_ok", "errors non-empty when provider ok=false", tr.get("provider_ok"))

    if doc.get("ok") is True and not str(doc.get("response_text") or "").strip():
        fail("TRACE018", "response_text", "non-empty when ok=true", "")

    resp = str(doc.get("response_text") or "").lower()
    claim_markers = ("wykonałem migrację", "wykonano migrację", "migracja zakończona sukcesem", "successfully migrated")
    if any(m in resp for m in claim_markers) and tool_exec == 0 and not tr.get("execution_evidence"):
        fail("TRACE019", "response_text/action_claim", "no execution claim without tool evidence", True)

    final_prov = str(tr.get("provider") or doc.get("provider") or "").lower()
    if "openai api" in resp and final_prov and final_prov != "openai" and "gpt-oss" in str(tr.get("model") or ""):
        fail("TRACE020", "response_text/provider", "text must not claim OpenAI API for non-openai provider", final_prov)

    return violations


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", nargs="?", help="Single response/trace JSON")
    ap.add_argument("--directory", help="Scan directory recursively for response.json/trace.json")
    ap.add_argument("--json-out", help="Write violations JSON")
    args = ap.parse_args()

    files: list[Path] = []
    if args.path:
        files.append(Path(args.path))
    if args.directory:
        root = Path(args.directory)
        files.extend(sorted(root.rglob("response.json")))
        files.extend(sorted(root.rglob("*_response.json")))
        files.extend(sorted(root.rglob("trace.json")))
        files.extend(sorted(root.rglob("*_trace.json")))
    if not files:
        print("usage: validate_turn_trace.py <file> | --directory DIR", file=sys.stderr)
        return 2

    # De-dupe: prefer *_response.json over sibling *_trace.json
    preferred: list[Path] = []
    seen: set[str] = set()
    for f in files:
        if not f.exists():
            continue
        if f.name.endswith("_trace.json"):
            sibling = f.with_name(f.name.replace("_trace.json", "_response.json"))
            if sibling.exists():
                continue
        if f.name == "trace.json" and (f.parent / "response.json").exists():
            continue
        key = str(f.resolve())
        if key in seen:
            continue
        seen.add(key)
        preferred.append(f)

    all_v: list[dict[str, Any]] = []
    for f in preferred:
        try:
            doc = _load(f)
            # Merge sibling response text/ok into bare traces when present.
            if f.name.endswith("_trace.json"):
                sibling = f.with_name(f.name.replace("_trace.json", "_response.json"))
                if sibling.exists():
                    try:
                        resp = json.loads(sibling.read_text(encoding="utf-8"))
                        if isinstance(resp, dict):
                            if "ok" in resp:
                                doc["ok"] = resp.get("ok")
                            if resp.get("response_text"):
                                doc["response_text"] = resp.get("response_text")
                            if isinstance(resp.get("trace"), dict) and not isinstance(doc.get("trace"), dict):
                                doc["trace"] = resp["trace"]
                    except Exception:
                        pass
        except Exception as exc:  # noqa: BLE001
            all_v.append({"code": "LOAD", "artifact": str(f), "actual": str(exc), "expected": "readable JSON", "field_path": ""})
            continue
        all_v.extend(validate_doc(doc, artifact=str(f)))

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(all_v, indent=2, ensure_ascii=False), encoding="utf-8")

    if not all_v:
        print(f"OK: validated {len(preferred)} file(s), 0 violations")
        return 0
    print(f"FAIL: {len(all_v)} violation(s) across {len(preferred)} file(s)")
    for v in all_v:
        print(f"{v['code']} | {v.get('artifact')} | {v.get('field_path')} | expected={v.get('expected')} actual={v.get('actual')}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
