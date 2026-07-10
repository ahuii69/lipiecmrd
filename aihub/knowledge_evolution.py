#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import difflib
import logging
from collections import defaultdict
from typing import Any, Dict, List, Tuple

from aihub.db import (
    append_event,
    exec_one,
    fetch_all,
    fetch_one,
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


class KnowledgeEvolution:
    """
    System ewolucji wiedzy w pamięci AI.
    - Deduplikacja faktów
    - Usuwanie nieaktualnych informacji
    - Wzmacnianie faktów ważnych
    - Optymalizacja pamięci
    """

    def __init__(self, similarity_threshold: float = 0.75):
        self.similarity_threshold = similarity_threshold

    def _compute_string_similarity(self, s1: str, s2: str) -> float:
        """Obliczenie podobieństwa tekstowego między dwoma stringami."""
        return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    def _compute_semantic_similarity(
        self, facts: List[Dict[str, Any]]
    ) -> List[Tuple[str, str, float]]:
        """
        Obliczenie semantycznego podobieństwa między faktami.
        Returns: Lista (id1, id2, similarity)
        """
        if not facts:
            return []

        contents = [f["content"] for f in facts]
        tokens = [tokenize(c) for c in contents]

        # Build TF-IDF
        df = build_df(tokens)
        n_docs = len(tokens)
        if n_docs == 0:
            return []

        df = prune_vocab(df, n_docs)

        # Wektoryzacja
        vecs = []
        for toks in tokens:
            vecs.append(tfidf_vector(toks, df, n_docs))

        # Porównanie każdej pary
        similarities = []
        for i in range(len(facts)):
            for j in range(i + 1, len(facts)):
                ranked = topk_cosine(vecs[i], [(facts[j]["id"], vecs[j])], k=1)
                if ranked:
                    sim_score = float(ranked[0][1])
                    if sim_score > self.similarity_threshold:
                        similarities.append((facts[i]["id"], facts[j]["id"], sim_score))

        return similarities

    def _cluster_similar_facts_ann(
        self,
        facts: List[Dict[str, Any]],
        top_k: int = 10,
        similarity_threshold: float = 0.8,
    ) -> List[List[Dict[str, Any]]]:
        """Cluster semantically similar facts using ANN (FAISS) with cosine/IP."""
        if len(facts) < 2:
            return []

        try:
            import faiss
            import numpy as np

            from aihub.embedding_engine import embed_batch

            contents = [str(f.get("content", "")) for f in facts]
            responses = embed_batch(contents, input_type="document")
            # Filter out failed embeddings
            valid = [(i, r) for i, r in enumerate(responses) if r is not None]
            if len(valid) < 2:
                return []
            valid_facts = [facts[i] for i, _ in valid]
            embeddings = np.array([r.vector for _, r in valid], dtype="float32")

            # Cosine via inner-product on L2-normalized vectors
            faiss.normalize_L2(embeddings)
            dim = int(embeddings.shape[1])
            index = faiss.IndexFlatIP(dim)
            index.add(embeddings)

            n = len(valid_facts)
            k = min(max(2, top_k + 1), n)
            sims, neigh = index.search(embeddings, k)

            parent = list(range(n))

            def find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            def union(a: int, b: int) -> None:
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

            for i in range(n):
                for j, s in zip(neigh[i], sims[i]):
                    j = int(j)
                    if j < 0 or j == i:
                        continue
                    if float(s) > similarity_threshold:
                        union(i, j)

            clusters_idx: Dict[int, List[int]] = defaultdict(list)
            for i in range(n):
                clusters_idx[find(i)].append(i)

            clusters = []
            for members in clusters_idx.values():
                if len(members) > 1:
                    clusters.append([valid_facts[i] for i in members])

            return clusters

        except Exception as e:  # noqa: BLE001
            logger.debug(
                "ANN clustering unavailable, fallback to sparse similarity: %s", e
            )

        # Fallback (legacy sparse similarity)
        sim_pairs = self._compute_semantic_similarity(facts)
        if not sim_pairs:
            return []

        parent = list(range(len(facts)))
        id_to_idx = {f["id"]: i for i, f in enumerate(facts)}

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[rb] = ra

        for f1, f2, score in sim_pairs:
            if score > similarity_threshold and f1 in id_to_idx and f2 in id_to_idx:
                union(id_to_idx[f1], id_to_idx[f2])

        clusters_idx: Dict[int, List[int]] = defaultdict(list)
        for i in range(len(facts)):
            clusters_idx[find(i)].append(i)

        clusters = []
        for members in clusters_idx.values():
            if len(members) > 1:
                clusters.append([facts[i] for i in members])
        return clusters

    def _merge_facts(
        self, fact1: Dict[str, Any], fact2: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Łączenie dwóch podobnych faktów.
        Preferuje nowszy, bardziej pewny, lub ważniejszy.
        """
        # Wybierz lepszy na podstawie importance + confidence
        score1 = (
            float(fact1.get("importance", 0.5)) + float(fact1.get("confidence", 0.5))
        ) / 2.0
        score2 = (
            float(fact2.get("importance", 0.5)) + float(fact2.get("confidence", 0.5))
        ) / 2.0

        if score1 >= score2:
            winner, loser = fact1, fact2
        else:
            winner = fact2
            loser = fact1

        # Dodaj tagy z obydwu
        merged_tags = list(set(winner.get("tags", []) + loser.get("tags", [])))

        # Połącz metadane
        merged_meta = dict(winner.get("meta", {}))
        merged_meta["merged_with"] = loser["id"]
        merged_meta["merged_at"] = now_ts()

        return {
            **winner,
            "tags": merged_tags,
            "meta": merged_meta,
        }

    def deduplicate(self, user_id: str, layer: str = "L2") -> Dict[str, Any]:
        """
        Deduplikacja faktów dla danego użytkownika i warstwy.

        Args:
            user_id: ID użytkownika
            layer: L1 (episodic) lub L2 (semantic)

        Returns:
            Stats deduplikacji
        """
        try:
            # Pobierz wszystkie fakty
            rows = fetch_all(
                """
            SELECT id, content, tags, meta, ts, importance, confidence
            FROM memory_nodes
            WHERE user_id=? AND layer=? AND deleted=0
            ORDER BY ts DESC
            """,
                (user_id, layer),
            )

            if not rows:
                return {"ok": True, "user_id": user_id, "layer": layer, "duplicates": 0}

            facts: List[Dict[str, Any]] = []
            for r in rows:
                item = dict(r)
                item["tags"] = json_loads(item.get("tags", "[]")) or []
                item["meta"] = json_loads(item.get("meta", "{}")) or {}
                facts.append(item)

            clusters = self._cluster_similar_facts_ann(
                facts,
                top_k=10,
                similarity_threshold=0.8,
            )

            if not clusters:
                logger.debug(f"No duplicates found for user {user_id} in {layer}")
                return {
                    "ok": True,
                    "user_id": user_id,
                    "layer": layer,
                    "duplicates": 0,
                }

            # Merge per-cluster (winner keeps merged tags/meta; others deleted)
            merged_count = 0

            for cluster in clusters:
                # Winner by importance/confidence and recency tie-breaker
                winner = max(
                    cluster,
                    key=lambda f: (
                        float(f.get("importance", 0.5))
                        + float(f.get("confidence", 0.5)),
                        float(f.get("ts", 0.0)),
                    ),
                )
                winner_id = winner["id"]

                merged_tags = set(winner.get("tags", []))
                merged_meta = dict(winner.get("meta", {}))
                losers = []

                for f in cluster:
                    if f["id"] == winner_id:
                        continue
                    losers.append(f["id"])
                    merged_tags.update(f.get("tags", []))
                    src_ids = merged_meta.get("merged_from", [])
                    if not isinstance(src_ids, list):
                        src_ids = [str(src_ids)]
                    src_ids.append(f["id"])
                    merged_meta["merged_from"] = list(dict.fromkeys(src_ids))

                merged_meta["merged_at"] = now_ts()

                exec_one(
                    """
                    UPDATE memory_nodes
                    SET tags=?, meta=?
                    WHERE id=?
                    """,
                    (
                        json_dumps(sorted(merged_tags)),
                        json_dumps(merged_meta),
                        winner_id,
                    ),
                )

                for loser_id in losers:
                    exec_one(
                        "UPDATE memory_nodes SET deleted=1 WHERE id=?", (loser_id,)
                    )
                    merged_count += 1

            append_event(
                user_id,
                "knowledge.dedup",
                {
                    "layer": layer,
                    "duplicates_found": sum(max(0, len(c) - 1) for c in clusters),
                    "merged": merged_count,
                },
            )

            return {
                "ok": True,
                "user_id": user_id,
                "layer": layer,
                "duplicates": sum(max(0, len(c) - 1) for c in clusters),
                "merged": merged_count,
                "ts": now_ts(),
            }

        except Exception as e:
            logger.error(f"Error in deduplicate for user {user_id}: {e}", exc_info=True)
            return {
                "ok": False,
                "user_id": user_id,
                "layer": layer,
                "error": str(e),
                "ts": now_ts(),
            }

    def reinforce(
        self, user_id: str, fact_id: str, increment: float = 0.1
    ) -> Dict[str, Any]:
        """
        Wzmocnienie ważnego faktu (zwiększenie importance + confidence).
        """
        try:
            row = fetch_one(
                "SELECT * FROM memory_nodes WHERE id=? AND user_id=?",
                (fact_id, user_id),
            )

            if not row:
                return {
                    "ok": False,
                    "error": "fact not found",
                    "ts": now_ts(),
                }

            old_importance = float(row["importance"])
            old_confidence = float(row["confidence"])

            new_importance = min(0.99, old_importance + increment)
            new_confidence = min(0.99, old_confidence + increment)

            exec_one(
                "UPDATE memory_nodes SET importance=?, confidence=? WHERE id=?",
                (new_importance, new_confidence, fact_id),
            )

            append_event(
                user_id,
                "knowledge.reinforce",
                {
                    "fact_id": fact_id,
                    "old_importance": old_importance,
                    "new_importance": new_importance,
                    "old_confidence": old_confidence,
                    "new_confidence": new_confidence,
                },
            )

            logger.debug(f"Reinforced fact {fact_id} for user {user_id}")

            return {
                "ok": True,
                "fact_id": fact_id,
                "old_importance": old_importance,
                "new_importance": new_importance,
                "old_confidence": old_confidence,
                "new_confidence": new_confidence,
                "ts": now_ts(),
            }

        except Exception as e:
            logger.error(f"Error in reinforce for user {user_id}: {e}", exc_info=True)
            return {
                "ok": False,
                "error": str(e),
                "ts": now_ts(),
            }

    def archive_stale(
        self, user_id: str, days: int = 90, archival_layer: str = "L3"
    ) -> Dict[str, Any]:
        """
        Archiwizacja starych nieużywanych faktów do warstwy archiwum.

        Returns: Stats archiwizacji
        """
        try:
            threshold_ts = now_ts() - (days * 86400)

            # Znaj stare, nisko oceniane fakty
            old_facts = fetch_all(
                """
            SELECT id, content, tags, meta, importance, confidence
            FROM memory_nodes
            WHERE user_id=? AND layer IN ('L1','L2')
            AND deleted=0
            AND ts < ?
            AND (importance < 0.45 OR confidence < 0.45)
            ORDER BY ts ASC
            LIMIT 1000
            """,
                (user_id, threshold_ts),
            )

            if not old_facts:
                return {
                    "ok": True,
                    "user_id": user_id,
                    "archived": 0,
                    "ts": now_ts(),
                }

            archived_count = 0

            for fact in old_facts:
                try:
                    # Przenieś do archiwum
                    exec_one(
                        """
                    UPDATE memory_nodes
                    SET layer=?
                    WHERE id=?
                    """,
                        (archival_layer, fact["id"]),
                    )

                    append_event(
                        user_id,
                        "knowledge.archive",
                        {"fact_id": fact["id"], "layer_before": "L1/L2"},
                    )

                    archived_count += 1

                except Exception as e:
                    logger.debug(f"Error archiving fact {fact['id']}: {e}")

            logger.info(f"Archived {archived_count} stale facts for user {user_id}")

            return {
                "ok": True,
                "user_id": user_id,
                "archived": archived_count,
                "threshold_days": days,
                "ts": now_ts(),
            }

        except Exception as e:
            logger.error(
                f"Error in archive_stale for user {user_id}: {e}",
                exc_info=True,
            )
            return {
                "ok": False,
                "user_id": user_id,
                "error": str(e),
                "ts": now_ts(),
            }

    def evolve_all(self, user_id: str) -> Dict[str, Any]:
        """
        Pełny cykl ewolucji wiedzy (deduplikacja + oprawianie + archiwizacja).
        """
        try:
            results = {
                "dedup_l1": self.deduplicate(user_id, "L1"),
                "dedup_l2": self.deduplicate(user_id, "L2"),
                "archived": self.archive_stale(user_id, days=90),
            }

            success = all(r.get("ok") for r in results.values())
            total_updates = (
                results["dedup_l1"].get("merged", 0)
                + results["dedup_l2"].get("merged", 0)
                + results["archived"].get("archived", 0)
            )

            append_event(
                user_id,
                "knowledge.evolve_all",
                {
                    "dedup_l1": results["dedup_l1"],
                    "dedup_l2": results["dedup_l2"],
                    "archived": results["archived"],
                    "total_updates": total_updates,
                },
            )

            logger.info(
                f"Knowledge evolution for user {user_id}: {total_updates} updates"
            )

            return {
                "ok": success,
                "user_id": user_id,
                "results": results,
                "total_updates": total_updates,
                "ts": now_ts(),
            }

        except Exception as e:
            logger.error(f"Error in evolve_all for user {user_id}: {e}", exc_info=True)
            return {
                "ok": False,
                "user_id": user_id,
                "error": str(e),
                "ts": now_ts(),
            }


# Singleton
_evolution_engine = KnowledgeEvolution()


def deduplicate(user_id: str, layer: str = "L2") -> Dict[str, Any]:
    """Public API dla deduplikacji."""
    return _evolution_engine.deduplicate(user_id, layer)


def reinforce(user_id: str, fact_id: str, increment: float = 0.1) -> Dict[str, Any]:
    """Public API dla wzmocnienia."""
    return _evolution_engine.reinforce(user_id, fact_id, increment)


def archive_stale(
    user_id: str, days: int = 90, archival_layer: str = "L3"
) -> Dict[str, Any]:
    """Public API dla archiwizacji."""
    return _evolution_engine.archive_stale(user_id, days, archival_layer)


def evolve_all(user_id: str) -> Dict[str, Any]:
    """Public API dla pełnego cyklu ewolucji."""
    return _evolution_engine.evolve_all(user_id)
