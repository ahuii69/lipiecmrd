#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical memory context pack for prompts, trace and frontend inspection.

This module turns the canonical read outcome (graph + Memory V2) into one
budgeted, deterministic object. It intentionally does not perform another
retrieval pass: selection stays tied to ``MemoryCanonicalCore.read_memory`` so
runtime prompt injection, trace and post-turn learning can reference the same
memory ids.
"""

from __future__ import annotations

import re
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from aihub.memory_read_contracts import MemoryReadOutcome
from aihub.memory_v2_models import MemoryV2Item, MemoryV2Procedure

PackSource = Literal["graph_stm", "graph_episodic", "graph_semantic", "memory_v2", "procedure", "contradiction"]


class MemoryContextPackItem(BaseModel):
    id: str
    source: PackSource
    memory_type: str = ""
    title: str = ""
    content: str
    score: float = 0.0
    confidence: float = 0.0
    salience: float = 0.0
    reason_codes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryContextPack(BaseModel):
    user_id: str
    query: str
    facts: list[MemoryContextPackItem] = Field(default_factory=list)
    preferences: list[MemoryContextPackItem] = Field(default_factory=list)
    procedures: list[MemoryContextPackItem] = Field(default_factory=list)
    episodes: list[MemoryContextPackItem] = Field(default_factory=list)
    contradictions: list[MemoryContextPackItem] = Field(default_factory=list)
    other: list[MemoryContextPackItem] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)
    excluded_ids: list[str] = Field(default_factory=list)
    token_budget_chars: int = 8000
    used_chars: int = 0
    source_distribution: dict[str, int] = Field(default_factory=dict)
    retrieval_trace: dict[str, Any] = Field(default_factory=dict)
    created_ts: float = Field(default_factory=time.time)

    def all_items(self) -> list[MemoryContextPackItem]:
        return [
            *self.preferences,
            *self.facts,
            *self.procedures,
            *self.episodes,
            *self.contradictions,
            *self.other,
        ]

    def to_prompt_text(self, *, max_chars: int | None = None) -> str:
        """Render a compact prompt block with the exact selected pack items."""
        budget = max(1000, int(max_chars or self.token_budget_chars))
        lines: list[str] = ["PAMIĘĆ KONTEKSTOWA (wybrane, zweryfikowane wpisy):"]

        def add_group(label: str, items: list[MemoryContextPackItem]) -> None:
            if not items:
                return
            lines.append(f"\n{label}:")
            for item in items:
                text = item.content.replace("\n", " ").strip()
                if item.title and item.title.lower() not in text.lower():
                    text = f"{item.title}: {text}"
                lines.append(f"- [{item.id}] {text}")

        add_group("Preferencje", self.preferences)
        add_group("Fakty", self.facts)
        add_group("Procedury", self.procedures)
        add_group("Epizody", self.episodes)
        add_group("Sprzeczności/uwagi", self.contradictions)
        add_group("Inne", self.other)

        rendered = "\n".join(lines).strip()
        if len(rendered) <= budget:
            return rendered
        return rendered[: max(0, budget - 32)].rstrip() + "\n…[memory context truncated]"

    def to_trace_summary(self) -> dict[str, Any]:
        """Small non-secret summary for chat traces, logs and cockpit diagnostics."""
        return {
            "selected_ids": list(self.selected_ids),
            "excluded_count": len(self.excluded_ids),
            "used_chars": int(self.used_chars),
            "token_budget_chars": int(self.token_budget_chars),
            "source_distribution": dict(self.source_distribution),
            "item_count": len(self.all_items()),
            "retrieval_trace": dict(self.retrieval_trace),
        }


def _clip(text: str, limit: int = 900) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


_JUNK_MEMORY_MARKERS = (
    "memory-guided response",
    "context from ",
    "memories helped",
    "brak danych (web)",
    "brak danych",
    "działa. gotowy",
    "dziala. gotowy",
    "fallback",
    "helpdesk",
    "smoke ok",
    "test ping",
    "made progress",
    "planned_reasoning",
    "reasoning steps",
    "[kontekst pamięci]",
    "[kontekst pamieci]",
)

_EPISODE_ECHO = re.compile(r"(?is)\bU\s*:.+\|\|.*A\s*:")

_SPORT_NOISE = (
    "wynik meczu",
    "liga mistrzów",
    "liga mistrzow",
    "mecz ",
    " gol ",
    "gole ",
    "tabela ligowa",
)


def is_junk_memory_content(text: str, *, query: str = "") -> bool:
    """Reject meta-memory, fallback leftovers, and off-topic sport noise."""
    raw = (text or "").strip()
    if not raw or len(raw) < 3:
        return True
    low = raw.lower()
    if any(m in low for m in _JUNK_MEMORY_MARKERS):
        return True
    if _EPISODE_ECHO.search(raw) or "||" in raw or raw.strip().startswith("U:"):
        return True
    # Question-shaped "facts" (ingested recall questions) are not usable memory.
    if raw.endswith("?") and len(raw.split()) <= 12:
        return True
    if re.match(r"(?iu)^(czego|czym|jak|jaki|jaka|jakie|czy)\b", raw) and "lubi" in low:
        return True
    # Near-echo of the current query is not evidence.
    qn = re.sub(r"\s+", " ", (query or "").strip().lower())
    rn = re.sub(r"\s+", " ", low)
    if qn and rn and (qn == rn or qn.rstrip("?") == rn.rstrip("?")):
        return True
    # Single-token shout / nonverbal noise
    if len(raw.split()) <= 2 and low in {"elo", "gówno", "gowno", "ok", "działa", "dziala", "siema", "hej"}:
        return True
    q = (query or "").lower()
    # For identity/meta asks, drop sport chatter entirely
    try:
        from aihub.strategy_selector import is_assistant_meta_ask

        meta = is_assistant_meta_ask(query)
    except Exception:
        meta = False
    if meta and any(s in low for s in _SPORT_NOISE):
        return True
    if meta and ("wynik" in low or "mecz" in low) and "system" not in low:
        return True
    return False


def _pref_stem(word: str) -> str:
    return re.sub(r"[^\w]", "", (word or "").lower())[:5]


def _extract_pl_preferences(text: str) -> tuple[set[str], set[str]]:
    likes: set[str] = set()
    dislikes: set[str] = set()
    for m in re.finditer(r"(?iu)\blubi\s+(\S+)", text or ""):
        likes.add(_pref_stem(m.group(1)))
    for m in re.finditer(r"(?iu)\bnie\s+lubi\s+(\S+)", text or ""):
        dislikes.add(_pref_stem(m.group(1)))
    return likes, dislikes


def memory_contradicts_correction_hints(content: str, correction_hints: str) -> bool:
    """Drop stale memory when durable user correction flips like/dislike on same topic."""
    if not (content or "").strip() or not (correction_hints or "").strip():
        return False
    cont_l = " ".join((content or "").lower().split())
    # The correction statement itself must stay injectable.
    if any(k in cont_l for k in ("poprawka", "korekta", "sprostowanie", "nie, jednak", "nie, ")):
        return False
    # Prefer the newest preference-bearing hint lines (tail), not the whole history dump.
    hint_lines = [
        ln for ln in (correction_hints or "").splitlines() if ln.strip() and ("lubi" in ln.lower())
    ]
    recent_hints = "\n".join(hint_lines[-3:]) if hint_lines else correction_hints
    h_likes, h_dislikes = _extract_pl_preferences(recent_hints)
    c_likes, c_dislikes = _extract_pl_preferences(content)
    if h_likes or h_dislikes:
        for stem in h_likes:
            if stem and stem in c_dislikes:
                return True
        for stem in h_dislikes:
            if stem and stem in c_likes:
                return True
    # Numeric/fact supersession: "korekta … 8080, nie 9000" vs stale "… 9000".
    import re as _re

    hint_l = " ".join((recent_hints or "").lower().split())
    if any(k in hint_l for k in ("korekta", "poprawka", "sprostowanie")) or " nie " in f" {hint_l} ":
        negated = set(_re.findall(r"\bnie\s+(\d+(?:\.\d+)?)\b", hint_l))
        all_nums = _re.findall(r"\d+(?:\.\d+)?", hint_l)
        affirmed = {n for n in all_nums if n not in negated}
        cont_nums = set(_re.findall(r"\d+(?:\.\d+)?", cont_l))
        if negated and (cont_nums & negated) and not (cont_nums & affirmed):
            return True
    return False


def _normalize_memory_key(text: str) -> str:
    import hashlib
    import re

    norm = re.sub(r"\s+", " ", (text or "").strip().lower())
    norm = re.sub(r"[^\w\s]", "", norm, flags=re.UNICODE)
    return hashlib.sha1(norm.encode("utf-8", errors="replace")).hexdigest()


def _v2_reason_codes(item: MemoryV2Item) -> list[str]:
    codes: list[str] = []
    if item.is_pinned:
        codes.append("pinned")
    if item.retrieval_priority_score >= 0.7:
        codes.append("high_priority")
    if item.salience_score >= 0.7:
        codes.append("high_salience")
    if item.confidence_score >= 0.8:
        codes.append("high_confidence")
    if item.outcome_reinforcement_score > 0:
        codes.append("reinforced")
    if item.contradiction_state != "none":
        codes.append(f"contradiction:{item.contradiction_state}")
    if item.embedding_vector_ref:
        codes.append("indexed")
    return codes or ["ranked_retrieval"]


def _from_v2(item: MemoryV2Item) -> MemoryContextPackItem:
    content = item.summary or item.content
    return MemoryContextPackItem(
        id=item.id,
        source="memory_v2",
        memory_type=str(item.memory_type),
        title=item.title,
        content=_clip(content),
        score=float(item.retrieval_priority_score or item.salience_score or 0.0),
        confidence=float(item.confidence_score or 0.0),
        salience=float(item.salience_score or 0.0),
        reason_codes=_v2_reason_codes(item),
        metadata={
            "scope": item.scope,
            "source_kind": item.source_kind,
            "source_ref": item.source_ref,
            "created_ts": item.created_ts,
            "updated_ts": item.updated_ts,
            "stability_tier": item.stability_tier,
            "decay_bucket": item.decay_bucket,
            "embedding_vector_ref": item.embedding_vector_ref,
        },
    )


def _from_proc(proc: MemoryV2Procedure) -> MemoryContextPackItem:
    tools = ", ".join(proc.recommended_tools)
    avoid = "; ".join(proc.avoid_patterns)
    # User-declared procedures store the ordered steps in recommended_strategy.
    content = (proc.recommended_strategy or "").strip() or proc.name
    if tools:
        content += f" | tools: {tools}"
    if avoid and "user_declared" not in avoid:
        content += f" | avoid: {avoid}"
    return MemoryContextPackItem(
        id=proc.id,
        source="procedure",
        memory_type="procedural",
        title=proc.name,
        content=_clip(content),
        score=float(proc.confidence_score or proc.success_rate or 0.0),
        confidence=float(proc.confidence_score or 0.0),
        salience=float(proc.success_rate or 0.0),
        reason_codes=["procedure", "user_declared" if "user_declared" in (proc.avoid_patterns or []) else "experience_pattern"],
        metadata={
            "trigger_pattern": proc.trigger_pattern,
            "success_rate": proc.success_rate,
            "failure_rate": proc.failure_rate,
            "evidence_count": proc.evidence_count,
            "recommended_tools": proc.recommended_tools,
        },
    )


def _graph_items(graph: dict[str, Any] | None) -> list[MemoryContextPackItem]:
    if not graph:
        return []
    out: list[MemoryContextPackItem] = []
    specs: list[tuple[str, PackSource, str]] = [
        ("stm", "graph_stm", "stm"),
        ("episodic", "graph_episodic", "episode"),
        ("semantic", "graph_semantic", "fact"),
        ("dense_hits", "graph_semantic", "dense"),
        ("graph_hits", "graph_semantic", "graph"),
    ]
    for key, source, mtype in specs:
        for idx, raw in enumerate(graph.get(key) or []):
            if not isinstance(raw, dict):
                continue
            ident = str(raw.get("id") or raw.get("node_id") or f"{source}:{idx}")
            content = raw.get("content") or raw.get("summary") or raw.get("text") or raw.get("title") or ""
            if not str(content).strip():
                continue
            out.append(
                MemoryContextPackItem(
                    id=ident,
                    source=source,
                    memory_type=mtype,
                    title=str(raw.get("title") or raw.get("kind") or ""),
                    content=_clip(str(content)),
                    score=float(raw.get("score") or raw.get("rank_score") or raw.get("importance") or 0.0),
                    confidence=float(raw.get("confidence") or 0.0),
                    salience=float(raw.get("importance") or raw.get("salience") or 0.0),
                    reason_codes=[key],
                    metadata={k: v for k, v in raw.items() if k not in {"content", "summary", "text"}},
                )
            )
    return out


SOURCE_RELIABILITY: dict[str, float] = {
    "memory_v2": 0.88,
    "procedure": 0.92,
    "contradiction": 0.78,
    "graph_semantic": 0.72,
    "graph_episodic": 0.65,
    "graph_stm": 0.52,
}

# Half-life for recency decay (seconds) — ~14 days.
_RECENCY_HALF_LIFE_S = 14.0 * 86400.0


def baseline_score_components(
    item: MemoryContextPackItem,
    *,
    query: str,
    correction_hints: str = "",
) -> dict[str, float]:
    """Pre-evidence ranking formula (marker/correction/type only) for A/B eval."""
    type_boost = {
        "preference": 0.18,
        "fact": 0.12,
        "procedural": 0.16,
        "autobiographical": 0.07,
        "relationship": 0.08,
        "lesson": 0.11,
        "contradiction": 0.20,
    }.get(item.memory_type, 0.0)
    q_tokens = {
        t.lower()
        for t in re.findall(r"[A-Za-z0-9_-]{5,}", query or "")
    }
    c_low = f"{item.title} {item.content}".lower()
    overlap = sum(1 for t in q_tokens if t in c_low)
    overlap_boost = min(0.45, 0.12 * overlap)
    exact_marker = 0.0
    for t in q_tokens:
        if "-" in t and len(t) >= 10 and t in c_low:
            exact_marker = 0.55
            break
    correction_boost = 0.0
    if correction_hints and any(k in c_low for k in ("poprawka", "korekta", "nie, ", "jednak")):
        correction_boost = 0.35
    composite = (
        float(item.score or 0.0)
        + float(item.salience or 0.0) * 0.35
        + float(item.confidence or 0.0) * 0.20
        + type_boost
        + overlap_boost
        + exact_marker
        + correction_boost
    )
    return {
        "composite": composite,
        "exact_marker": exact_marker,
        "correction_boost": correction_boost,
        "overlap_boost": overlap_boost,
    }


def _item_timestamp(item: MemoryContextPackItem) -> float:
    md = item.metadata or {}
    for key in ("updated_ts", "created_ts", "ts", "timestamp"):
        try:
            v = float(md.get(key) or 0)
            if v > 0:
                return v
        except (TypeError, ValueError):
            continue
    return 0.0


def _token_set(text: str) -> set[str]:
    return {t.lower() for t in re.findall(r"[A-Za-z0-9À-ÿ_-]{3,}", text or "")}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def evidence_score_components(
    item: MemoryContextPackItem,
    *,
    query: str,
    correction_hints: str = "",
    now: float | None = None,
) -> dict[str, float]:
    """Enterprise evidence features for one memory candidate."""
    now_ts = float(now if now is not None else time.time())
    q_tokens = _token_set(query)
    c_text = f"{item.title} {item.content}"
    c_low = c_text.lower()
    c_tokens = _token_set(c_text)

    # Base semantic / retrieval score already on the item.
    base = float(item.score or 0.0)

    # Evidence confidence (explicit item confidence + reinforcement hints).
    evidence_conf = float(item.confidence or 0.0)
    if "reinforced" in (item.reason_codes or []):
        evidence_conf = min(1.0, evidence_conf + 0.1)
    if "high_confidence" in (item.reason_codes or []):
        evidence_conf = max(evidence_conf, 0.8)

    # Source reliability.
    source_rel = float(SOURCE_RELIABILITY.get(item.source, 0.55))

    # Recency decay + freshness (1 = fresh).
    ts = _item_timestamp(item)
    if ts <= 0:
        freshness = 0.45
        recency = 0.35
    else:
        age = max(0.0, now_ts - ts)
        # Exponential half-life decay.
        recency = 0.5 ** (age / _RECENCY_HALF_LIFE_S)
        freshness = recency
        # STM is fresh by nature even without ts.
        if item.source == "graph_stm":
            freshness = max(freshness, 0.7)

    # Exact marker / rare token overlap.
    overlap = sum(1 for t in q_tokens if len(t) >= 5 and t in c_low)
    overlap_boost = min(0.45, 0.12 * overlap)
    exact_marker = 0.0
    for t in q_tokens:
        if "-" in t and len(t) >= 10 and t in c_low:
            exact_marker = 0.55
            break

    correction_boost = 0.0
    if correction_hints and any(k in c_low for k in ("poprawka", "korekta", "nie, ", "jednak")):
        correction_boost = 0.35

    # Contradiction score: surface contradictions when relevant; penalize unresolved conflicts.
    contradiction = 0.0
    if item.memory_type == "contradiction" or item.source == "contradiction":
        contradiction = 0.25  # useful to show
    for rc in item.reason_codes or []:
        if str(rc).startswith("contradiction:"):
            state = str(rc).split(":", 1)[-1]
            if state in ("unresolved", "open", "active"):
                contradiction -= 0.2
            elif state in ("resolved", "superseded"):
                contradiction += 0.05

    type_boost = {
        "preference": 0.18,
        "fact": 0.12,
        "procedural": 0.16,
        "autobiographical": 0.07,
        "relationship": 0.08,
        "lesson": 0.11,
        "contradiction": 0.20,
    }.get(item.memory_type, 0.0)

    # Reliability-weighted base: raw retrieval score cannot dominate weak sources.
    weighted_base = base * (0.30 + 0.70 * source_rel) * (0.35 + 0.65 * max(evidence_conf, 0.12))
    low_conf_penalty = 0.0 if evidence_conf >= 0.45 else (0.45 - evidence_conf) * 1.05
    # Fresh STM noise: freshness helps, but not when confidence is tiny.
    freshness_term = freshness * (0.12 if evidence_conf >= 0.4 else 0.03)

    composite = (
        weighted_base
        + float(item.salience or 0.0) * 0.30
        + evidence_conf * 0.28
        + source_rel * 0.28
        + recency * 0.14
        + freshness_term
        + type_boost
        + overlap_boost
        + exact_marker
        + correction_boost
        + contradiction
        - low_conf_penalty
    )

    return {
        "composite": composite,
        "base": base,
        "weighted_base": weighted_base,
        "evidence_confidence": evidence_conf,
        "source_reliability": source_rel,
        "recency": recency,
        "freshness": freshness,
        "contradiction": contradiction,
        "overlap_boost": overlap_boost,
        "exact_marker": exact_marker,
        "correction_boost": correction_boost,
        "low_conf_penalty": low_conf_penalty,
        "jaccard_query": _jaccard(q_tokens, c_tokens),
    }


def select_with_diversity(
    ranked: list[tuple[MemoryContextPackItem, dict[str, float]]],
    *,
    max_items: int,
    lambda_diversity: float = 0.72,
) -> list[tuple[MemoryContextPackItem, dict[str, float]]]:
    """MMR-style selection: relevance vs redundancy."""
    if not ranked:
        return []
    remaining = list(ranked)
    selected: list[tuple[MemoryContextPackItem, dict[str, float]]] = []
    selected_tokens: list[set[str]] = []

    while remaining and len(selected) < max_items:
        best_i = 0
        best_val = float("-inf")
        for i, (item, feats) in enumerate(remaining):
            rel = float(feats.get("composite") or 0.0)
            red = 0.0
            toks = _token_set(f"{item.title} {item.content}")
            if selected_tokens:
                red = max(_jaccard(toks, st) for st in selected_tokens)
            mmr = lambda_diversity * rel - (1.0 - lambda_diversity) * red * 1.2
            # Prefer source diversity slightly.
            if selected and item.source in {s.source for s, _ in selected}:
                mmr -= 0.05
            if mmr > best_val:
                best_val = mmr
                best_i = i
        chosen = remaining.pop(best_i)
        selected.append(chosen)
        selected_tokens.append(_token_set(f"{chosen[0].title} {chosen[0].content}"))
    return selected


def build_memory_context_pack(
    outcome: MemoryReadOutcome,
    *,
    max_chars: int = 2400,
    max_items: int = 8,
    correction_hints: str = "",
) -> MemoryContextPack:
    """Build one deterministic budgeted context pack from canonical memory read outcome."""
    max_chars = max(600, min(4000, int(max_chars or 2400)))
    max_items = max(1, min(12, int(max_items or 8)))
    selected: list[MemoryContextPackItem] = []
    excluded: list[str] = []
    seen_ids: set[str] = set()
    seen_content: set[str] = set()
    used = 0
    per_source_caps = {
        "memory_v2": 4,
        "procedure": 2,
        "contradiction": 2,
        "graph_stm": 2,
        "graph_episodic": 2,
        "graph_semantic": 3,
    }
    per_source_counts: dict[str, int] = {}

    candidates: list[MemoryContextPackItem] = []
    candidates.extend(_from_v2(item) for item in outcome.v2.items)
    candidates.extend(_from_proc(proc) for proc in outcome.v2.related_procedures)
    for c in outcome.v2.contradictions:
        cid = str(c.get("id") or c.get("memory_id") or c.get("from_memory_id") or f"contradiction:{len(candidates)}")
        text = str(c.get("reason") or c.get("detail") or c)
        candidates.append(
            MemoryContextPackItem(
                id=cid,
                source="contradiction",
                memory_type="contradiction",
                title="contradiction",
                content=_clip(text),
                score=0.7,
                confidence=float(c.get("confidence") or 0.0) if isinstance(c, dict) else 0.0,
                salience=0.7,
                reason_codes=["contradiction"],
                metadata=dict(c) if isinstance(c, dict) else {},
            )
        )
    candidates.extend(_graph_items(outcome.graph_context))

    query = str(outcome.query or "")
    now = time.time()
    scored: list[tuple[MemoryContextPackItem, dict[str, float]]] = []
    for item in candidates:
        if item.id in seen_ids:
            excluded.append(item.id)
            continue
        if is_junk_memory_content(item.content, query=query) or (
            (item.title or "").strip()
            and is_junk_memory_content(item.title, query=query)
        ):
            excluded.append(item.id)
            continue
        if memory_contradicts_correction_hints(item.content, correction_hints) or memory_contradicts_correction_hints(
            item.title, correction_hints
        ):
            excluded.append(item.id)
            continue
        # Relevance gate: keep preferences always; others need minimal score
        if item.memory_type != "preference" and item.score < 0.12 and item.salience < 0.2:
            excluded.append(item.id)
            continue
        content_key = _normalize_memory_key(f"{item.title}|{item.content}")
        if content_key in seen_content:
            excluded.append(item.id)
            continue
        seen_content.add(content_key)
        feats = evidence_score_components(
            item, query=query, correction_hints=correction_hints, now=now
        )
        item.reason_codes = list(item.reason_codes or []) + [
            f"ev_conf={feats['evidence_confidence']:.2f}",
            f"src_rel={feats['source_reliability']:.2f}",
            f"recency={feats['recency']:.2f}",
            f"fresh={feats['freshness']:.2f}",
        ]
        if feats["exact_marker"] > 0:
            item.reason_codes.append("exact_marker")
        if feats["correction_boost"] > 0:
            item.reason_codes.append("correction_bearing")
        scored.append((item, feats))

    scored.sort(key=lambda pair: pair[1]["composite"], reverse=True)
    diverse = select_with_diversity(scored, max_items=max_items * 2)  # oversample then pack

    for item, feats in diverse:
        src_cap = per_source_caps.get(item.source, 3)
        if per_source_counts.get(item.source, 0) >= src_cap:
            excluded.append(item.id)
            continue
        size = len(item.content) + len(item.title) + 32
        if len(selected) >= max_items or used + size > max_chars:
            excluded.append(item.id)
            continue
        # Attach composite into metadata for traces.
        md = dict(item.metadata or {})
        md["evidence"] = {k: round(float(v), 4) for k, v in feats.items()}
        item.metadata = md
        item.score = float(feats["composite"])
        selected.append(item)
        seen_ids.add(item.id)
        per_source_counts[item.source] = per_source_counts.get(item.source, 0) + 1
        used += size

    pack = MemoryContextPack(
        user_id=outcome.user_id,
        query=outcome.query,
        token_budget_chars=max_chars,
        used_chars=used,
        selected_ids=[item.id for item in selected],
        excluded_ids=excluded,
        retrieval_trace={
            "graph_loaded": outcome.graph_context is not None,
            "v2_total_count": outcome.v2.total_count,
            "v2_selected_count": len(outcome.v2.items),
            "procedure_count": len(outcome.v2.related_procedures),
            "contradiction_count": len(outcome.v2.contradictions),
            "candidate_count": len(candidates),
            "junk_or_dup_excluded": len(excluded),
            "per_source_counts": dict(per_source_counts),
            "evidence_driven": True,
            "diversity_mmr": True,
            "scoring": [
                "recency_decay",
                "evidence_confidence",
                "source_reliability",
                "contradiction",
                "freshness",
                "marker",
                "correction",
            ],
        },
    )
    dist: dict[str, int] = {}
    for item in selected:
        dist[item.source] = dist.get(item.source, 0) + 1
        if item.source == "procedure" or item.memory_type == "procedural":
            pack.procedures.append(item)
        elif item.memory_type == "preference":
            pack.preferences.append(item)
        elif item.memory_type in {"fact", "lesson", "relationship"}:
            pack.facts.append(item)
        elif item.memory_type == "autobiographical" or item.source == "graph_episodic":
            pack.episodes.append(item)
        elif item.source == "contradiction" or item.memory_type == "contradiction":
            pack.contradictions.append(item)
        else:
            pack.other.append(item)
    pack.source_distribution = dist
    return pack
