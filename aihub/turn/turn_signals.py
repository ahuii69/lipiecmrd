#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-turn quality signals for dynamic budget + adaptive runtime."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TurnSignals:
    """Continuous signals estimated before prompt assembly / stage planning."""

    confidence: float = 0.5
    uncertainty: float = 0.5
    novelty: float = 0.4
    tool_probability: float = 0.2
    memory_usefulness: float = 0.4
    expected_token_roi: float = 0.5
    latency_budget_ms: float = 4000.0
    complexity: float = 0.4
    reason_codes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["reason_codes"] = list(self.reason_codes)
        return d


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def compute_turn_signals(
    *,
    user_text: str,
    selected_strategy: str | None = None,
    web_decision: str | None = None,
    strategy_confidence: float | None = None,
    intent_confidence: float | None = None,
    ambiguity: float | None = None,
    memory_hits: int = 0,
    memory_pack_items: int = 0,
    history_len: int = 0,
    budget_profile: str | None = None,
    reason_codes: list[str] | None = None,
) -> TurnSignals:
    """Estimate turn signals from decision-time observables (no extra LLM)."""
    text = (user_text or "").strip()
    low = text.lower()
    words = [w for w in low.split() if w]
    n_words = len(words)
    strat = (selected_strategy or "instant").strip().lower()
    web = (web_decision or "off").strip().lower()
    profile = (budget_profile or "").strip().lower()
    codes: list[str] = ["TURN_SIGNALS_COMPUTED"]

    conf = _clamp(
        0.55 * float(strategy_confidence if strategy_confidence is not None else 0.55)
        + 0.45 * float(intent_confidence if intent_confidence is not None else 0.55)
    )
    amb = _clamp(float(ambiguity if ambiguity is not None else 0.0))
    if "?" in text and n_words <= 4:
        amb = max(amb, 0.25)
    if any(k in low for k in ("nie wiem", "może", "albo", "czy raczej", "nie jestem pewien")):
        amb = max(amb, 0.55)
        codes.append("SIG_USER_UNCERTAINTY")
    uncertainty = _clamp(0.55 * amb + 0.45 * (1.0 - conf))

    # Novelty: long / rare markers / no memory hits → higher.
    novelty = 0.25
    if n_words >= 25:
        novelty += 0.25
    if n_words >= 60:
        novelty += 0.15
    if memory_hits <= 0 and memory_pack_items <= 0:
        novelty += 0.2
        codes.append("SIG_NO_MEMORY_HITS")
    if any(k in low for k in ("nowy", "pierwszy raz", "from scratch", "od zera", "profile26-")):
        novelty += 0.15
    novelty = _clamp(novelty)

    tool_p = 0.05
    if strat in ("research", "agentic"):
        tool_p = 0.55 if strat == "research" else 0.7
    if web in ("required", "optional"):
        tool_p = max(tool_p, 0.65 if web == "required" else 0.35)
    if any(k in low for k in ("sprawdź", "sprawdz", "wyszukaj", "uruchom", "wykonaj", "zrób", "zrob")):
        tool_p = max(tool_p, 0.6)
        codes.append("SIG_TOOLISH_LEXICON")
    if profile in ("meta_light", "casual_light"):
        tool_p = min(tool_p, 0.05)
    tool_p = _clamp(tool_p)

    mem_u = 0.2
    if memory_pack_items > 0:
        mem_u = _clamp(0.35 + 0.12 * min(5, memory_pack_items))
        codes.append("SIG_MEMORY_PACK_PRESENT")
    if memory_hits > 0:
        mem_u = max(mem_u, _clamp(0.3 + 0.08 * min(6, memory_hits)))
    if any(k in low for k in ("pamiętasz", "pamietasz", "jak nazywa", "co mówiłem", "co mowilem", "zapamiętał")):
        mem_u = max(mem_u, 0.75)
        codes.append("SIG_RECALL_INTENT")
    if looks_correctionish(low) or looks_rememberish(low):
        mem_u = max(mem_u, 0.7)

    # Expected token ROI: value of extra context vs cost.
    roi = 0.45
    if profile in ("meta_light", "casual_light"):
        roi = 0.15
        codes.append("SIG_LOW_ROI_LIGHT")
    elif strat == "agentic" or any(k in low for k in ("zaplanuj", "migracj", "etap")):
        roi = 0.85
        codes.append("SIG_HIGH_ROI_AGENTIC")
    elif mem_u >= 0.6 or looks_correctionish(low):
        roi = 0.7
    elif novelty < 0.3 and conf >= 0.7 and n_words <= 12:
        roi = 0.25
        codes.append("SIG_LOW_ROI_SIMPLE")
    roi = _clamp(roi)

    complexity = _clamp(
        0.25 * min(1.0, n_words / 40.0)
        + 0.25 * novelty
        + 0.2 * tool_p
        + 0.15 * uncertainty
        + 0.15 * (1.0 if strat in ("research", "agentic") else 0.3)
    )

    # Latency budget shrinks for simple/high-confidence turns.
    if profile in ("meta_light", "casual_light") or (conf >= 0.75 and complexity <= 0.25):
        latency_ms = 1200.0
        codes.append("SIG_LATENCY_TIGHT")
    elif complexity >= 0.7 or strat == "agentic":
        latency_ms = 9000.0
        codes.append("SIG_LATENCY_WIDE")
    elif strat == "research" or web == "required":
        latency_ms = 7000.0
    else:
        latency_ms = 4000.0

    if history_len >= 20 and complexity < 0.5:
        # Long thread but simple ask — prefer lean prompt.
        roi = min(roi, 0.55)
        codes.append("SIG_LONG_THREAD_LEAN")

    if reason_codes:
        for c in reason_codes[:8]:
            if c and c not in codes:
                codes.append(str(c)[:64])

    return TurnSignals(
        confidence=conf,
        uncertainty=uncertainty,
        novelty=novelty,
        tool_probability=tool_p,
        memory_usefulness=mem_u,
        expected_token_roi=roi,
        latency_budget_ms=latency_ms,
        complexity=complexity,
        reason_codes=codes,
    )


def looks_correctionish(low: str) -> bool:
    return any(
        m in low
        for m in ("poprawka", "korekta", "nie, jednak", "nie jednak", "jednak lubi", "odwołuję", "odwoluje")
    )


def looks_rememberish(low: str) -> bool:
    return any(m in low for m in ("zapamiętaj", "zapamietaj", "zapisz, że", "zapisz ze", "zapisz że"))
