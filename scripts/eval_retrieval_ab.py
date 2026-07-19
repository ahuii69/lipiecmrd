#!/usr/bin/env python3
"""Retrieval A/B: baseline vs evidence-driven scoring (precision/recall/win-rate)."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aihub.memory_context_pack import (
    MemoryContextPackItem,
    baseline_score_components,
    evidence_score_components,
    select_with_diversity,
)

NOW = time.time()

# Labeled cases: gold_ids are the relevant memories that should rank in top-k.
CASES: list[dict] = [
    {
        "id": "marker_fresh_k1",
        "query": "Co wiemy o Profile26-abcd1234 odkurzacz?",
        "correction_hints": "korekta: odkurzacz to narzędzie",
        "k": 1,
        "gold": ["gold_fresh"],
        "items": [
            MemoryContextPackItem(
                id="gold_fresh",
                source="memory_v2",
                memory_type="fact",
                title="Profile26-abcd1234",
                content="korekta: Profile26-abcd1234 odkurzacz to narzędzie do sprzątania",
                score=0.42,
                confidence=0.92,
                salience=0.7,
                metadata={"updated_ts": NOW - 3600},
            ),
            MemoryContextPackItem(
                id="stale_wrong",
                source="graph_stm",
                memory_type="fact",
                title="odkurzacz",
                content="odkurzacz to produkt marketingowy z reklamy telewizyjnej",
                score=0.95,
                confidence=0.25,
                salience=0.35,
                metadata={"updated_ts": NOW - 180 * 86400},
            ),
        ],
    },
    {
        "id": "diversity_prefs",
        "query": "Jakie mam preferencje napojów?",
        "correction_hints": "",
        "k": 2,
        "gold": ["tea", "coffee1"],
        "items": [
            MemoryContextPackItem(
                id="coffee1",
                source="memory_v2",
                memory_type="preference",
                title="pref",
                content="User lubi kawę rano",
                score=0.8,
                confidence=0.8,
                salience=0.7,
                metadata={"updated_ts": NOW - 86400},
            ),
            MemoryContextPackItem(
                id="coffee2",
                source="memory_v2",
                memory_type="preference",
                title="pref",
                content="Użytkownik lubi kawę z rana mocno",
                score=0.79,
                confidence=0.8,
                salience=0.7,
                metadata={"updated_ts": NOW - 90000},
            ),
            MemoryContextPackItem(
                id="tea",
                source="memory_v2",
                memory_type="preference",
                title="pref",
                content="Preferuje herbatę wieczorem",
                score=0.75,
                confidence=0.85,
                salience=0.7,
                metadata={"updated_ts": NOW - 7200},
            ),
        ],
    },
    {
        "id": "procedure_over_stm_noise",
        "query": "Jak debugować błąd 502?",
        "correction_hints": "",
        "k": 1,
        "gold": ["proc_502"],
        "items": [
            MemoryContextPackItem(
                id="proc_502",
                source="procedure",
                memory_type="procedural",
                title="debug 502",
                content="najpierw logi nginx, potem upstream, potem restart",
                score=0.48,
                confidence=0.92,
                salience=0.8,
                metadata={"updated_ts": NOW - 86400 * 3, "evidence_count": 12},
            ),
            MemoryContextPackItem(
                id="stm_noise",
                source="graph_stm",
                memory_type="fact",
                title="502",
                content="debugować błąd 502 wspomniane luźno na czacie",
                score=0.91,
                confidence=0.15,
                salience=0.15,
                metadata={"updated_ts": NOW - 600},
            ),
        ],
    },
    {
        "id": "contradiction_fresh_correction",
        "query": "Czy lubię kawę?",
        "correction_hints": "korekta: nie lubię kawy",
        "k": 1,
        "gold": ["corr_coffee"],
        "items": [
            MemoryContextPackItem(
                id="old_like",
                source="memory_v2",
                memory_type="preference",
                title="kawa",
                content="User lubi kawę bardzo",
                score=0.86,
                confidence=0.55,
                salience=0.6,
                metadata={"updated_ts": NOW - 90 * 86400},
            ),
            MemoryContextPackItem(
                id="corr_coffee",
                source="memory_v2",
                memory_type="fact",
                title="kawa",
                content="korekta: nie lubię kawy, wolę herbatę",
                score=0.5,
                confidence=0.95,
                salience=0.85,
                metadata={"updated_ts": NOW - 1800},
            ),
        ],
    },
    {
        "id": "semantic_over_noisy_stm",
        "query": "Jaki jest mój adres e-mail do faktur?",
        "correction_hints": "",
        "k": 1,
        "gold": ["email_fact"],
        "items": [
            MemoryContextPackItem(
                id="email_fact",
                source="memory_v2",
                memory_type="fact",
                title="email",
                content="Adres e-mail do faktur: billing@example.com",
                score=0.4,
                confidence=0.95,
                salience=0.8,
                metadata={"updated_ts": NOW - 86400 * 2},
            ),
            MemoryContextPackItem(
                id="stm_chat",
                source="graph_stm",
                memory_type="fact",
                title="email",
                content="mój adres e-mail do faktur był wspomniany w żartach",
                score=0.85,
                confidence=0.2,
                salience=0.2,
                metadata={"updated_ts": NOW - 120},
            ),
        ],
    },
    {
        "id": "lesson_reliability",
        "query": "Czego unikać przy deployu w piątek?",
        "correction_hints": "",
        "k": 1,
        "gold": ["lesson_fri"],
        "items": [
            MemoryContextPackItem(
                id="lesson_fri",
                source="memory_v2",
                memory_type="lesson",
                title="deploy",
                content="Unikać deployu w piątek po 15 bez rollback planu",
                score=0.45,
                confidence=0.9,
                salience=0.75,
                reason_codes=["reinforced", "high_confidence"],
                metadata={"updated_ts": NOW - 86400 * 5},
            ),
            MemoryContextPackItem(
                id="epi_noise",
                source="graph_episodic",
                memory_type="autobiographical",
                title="piątek",
                content="W piątek rozmawialiśmy o deployu przy kawie",
                score=0.8,
                confidence=0.3,
                salience=0.3,
                metadata={"updated_ts": NOW - 86400},
            ),
        ],
    },
]


def _topk_baseline(case: dict) -> list[str]:
    scored = [
        (it, baseline_score_components(it, query=case["query"], correction_hints=case.get("correction_hints") or ""))
        for it in case["items"]
    ]
    scored.sort(key=lambda p: p[1]["composite"], reverse=True)
    return [it.id for it, _ in scored[: case["k"]]]


def _topk_evidence(case: dict) -> list[str]:
    scored = [
        (
            it,
            evidence_score_components(
                it,
                query=case["query"],
                correction_hints=case.get("correction_hints") or "",
                now=NOW,
            ),
        )
        for it in case["items"]
    ]
    scored.sort(key=lambda p: p[1]["composite"], reverse=True)
    diverse = select_with_diversity(scored, max_items=case["k"])
    return [it.id for it, _ in diverse]


def _pr(pred: list[str], gold: list[str]) -> tuple[float, float]:
    g = set(gold)
    if not pred:
        return 0.0, 0.0
    hit = len(set(pred) & g)
    precision = hit / len(pred)
    recall = hit / max(1, len(g))
    return precision, recall


def evaluate_retrieval_ab(cases: list[dict] | None = None) -> dict:
    rows = []
    wins = ties = losses = 0
    sum_p_b = sum_r_b = sum_p_e = sum_r_e = 0.0
    for case in cases or CASES:
        b = _topk_baseline(case)
        e = _topk_evidence(case)
        pb, rb = _pr(b, case["gold"])
        pe, re = _pr(e, case["gold"])
        sum_p_b += pb
        sum_r_b += rb
        sum_p_e += pe
        sum_r_e += re
        # Win = higher F1
        f1b = 0.0 if (pb + rb) == 0 else 2 * pb * rb / (pb + rb)
        f1e = 0.0 if (pe + re) == 0 else 2 * pe * re / (pe + re)
        if f1e > f1b + 1e-9:
            wins += 1
            verdict = "evidence_win"
        elif f1b > f1e + 1e-9:
            losses += 1
            verdict = "baseline_win"
        else:
            ties += 1
            verdict = "tie"
        rows.append(
            {
                "id": case["id"],
                "baseline_topk": b,
                "evidence_topk": e,
                "gold": case["gold"],
                "baseline_p": round(pb, 3),
                "baseline_r": round(rb, 3),
                "evidence_p": round(pe, 3),
                "evidence_r": round(re, 3),
                "verdict": verdict,
            }
        )
    n = max(1, len(rows))
    summary = {
        "n": len(rows),
        "baseline_precision": round(sum_p_b / n, 3),
        "baseline_recall": round(sum_r_b / n, 3),
        "evidence_precision": round(sum_p_e / n, 3),
        "evidence_recall": round(sum_r_e / n, 3),
        "win_rate": round(wins / n, 3),
        "tie_rate": round(ties / n, 3),
        "loss_rate": round(losses / n, 3),
        "rows": rows,
        "gates": {
            "evidence_precision_ge_baseline": (sum_p_e / n) >= (sum_p_b / n) - 1e-9,
            "evidence_recall_ge_baseline": (sum_r_e / n) >= (sum_r_b / n) - 1e-9,
            "win_rate_ge_0.5": (wins / n) >= 0.5,
            "no_losses": losses == 0,
            "mean_f1_improvement": True,  # filled below
        },
    }
    f1b = 0.0 if (summary["baseline_precision"] + summary["baseline_recall"]) == 0 else (
        2 * summary["baseline_precision"] * summary["baseline_recall"]
        / (summary["baseline_precision"] + summary["baseline_recall"])
    )
    f1e = 0.0 if (summary["evidence_precision"] + summary["evidence_recall"]) == 0 else (
        2 * summary["evidence_precision"] * summary["evidence_recall"]
        / (summary["evidence_precision"] + summary["evidence_recall"])
    )
    summary["baseline_f1"] = round(f1b, 3)
    summary["evidence_f1"] = round(f1e, 3)
    summary["gates"]["mean_f1_improvement"] = f1e >= f1b - 1e-9 and f1e > 0
    summary["pass"] = all(summary["gates"].values())
    return summary


def main() -> int:
    out = evaluate_retrieval_ab()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
