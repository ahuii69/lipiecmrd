#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ConsistencyEngine — Silnik spójności wiedzy.

Klasyfikuje nowo napływające fakty w kontekście istniejącej pamięci:
  - duplicate:   fakt jest semantycznym duplikatem istniejącego
  - revision:    fakt aktualizuje/zastępuje starszy (np. zmiana adresu)
  - conflict:    fakt jest sprzeczny z innym, wymagana interwencja
  - uncertain:   niedostateczne dane do klasyfikacji
  - new_fact:    całkowicie nowa informacja

Działa na bazie:
  - TF-IDF (vector_index) — szybka semantyczna similarity
  - difflib — string-level similarity
  - knowledge_graph — sprawdzenie istniejących relacji
"""

import difflib
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from aihub.db import (
    append_event,
    exec_one,
    fetch_all,
    json_dumps,
    json_loads,
    now_ts,
)
from aihub.vector_index import (
    build_df,
    prune_vocab,
    tfidf_vector,
    tokenize,
    topk_cosine,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ConsistencyVerdict:
    """Wynik sprawdzenia spójności nowego faktu."""

    classification: str  # duplicate | revision | conflict | uncertain | new_fact
    confidence: float  # 0.0-1.0
    matched_node_id: Optional[str] = None
    matched_content: Optional[str] = None
    similarity_score: float = 0.0
    reasoning: str = ""
    suggested_action: str = ""  # keep | merge | supersede | flag | store
    metadata: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class ConsistencyEngine:
    """
    Silnik spójności wiedzy.

    Porównuje nowy fakt z istniejącymi faktami usera za pomocą:
      1. TF-IDF cosine similarity (semantyka)
      2. String similarity (difflib SequenceMatcher)
      3. KnowledgeGraph edges ("contradicts", "refines")

    Wynik:
      - duplicate  ≥ 0.92 combined similarity
      - revision   ≥ 0.55 combined + revision keywords
      - conflict   sprzeczność tematowa (KG contradicts / antonimy logiczne)
      - uncertain  0.40-0.55 combined, niejasna relacja
      - new_fact   < 0.40 combined
    """

    # ---- progi ----
    DUPLICATE_THRESHOLD = 0.92
    REVISION_THRESHOLD = 0.55
    UNCERTAIN_LOW = 0.40
    CONFLICT_KEYWORD_BOOST = 0.15

    # Revision detection keywords  (fakt nowy vs. stary mówi o tym samym ale z inną wartością)
    _REVISION_SIGNALS = [
        "teraz",
        "już",
        "zmienił",
        "zmieniam",
        "nowy",
        "nowa",
        "aktualnie",
        "od dzisiaj",
        "od teraz",
        "przeprowadzi",
        "przepraszam",
        "korekta",
        "poprawka",
        "nie tak",
        "źle powiedziałem",
        "właściwie",
    ]

    # Conflict detection antonyms / negations
    _CONFLICT_NEGATION_PAIRS = [
        ("lubię", "nie lubię"),
        ("tak", "nie"),
        ("zawsze", "nigdy"),
        ("preferuję", "nie preferuję"),
        ("chcę", "nie chcę"),
        ("mogę", "nie mogę"),
        ("umiem", "nie umiem"),
    ]

    def __init__(
        self,
        duplicate_threshold: float = 0.92,
        revision_threshold: float = 0.55,
    ):
        self.DUPLICATE_THRESHOLD = duplicate_threshold
        self.REVISION_THRESHOLD = revision_threshold

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(
        self,
        user_id: str,
        new_fact: str,
        tags: Optional[List[str]] = None,
        *,
        existing_facts: Optional[List[Dict[str, Any]]] = None,
    ) -> ConsistencyVerdict:
        """
        Sprawdź spójność nowego faktu z istniejącą wiedzą usera.

        Args:
            user_id: id usera
            new_fact: treść nowego faktu
            tags: opcjonalne tagi faktu
            existing_facts: opcjonalnie podaj fakty wprost (testy); inaczej query z DB

        Returns:
            ConsistencyVerdict
        """
        if not new_fact or not new_fact.strip():
            return ConsistencyVerdict(
                classification="new_fact",
                confidence=1.0,
                reasoning="Empty fact text",
                suggested_action="store",
            )

        # 1. Pobierz istniejące fakty usera
        facts = existing_facts or self._load_user_facts(user_id)
        if not facts:
            return ConsistencyVerdict(
                classification="new_fact",
                confidence=1.0,
                reasoning="No existing facts for user",
                suggested_action="store",
            )

        # 2. Oblicz combined similarity (TF-IDF + string)
        scored = self._score_against_existing(new_fact, facts)
        if not scored:
            return ConsistencyVerdict(
                classification="new_fact",
                confidence=0.95,
                reasoning="Could not compute similarity (empty tokens)",
                suggested_action="store",
            )

        best_match = scored[0]  # highest combined
        best_id = best_match["id"]
        best_content = best_match["content"]
        combined = best_match["combined"]
        tfidf_sim = best_match["tfidf"]
        string_sim = best_match["string"]

        # 3. Sprawdzamy KG (czy istnieje krawędź contradicts/refines)
        kg_relation = self._check_kg_relation(best_id, new_fact, user_id)

        # 4. Klasyfikacja
        verdict = self._classify(
            new_fact=new_fact,
            best_content=best_content,
            best_id=best_id,
            combined=combined,
            tfidf_sim=tfidf_sim,
            string_sim=string_sim,
            kg_relation=kg_relation,
        )

        # 5. Persist check to DB
        self._persist_check(user_id, new_fact, verdict)

        return verdict

    # ------------------------------------------------------------------
    # Similarity scoring
    # ------------------------------------------------------------------

    def _score_against_existing(
        self, new_fact: str, facts: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Score new_fact against each existing fact using TF-IDF + string similarity."""
        new_tokens = tokenize(new_fact)
        if not new_tokens:
            return []

        fact_tokens = [tokenize(f["content"]) for f in facts]
        # Include new_fact in corpus for proper IDF
        all_tokens = fact_tokens + [new_tokens]
        n_docs = len(all_tokens)

        df = build_df(all_tokens)
        df = prune_vocab(df, n_docs)

        new_vec = tfidf_vector(new_tokens, df, n_docs)

        scored = []
        for i, f in enumerate(facts):
            # TF-IDF cosine
            fv = tfidf_vector(fact_tokens[i], df, n_docs)
            tfidf_pairs = topk_cosine(new_vec, [(f["id"], fv)], k=1)
            tfidf_sim = float(tfidf_pairs[0][1]) if tfidf_pairs else 0.0

            # String similarity
            string_sim = difflib.SequenceMatcher(
                None, new_fact.lower(), f["content"].lower()
            ).ratio()

            # Combined: weighted average (TF-IDF = 0.6, string = 0.4)
            combined = (tfidf_sim * 0.6) + (string_sim * 0.4)

            scored.append(
                {
                    "id": f["id"],
                    "content": f["content"],
                    "tfidf": tfidf_sim,
                    "string": string_sim,
                    "combined": combined,
                }
            )

        scored.sort(key=lambda x: x["combined"], reverse=True)
        return scored

    # ------------------------------------------------------------------
    # KG relation check
    # ------------------------------------------------------------------

    def _check_kg_relation(
        self, matched_node_id: str, new_fact: str, user_id: str
    ) -> Optional[str]:
        """Check if knowledge graph has a relevant edge for matched node."""
        try:
            from aihub.knowledge_graph import _graph

            if matched_node_id not in _graph.nodes:
                return None

            # Check edges from/to this node
            for edge in _graph.edges:
                if (
                    edge.source_id == matched_node_id
                    or edge.target_id == matched_node_id
                ):
                    if edge.relation_type in ("contradicts", "refines", "supersedes"):
                        return edge.relation_type
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------

    def _classify(
        self,
        new_fact: str,
        best_content: str,
        best_id: str,
        combined: float,
        tfidf_sim: float,
        string_sim: float,
        kg_relation: Optional[str],
    ) -> ConsistencyVerdict:
        """Classify based on similarity + KG + keyword analysis."""

        # --- KG override: if KG already says contradiction ---
        if kg_relation == "contradicts":
            return ConsistencyVerdict(
                classification="conflict",
                confidence=0.90,
                matched_node_id=best_id,
                matched_content=best_content,
                similarity_score=combined,
                reasoning=f"KG edge 'contradicts' found (combined={combined:.2f})",
                suggested_action="flag",
                metadata={"kg_relation": kg_relation},
            )

        if kg_relation in ("refines", "supersedes"):
            return ConsistencyVerdict(
                classification="revision",
                confidence=0.85,
                matched_node_id=best_id,
                matched_content=best_content,
                similarity_score=combined,
                reasoning=f"KG edge '{kg_relation}' found (combined={combined:.2f})",
                suggested_action="supersede",
                metadata={"kg_relation": kg_relation},
            )

        # --- Duplicate ---
        # Fallback: if string_sim alone is very high, trust it even when TF-IDF
        # degenerates (e.g. single document in corpus → all IDF weights = 0).
        effective_sim = max(combined, string_sim)
        if effective_sim >= self.DUPLICATE_THRESHOLD:
            return ConsistencyVerdict(
                classification="duplicate",
                confidence=min(1.0, effective_sim),
                matched_node_id=best_id,
                matched_content=best_content,
                similarity_score=effective_sim,
                reasoning=f"High similarity {effective_sim:.3f} >= {self.DUPLICATE_THRESHOLD} (combined={combined:.3f}, string={string_sim:.3f})",
                suggested_action="merge",
            )

        # --- Revision vs Conflict (mid-range similarity) ---
        if combined >= self.REVISION_THRESHOLD:
            is_revision = self._detect_revision_signal(new_fact, best_content)
            is_conflict = self._detect_conflict_signal(new_fact, best_content)

            if is_conflict and not is_revision:
                return ConsistencyVerdict(
                    classification="conflict",
                    confidence=min(1.0, combined + self.CONFLICT_KEYWORD_BOOST),
                    matched_node_id=best_id,
                    matched_content=best_content,
                    similarity_score=combined,
                    reasoning=f"Conflict detected: negation/antonym pattern (combined={combined:.2f})",
                    suggested_action="flag",
                    metadata={"conflict_type": "negation_pattern"},
                )

            if is_revision:
                return ConsistencyVerdict(
                    classification="revision",
                    confidence=min(1.0, combined + 0.05),
                    matched_node_id=best_id,
                    matched_content=best_content,
                    similarity_score=combined,
                    reasoning=f"Revision keywords detected (combined={combined:.2f})",
                    suggested_action="supersede",
                )

            # High enough but no clear signal → uncertain
            if combined < 0.70:
                return ConsistencyVerdict(
                    classification="uncertain",
                    confidence=combined,
                    matched_node_id=best_id,
                    matched_content=best_content,
                    similarity_score=combined,
                    reasoning=f"Mid-range similarity {combined:.2f} — unclear relation",
                    suggested_action="store",
                    metadata={"needs_review": True},
                )

            # Default: treat as potential revision at high similarity
            return ConsistencyVerdict(
                classification="revision",
                confidence=combined * 0.8,
                matched_node_id=best_id,
                matched_content=best_content,
                similarity_score=combined,
                reasoning=f"High topical overlap {combined:.2f} — likely revision",
                suggested_action="supersede",
            )

        # --- Uncertain ---
        if combined >= self.UNCERTAIN_LOW:
            return ConsistencyVerdict(
                classification="uncertain",
                confidence=combined,
                matched_node_id=best_id,
                matched_content=best_content,
                similarity_score=combined,
                reasoning=f"Low overlap {combined:.2f} — insufficient data for classification",
                suggested_action="store",
                metadata={"needs_review": True},
            )

        # --- New fact ---
        return ConsistencyVerdict(
            classification="new_fact",
            confidence=max(0.5, 1.0 - combined),
            matched_node_id=best_id if combined > 0.15 else None,
            matched_content=best_content if combined > 0.15 else None,
            similarity_score=combined,
            reasoning=f"Low similarity {combined:.2f} — new information",
            suggested_action="store",
        )

    # ------------------------------------------------------------------
    # Revision / conflict signal detection
    # ------------------------------------------------------------------

    def _detect_revision_signal(self, new_fact: str, old_fact: str) -> bool:
        """Detect if new_fact is updating/revising old_fact."""
        nl = new_fact.lower()
        return any(signal in nl for signal in self._REVISION_SIGNALS)

    def _detect_conflict_signal(self, new_fact: str, old_fact: str) -> bool:
        """Detect if new_fact contradicts old_fact via negation patterns."""
        nl = new_fact.lower()
        ol = old_fact.lower()

        for pos, neg in self._CONFLICT_NEGATION_PAIRS:
            # New says negative, old says positive (or vice versa)
            if (pos in ol and neg in nl) or (neg in ol and pos in nl):
                return True

        return False

    # ------------------------------------------------------------------
    # DB operations
    # ------------------------------------------------------------------

    def _load_user_facts(self, user_id: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Load user's L2 facts from memory_nodes."""
        rows = fetch_all(
            """
            SELECT id, content, tags, meta, importance, confidence, ts
            FROM memory_nodes
            WHERE user_id=? AND layer='L2' AND deleted=0
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        result = []
        for r in rows:
            result.append(
                {
                    "id": r["id"],
                    "content": r["content"],
                    "tags": json_loads(r["tags"]) if r["tags"] else [],
                    "importance": float(r["importance"]),
                    "confidence": float(r["confidence"]),
                    "ts": float(r["ts"]),
                }
            )
        return result

    def _persist_check(
        self, user_id: str, fact_text: str, verdict: ConsistencyVerdict
    ) -> None:
        """Persist consistency check result to DB."""
        try:
            check_id = hashlib.sha256(
                f"{user_id}:{fact_text}:{time.time_ns()}".encode()
            ).hexdigest()[:24]

            exec_one(
                """
                INSERT INTO consistency_checks(
                    id, user_id, fact_text, classification, confidence,
                    matched_node_id, similarity_score, reasoning,
                    suggested_action, metadata, ts
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    check_id,
                    user_id,
                    fact_text[:2000],
                    verdict.classification,
                    verdict.confidence,
                    verdict.matched_node_id or "",
                    verdict.similarity_score,
                    verdict.reasoning,
                    verdict.suggested_action,
                    json_dumps(verdict.metadata),
                    now_ts(),
                ),
            )
        except Exception:
            logger.debug("Failed to persist consistency check", exc_info=True)

    # ------------------------------------------------------------------
    # Apply verdict: modify KG edges, mark superseded etc.
    # ------------------------------------------------------------------

    def apply_verdict(
        self, user_id: str, new_node_id: str, verdict: ConsistencyVerdict
    ) -> Dict[str, Any]:
        """
        Apply consistency verdict to the knowledge graph.

        - duplicate → merge nodes
        - revision  → add 'supersedes' edge, mark old node confidence down
        - conflict  → add 'contradicts' edge, reduce both confidences
        - uncertain → add 'related_to' edge with low weight
        - new_fact  → no-op (fact already stored)
        """
        actions_taken: List[str] = []

        if not verdict.matched_node_id:
            return {"actions": [], "classification": verdict.classification}

        try:
            from aihub.knowledge_graph import (
                KnowledgeEdge,
                _graph,
                add_edge,
                persist_edge,
            )

            matched_id = verdict.matched_node_id

            if verdict.classification == "duplicate":
                _graph.merge_nodes(new_node_id, matched_id, keep_node_id=matched_id)
                actions_taken.append(f"merged {new_node_id} → {matched_id}")

            elif verdict.classification == "revision":
                edge = KnowledgeEdge(
                    source_id=new_node_id,
                    target_id=matched_id,
                    relation_type="supersedes",
                    weight=verdict.confidence,
                )
                add_edge(edge)
                persist_edge(
                    f"{new_node_id}:{matched_id}:supersedes",
                    new_node_id,
                    matched_id,
                    "supersedes",
                    verdict.confidence,
                )
                # Reduce old node confidence
                exec_one(
                    "UPDATE memory_nodes SET confidence = confidence * 0.5 WHERE id=?",
                    (matched_id,),
                )
                actions_taken.append(f"supersedes edge {new_node_id} → {matched_id}")
                actions_taken.append(f"reduced confidence of {matched_id}")

            elif verdict.classification == "conflict":
                edge = KnowledgeEdge(
                    source_id=new_node_id,
                    target_id=matched_id,
                    relation_type="contradicts",
                    weight=verdict.confidence,
                )
                add_edge(edge)
                persist_edge(
                    f"{new_node_id}:{matched_id}:contradicts",
                    new_node_id,
                    matched_id,
                    "contradicts",
                    verdict.confidence,
                )
                # Reduce both confidences slightly
                exec_one(
                    "UPDATE memory_nodes SET confidence = confidence * 0.7 WHERE id=?",
                    (new_node_id,),
                )
                exec_one(
                    "UPDATE memory_nodes SET confidence = confidence * 0.7 WHERE id=?",
                    (matched_id,),
                )
                actions_taken.append(f"contradicts edge {new_node_id} ↔ {matched_id}")

            elif verdict.classification == "uncertain":
                edge = KnowledgeEdge(
                    source_id=new_node_id,
                    target_id=matched_id,
                    relation_type="related_to",
                    weight=max(0.2, verdict.similarity_score * 0.5),
                )
                add_edge(edge)
                persist_edge(
                    f"{new_node_id}:{matched_id}:related_to",
                    new_node_id,
                    matched_id,
                    "related_to",
                    max(0.2, verdict.similarity_score * 0.5),
                )
                actions_taken.append(f"related_to edge {new_node_id} → {matched_id}")

            # Event log
            append_event(
                user_id,
                "consistency.applied",
                {
                    "classification": verdict.classification,
                    "new_node": new_node_id,
                    "matched_node": matched_id,
                    "actions": actions_taken,
                },
            )

        except Exception:
            logger.debug("apply_verdict failed", exc_info=True)

        return {
            "classification": verdict.classification,
            "actions": actions_taken,
        }

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def get_recent_checks(self, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent consistency checks for diagnostics/cockpit."""
        rows = fetch_all(
            """
            SELECT id, classification, confidence, matched_node_id,
                   similarity_score, reasoning, suggested_action, ts
            FROM consistency_checks
            WHERE user_id=?
            ORDER BY ts DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [
            {
                "id": r["id"],
                "classification": r["classification"],
                "confidence": float(r["confidence"]),
                "matched_node_id": r["matched_node_id"],
                "similarity_score": float(r["similarity_score"]),
                "reasoning": r["reasoning"],
                "suggested_action": r["suggested_action"],
                "ts": float(r["ts"]),
            }
            for r in rows
        ]

    def get_stats(self, user_id: str) -> Dict[str, Any]:
        """Get consistency check statistics."""
        rows = fetch_all(
            """
            SELECT classification, COUNT(*) as cnt, AVG(confidence) as avg_conf
            FROM consistency_checks
            WHERE user_id=?
            GROUP BY classification
            """,
            (user_id,),
        )
        stats: Dict[str, Any] = {}
        total = 0
        for r in rows:
            stats[r["classification"]] = {
                "count": int(r["cnt"]),
                "avg_confidence": round(float(r["avg_conf"]), 3),
            }
            total += int(r["cnt"])
        stats["total_checks"] = total
        return stats


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_consistency_engine = ConsistencyEngine()


def check_consistency(
    user_id: str,
    new_fact: str,
    tags: Optional[List[str]] = None,
    *,
    existing_facts: Optional[List[Dict[str, Any]]] = None,
) -> ConsistencyVerdict:
    """Public API — check fact consistency."""
    return _consistency_engine.check(
        user_id, new_fact, tags, existing_facts=existing_facts
    )


def apply_consistency_verdict(
    user_id: str, new_node_id: str, verdict: ConsistencyVerdict
) -> Dict[str, Any]:
    """Public API — apply verdict to KG."""
    return _consistency_engine.apply_verdict(user_id, new_node_id, verdict)


def get_consistency_checks(user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Public API — diagnostics."""
    return _consistency_engine.get_recent_checks(user_id, limit)


def get_consistency_stats(user_id: str) -> Dict[str, Any]:
    """Public API — statistics."""
    return _consistency_engine.get_stats(user_id)
