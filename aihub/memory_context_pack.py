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
    h_likes, h_dislikes = _extract_pl_preferences(correction_hints)
    c_likes, c_dislikes = _extract_pl_preferences(content)
    if h_likes or h_dislikes:
        for stem in h_likes:
            if stem and stem in c_dislikes:
                return True
        for stem in h_dislikes:
            if stem and stem in c_likes:
                return True
        # Explicit negation flip on shared token (e.g. burz*).
        if h_likes and c_dislikes:
            for hs in h_likes:
                for cs in c_dislikes:
                    if hs[:4] and hs[:4] == cs[:4]:
                        return True
    # Numeric/fact supersession: "korekta … 8080, nie 9000" vs stale "… 9000".
    import re as _re

    hint_l = " ".join((correction_hints or "").lower().split())
    cont_l = " ".join((content or "").lower().split())
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
    content = proc.recommended_strategy
    if tools:
        content += f" | tools: {tools}"
    if avoid:
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
        reason_codes=["procedure", "experience_pattern"],
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

    def candidate_score(item: MemoryContextPackItem) -> tuple[float, float, float]:
        type_boost = {
            "preference": 0.18,
            "fact": 0.12,
            "procedural": 0.16,
            "autobiographical": 0.07,
            "relationship": 0.08,
            "lesson": 0.11,
            "contradiction": 0.20,
        }.get(item.memory_type, 0.0)
        return (item.score + item.salience * 0.35 + item.confidence * 0.20 + type_boost, item.salience, item.confidence)

    query = str(outcome.query or "")
    for item in sorted(candidates, key=candidate_score, reverse=True):
        if item.id in seen_ids:
            excluded.append(item.id)
            continue
        if is_junk_memory_content(item.content, query=query) or is_junk_memory_content(
            item.title, query=query
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
        src_cap = per_source_caps.get(item.source, 3)
        if per_source_counts.get(item.source, 0) >= src_cap:
            excluded.append(item.id)
            continue
        size = len(item.content) + len(item.title) + 32
        if len(selected) >= max_items or used + size > max_chars:
            excluded.append(item.id)
            continue
        selected.append(item)
        seen_ids.add(item.id)
        seen_content.add(content_key)
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
