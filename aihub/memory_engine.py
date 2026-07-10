#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import logging
import re
import time
from typing import Any, Dict, List, Tuple

from aihub.config import EPISODES_MAX_PER_USER, LTM_MAX_FACTS_PER_USER, STM_MAX_MESSAGES
from aihub.memory_errors import MemoryVectorWriteError
from aihub.db import (
    append_event,
    exec_one,
    fetch_all,
    get_stm,
    insert_stm_message,
    now_ts,
    prune_stm,
    search_nodes_fts,
    upsert_node,
)
from aihub.vector_hook import remember_turn
from aihub.vector_index import (
    build_df,
    prune_vocab,
    tfidf_vector,
    tokenize,
    topk_cosine,
)

logger = logging.getLogger(__name__)


def _ingest_meta(meta: Dict[str, Any] | None, **extra: Any) -> Dict[str, Any]:
    """Meta faktów: ``memory_scope=user`` + pola z czatu (np. ``session_id``)."""
    out: Dict[str, Any] = {**(meta or {})}
    out.setdefault("memory_scope", "user")
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def _id_for(text: str, user_id: str, layer: str) -> str:
    h = hashlib.sha256()
    h.update(layer.encode("utf-8"))
    h.update(b"\0")
    h.update(user_id.encode("utf-8"))
    h.update(b"\0")
    h.update(text.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _l2_tags_to_memory_v2_type(tags: List[str]) -> str:
    low = {str(t).lower() for t in (tags or [])}
    if "preference" in low:
        return "preference"
    if "procedural" in low or "procedure" in low:
        return "procedural"
    return "fact"


def _mirror_l2_fact_to_memory_v2(
    user_id: str,
    fact: str,
    tags: List[str],
    node_id: str,
    importance: float,
    confidence: float,
) -> None:
    try:
        from aihub.memory_core import get_memory_core

        mt = _l2_tags_to_memory_v2_type(tags)
        title = fact if len(fact) <= 120 else (fact[:117] + "…")
        get_memory_core().v2_create_item(
            user_id=user_id,
            memory_type=mt,  # type: ignore[arg-type]
            scope="user",
            title=title,
            content=fact,
            source_kind="explicit_learning",
            source_ref=node_id,
            importance_score=float(importance),
            confidence_score=float(confidence),
        )
    except Exception:
        logger.debug("memory.l2.v2_mirror_failed", exc_info=True)


def add_stm(user_id: str, role: str, content: str, meta: Dict[str, Any]) -> str:
    msg_id = hashlib.md5(
        f"{user_id}:{role}:{time.time_ns()}".encode("utf-8")
    ).hexdigest()
    insert_stm_message(msg_id, user_id, role, content, meta)
    prune_stm(user_id, STM_MAX_MESSAGES)
    append_event(
        user_id, "memory.stm.add", {"id": msg_id, "role": role, "len": len(content)}
    )
    return msg_id


def _get_psyche_modulation(user_id: str) -> Dict[str, float]:
    """Get psyche-based modulation factors for importance/confidence scoring."""
    try:
        from aihub.db import get_psyche

        st = get_psyche(user_id)
        if not st:
            return {"imp_mod": 0.0, "conf_mod": 0.0, "max_facts": 3}
        energy = float(st.get("energy", 0.7))
        focus = float(st.get("focus", 0.65))
        # High focus → slight importance boost; low energy → slight confidence penalty
        imp_mod = 0.05 * (focus - 0.5)
        conf_mod = 0.05 * (energy - 0.5)
        # Learning throttle: low energy → fewer facts per turn
        if energy < 0.35:
            max_facts = 1
        elif focus >= 0.65:
            max_facts = 3
        else:
            max_facts = 2
        return {"imp_mod": imp_mod, "conf_mod": conf_mod, "max_facts": max_facts}
    except Exception:  # noqa: BLE001
        return {"imp_mod": 0.0, "conf_mod": 0.0, "max_facts": 3}


def _importance_from_text(text: str, psyche_mod: float = 0.0) -> float:
    t = text.lower()
    imp = 0.45
    if any(k in t for k in ["zapamiętaj", "ważne", "kluczowe", "nigdy", "zawsze"]):
        imp += 0.25
    if len(text) > 500:
        imp += 0.10
    imp += psyche_mod
    return max(0.0, min(1.0, imp))


def _confidence_from_text(text: str, psyche_mod: float = 0.0) -> float:
    # heuristic: declarative statements -> higher confidence
    t = text.lower()
    conf = 0.60
    if any(k in t for k in ["jestem", "mam", "to jest", "nazywam się", "mój", "moja"]):
        conf += 0.10
    if any(k in t for k in ["chyba", "może", "nie wiem", "wydaje mi się"]):
        conf -= 0.15
    conf += psyche_mod
    return max(0.20, min(0.95, conf))


def _feed_knowledge_graph(
    node_id: str,
    node_type: str,
    content: str,
    confidence: float,
    tags: List[str],
    meta: Dict[str, Any],
    user_id: str = "",
) -> None:
    """Feed a fact/episode into the knowledge graph."""
    try:
        from aihub.knowledge_graph import KnowledgeEdge, KnowledgeNode
        from aihub.knowledge_graph import add_edge as kg_add_edge
        from aihub.knowledge_graph import add_node as kg_add_node
        from aihub.knowledge_graph import persist_edge

        kg_add_node(
            KnowledgeNode(
                node_id=node_id,
                node_type=node_type,
                content=content,
                confidence=confidence,
                source=meta.get("source", "memory_engine"),
                metadata={"tags": tags, "user_id": user_id},
            )
        )

        # Persist to SQLite
        try:
            from aihub.knowledge_graph import persist_node

            persist_node(node_id, node_type, content, user_id)
        except Exception:  # noqa: BLE001
            logger.debug("KG persist_node failed", exc_info=True)

        # user → fact edge
        if user_id and node_type == "fact":
            # Ensure user node exists for in-memory graph relation integrity
            kg_add_node(
                KnowledgeNode(
                    node_id=user_id,
                    node_type="user",
                    content=f"user:{user_id}",
                    confidence=1.0,
                    source="memory_engine",
                    metadata={"user_id": user_id},
                )
            )
            edge = KnowledgeEdge(
                source_id=user_id,
                target_id=node_id,
                relation_type="user_fact",
                weight=confidence,
            )
            kg_add_edge(edge)
            persist_edge(
                f"{user_id}:{node_id}:user_fact",
                user_id,
                node_id,
                "user_fact",
                confidence,
            )

        # episode → fact edge
        if node_type == "fact":
            source_ep = meta.get("source_episode")
            if source_ep:
                edge = KnowledgeEdge(
                    source_id=source_ep,
                    target_id=node_id,
                    relation_type="episode_fact",
                    weight=confidence,
                )
                kg_add_edge(edge)
                persist_edge(
                    f"{source_ep}:{node_id}:episode_fact",
                    source_ep,
                    node_id,
                    "episode_fact",
                    confidence,
                )
    except Exception:  # noqa: BLE001
        logger.debug("_feed_knowledge_graph failed", exc_info=True)


def add_episode(user_id: str, summary: str, meta: Dict[str, Any]) -> str:
    node_id = _id_for(summary, user_id, "L1")
    tags = ["episode", meta.get("intent", "chat")]
    ts = now_ts()
    imp = max(0.55, _importance_from_text(summary))
    conf = max(0.55, _confidence_from_text(summary))
    upsert_node(node_id, user_id, "L1", summary, tags, meta, ts, imp, conf)
    append_event(
        user_id, "memory.l1.add", {"id": node_id, "importance": imp, "confidence": conf}
    )
    _feed_knowledge_graph(
        node_id, "episode", summary, conf, tags, meta, user_id=user_id
    )
    _enforce_caps(user_id)
    return node_id


def add_fact(user_id: str, fact: str, tags: List[str], meta: Dict[str, Any]) -> str:
    from aihub.memory_errors import require_user_id

    user_id = require_user_id(user_id)
    node_id = _id_for(fact, user_id, "L2")
    ts = now_ts()
    imp = max(0.60, _importance_from_text(fact))
    conf = max(0.55, _confidence_from_text(fact))

    # ── ETAP 9: consistency check before storing ──
    try:
        from aihub.consistency_engine import (
            apply_consistency_verdict,
            check_consistency,
        )

        verdict = check_consistency(user_id, fact)
        if verdict and verdict.classification == "duplicate":
            # Skip duplicate — return existing node id
            logger.debug(
                "Consistency: duplicate fact skipped (matched=%s)",
                verdict.matched_node_id,
            )
            return verdict.matched_node_id or node_id
        if verdict and verdict.classification == "revision":
            # Revision: store new version, apply KG edges
            conf = max(conf, verdict.confidence)
            meta = {
                **meta,
                "revised_from": verdict.matched_node_id,
                "consistency": "revision",
            }
        elif verdict and verdict.classification == "conflict":
            # Conflict: store but tag it, apply KG contradiction edge
            tags = list(dict.fromkeys(tags + ["conflict_detected"]))
            meta = {
                **meta,
                "conflict_with": verdict.matched_node_id,
                "consistency": "conflict",
            }
        # Apply verdict to KG (creates edges)
        if verdict:
            apply_consistency_verdict(user_id, node_id, verdict)
    except Exception:
        logger.debug("Consistency check in add_fact failed", exc_info=True)

    upsert_node(
        node_id,
        user_id,
        "L2",
        fact,
        list(dict.fromkeys(tags + ["fact"])),
        meta,
        ts,
        imp,
        conf,
    )
    append_event(
        user_id, "memory.l2.add", {"id": node_id, "importance": imp, "confidence": conf}
    )
    _feed_knowledge_graph(node_id, "fact", fact, conf, tags, meta, user_id=user_id)
    from aihub.vector_engine import add_memory as _vector_add_memory

    vr = _vector_add_memory(fact, user_id=user_id)
    if not vr.get("ok"):
        err = str(vr.get("error") or "unknown")
        logger.error(
            "memory.l2.vector_write_failed user=%s node=%s error=%s",
            user_id,
            node_id,
            err,
        )
        raise MemoryVectorWriteError(
            f"vector_engine.add_memory failed for L2 fact: {err}"
        )
    logger.info(
        "memory.l2.vector_indexed user=%s node=%s total_vectors=%s",
        user_id,
        node_id,
        vr.get("total_vectors"),
    )
    _mirror_l2_fact_to_memory_v2(user_id, fact, tags, node_id, imp, conf)
    _enforce_caps(user_id)
    return node_id


def _enforce_caps(user_id: str) -> None:
    # Hard caps per user, delete oldest low-importance first
    rows = fetch_all(
        """
    SELECT id, layer, importance, confidence, ts
    FROM memory_nodes
    WHERE user_id=? AND deleted=0 AND layer IN ('L1','L2')
    ORDER BY layer ASC, importance ASC, confidence ASC, ts ASC
    """,
        (user_id,),
    )
    l1 = [r for r in rows if r["layer"] == "L1"]
    l2 = [r for r in rows if r["layer"] == "L2"]

    def _trim(lst, maxn):
        if len(lst) <= maxn:
            return 0
        extra = len(lst) - maxn
        for r in lst[:extra]:
            exec_one("UPDATE memory_nodes SET deleted=1 WHERE id=?", (r["id"],))
        return extra

    del1 = _trim(l1, EPISODES_MAX_PER_USER)
    del2 = _trim(l2, LTM_MAX_FACTS_PER_USER)
    if del1 or del2:
        append_event(user_id, "memory.prune", {"deleted_l1": del1, "deleted_l2": del2})


def process_turn(
    user_id: str, user_msg: str, assistant_msg: str, intent: str, meta: Dict[str, Any]
) -> Dict[str, Any]:
    from aihub.memory_errors import require_user_id
    from aihub.memory_core import get_memory_core

    user_id = require_user_id(user_id)
    _core = get_memory_core()

    remember_turn(user_id, user_msg, assistant_msg)
    stm_meta = {**(meta or {}), "intent": intent}
    stm_meta.setdefault("memory_scope", "user")
    u_id = _core.ingest_stm_message(user_id, "user", user_msg, stm_meta)
    a_id = _core.ingest_stm_message(
        user_id, "assistant", assistant_msg, stm_meta
    )

    summary = f"U:{user_msg[:4000]} || A:{assistant_msg[:4000]}"
    ep_meta = {
        **(meta or {}),
        "intent": intent,
        "stm_ids": [u_id, a_id],
    }
    ep_meta.setdefault("memory_scope", "user")
    ep_id = _core.ingest_episode(user_id, summary, ep_meta)

    fact_ids: List[str] = []

    # Psyche-based modulation: scoring + learning throttle
    pmod = _get_psyche_modulation(user_id)
    max_facts = pmod["max_facts"]

    # --- LearningEngine (regex-based, per-rule short facts) ---
    try:
        from aihub.learning_engine import _learning_engine as _le

        le_facts = _le.extract_facts_from_message(user_id, user_msg, "user")
        for fact_text, tags, _imp, _conf in le_facts:
            if len(fact_ids) >= max_facts:
                break
            fact_ids.append(
                _core.ingest_fact(
                    user_id,
                    fact_text,
                    tags=tags + [intent],
                    meta=_ingest_meta(
                        meta,
                        source="learning_engine",
                        source_episode=ep_id,
                    ),
                )
            )
    except Exception:  # noqa: BLE001
        logger.debug("LearningEngine extraction failed", exc_info=True)

    # --- Keyword fallback: only if LearningEngine found nothing ---
    if not fact_ids:
        t = user_msg.lower()
        explicit_fact_match = re.search(
            r"(?is)(?:zapamiętaj\s+ważny\s+fakt|zapamietaj\s+wazny\s+fakt|"
            r"zapamiętaj,\s*że|zapamietaj,\s*ze|zapamiętaj:\s*|zapamietaj:\s*|"
            r"zapamiętaj\s+to\s+|zapamietaj\s+to\s+|"
            r"(?:mój|moja)\s+\S+\s+to\s+|"
            r"(?:testowe)\s+\S+\s+to\s+|"
            r"(?:ulubiony|ulubiona)\s+\S+\s+to\s+"
            r")\s*(.+?)\s*$",
            user_msg.strip(),
        )
        if explicit_fact_match:
            fact_text = explicit_fact_match.group(1).strip().strip(".")
            if fact_text:
                # Krótka samotna wartość (np. „zielony”) → zapisz całe zdanie użytkownika.
                if len(fact_text) < 24 and fact_text.count(" ") == 0:
                    fact_text = user_msg.strip().rstrip(".")
                fact_ids.append(
                    _core.ingest_fact(
                        user_id,
                        fact_text,
                        tags=["fact", "explicit_memory", intent],
                        meta=_ingest_meta(
                            meta,
                            source="explicit_fact_fallback",
                            source_episode=ep_id,
                        ),
                    )
                )
        elif any(
            k in t
            for k in [
                "hasło projektu to",
                "haslo projektu to",
                "testowe hasło",
                "testowe haslo",
                "robocze hasło",
                "robocze haslo",
            ]
        ):
            fact_ids.append(
                _core.ingest_fact(
                    user_id,
                    user_msg.strip().rstrip("."),
                    tags=["fact", "project_fact", intent],
                    meta=_ingest_meta(
                        meta,
                        source="project_fact_fallback",
                        source_episode=ep_id,
                    ),
                )
            )
        elif any(
            k in t
            for k in [
                "lubię",
                "nie lubię",
                "preferuję",
                "wolę",
                "mój ulubiony",
                "moja ulubiona",
            ]
        ):
            fact_ids.append(
                _core.ingest_fact(
                    user_id,
                    user_msg.strip().rstrip("."),
                    tags=["user", "preference", intent],
                    meta=_ingest_meta(
                        meta,
                        source="preference_fact_fallback",
                        source_episode=ep_id,
                    ),
                )
            )
        elif any(
            k in t
            for k in [
                "lubię",
                "nie lubię",
                "preferuję",
                "zawsze",
                "nigdy",
                "ważne",
                "zakaz",
                "nakaz",
            ]
        ):
            fact_text = f"Użytkownik: {user_msg.strip()}"
            fact_ids.append(
                _core.ingest_fact(
                    user_id,
                    fact_text,
                    tags=["user", "preference", intent],
                    meta=_ingest_meta(
                        meta,
                        source="keyword_fallback",
                        source_episode=ep_id,
                    ),
                )
            )

    out = {
        "stm_ids": [u_id, a_id],
        "episode_id": ep_id,
        "fact_ids": fact_ids,
        "ts": now_ts(),
    }
    logger.info(
        "memory.ingest_turn",
        extra={
            "user_id": user_id,
            "intent": intent,
            "fact_count": len(fact_ids),
            "episode_id": ep_id,
        },
    )
    return out


def _vector_rerank(
    query: str, candidates: List[Dict[str, Any]], topk: int
) -> List[Tuple[str, float]]:
    # Build TF-IDF over candidates and query, then cosine.
    docs_tokens = [tokenize(c["content"]) for c in candidates]
    n_docs = len(docs_tokens)
    if n_docs == 0:
        return []
    df = build_df(docs_tokens)
    # Use effective min_df=1 for small candidate sets (< 3 docs) to prevent
    # prune_vocab from eliminating all terms when VEC_MIN_DF=2 global default
    # would zero out all vectors on small FTS result sets.
    if n_docs < 3:
        # Bypass prune_vocab for tiny sets – keep all terms in vocab
        doc_vecs = []
        for c, toks in zip(candidates, docs_tokens):
            doc_vecs.append((c["id"], tfidf_vector(toks, df, n_docs)))
        qv = tfidf_vector(tokenize(query), df, n_docs)
    else:
        df = prune_vocab(df, n_docs)
        doc_vecs = []
        for c, toks in zip(candidates, docs_tokens):
            doc_vecs.append((c["id"], tfidf_vector(toks, df, n_docs)))
        qv = tfidf_vector(tokenize(query), df, n_docs)
    return topk_cosine(qv, doc_vecs, k=min(topk, len(doc_vecs)))


def retrieve_context_v1(user_id: str, query: str, limit: int) -> Dict[str, Any]:
    """Legacy graph retrieval (STM + L1/L2 nodes + vector/KG boosts). Used by canonical core."""
    from aihub.memory_errors import require_user_id
    from aihub.memory_scoring import combined_memory_score, dynamic_vector_top_k

    user_id = require_user_id(user_id)
    stm = get_stm(user_id, limit=min(20, STM_MAX_MESSAGES))

    # L1 candidates (FTS/LIKE)
    l1 = search_nodes_fts(user_id, "L1", query, limit=min(200, limit * 20))
    # L2 candidates (FTS/LIKE)
    l2 = search_nodes_fts(user_id, "L2", query, limit=min(400, limit * 40))

    # Vector rerank to ensure semantic-ish retrieval even when FTS is noisy
    l1_scores = {doc_id: s for doc_id, s in _vector_rerank(query, l1, topk=limit)}
    l2_scores = {doc_id: s for doc_id, s in _vector_rerank(query, l2, topk=limit)}

    def _pack(
        items: List[Dict[str, Any]], scores: Dict[str, float]
    ) -> List[Dict[str, Any]]:
        out = []
        for it in items:
            sid = it["id"]
            score = float(scores.get(sid, 0.0))
            blended = combined_memory_score(
                retrieval_score=score,
                importance=float(it["importance"]),
                confidence=float(it["confidence"]),
                ts=float(it["ts"]),
                meta=it.get("meta"),
                layer=str(it.get("layer") or ""),
                query=query or "",
            )
            out.append(
                {
                    "id": sid,
                    "layer": it["layer"],
                    "content": it["content"],
                    "tags": it["tags"],
                    "meta": it["meta"],
                    "ts": it["ts"],
                    "score": float(blended),
                    "score_breakdown": "weighted_recency_freq_explicit_semantic",
                }
            )
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:limit]

    episodic = _pack(l1, l1_scores)
    semantic = _pack(l2, l2_scores)

    # Vector dense boost: optional FAISS semantic search to complement FTS
    dense_hits: List[Dict[str, Any]] = []
    try:
        from aihub.vector_engine import search as vector_search

        vr = vector_search(query, k=dynamic_vector_top_k(limit), user_id=user_id)
        emb_trace = vr.get("embedding_trace") if isinstance(vr, dict) else None
        if vr.get("ok") and vr.get("results"):
            for r in vr["results"]:
                if r.get("similarity", 0) > 0.3:
                    dense_hits.append(
                        {
                            "text": r["text"],
                            "similarity": r["similarity"],
                            "embedding_trace": emb_trace,
                            "dense_path_used": vr.get("dense_path_used"),
                        }
                    )
    except Exception:  # noqa: BLE001
        logger.debug("retrieve_context: vector dense boost unavailable", exc_info=True)

    # Knowledge Graph contextual hits
    graph_hits: List[Dict[str, Any]] = []
    try:
        from aihub.knowledge_graph import get_related_nodes as kg_get_related_nodes
        from aihub.knowledge_graph import query_nodes as kg_query_nodes

        seen_nodes = set()
        seed_nodes = kg_query_nodes(query, limit=min(limit, 10), user_id=user_id)
        for node in seed_nodes:
            if node.node_id not in seen_nodes:
                seen_nodes.add(node.node_id)
                graph_hits.append(
                    {
                        "node_id": node.node_id,
                        "type": node.node_type,
                        "content": node.content,
                        "confidence": node.confidence,
                    }
                )

            for rel in kg_get_related_nodes(node.node_id):
                if rel.node_id in seen_nodes:
                    continue
                seen_nodes.add(rel.node_id)
                graph_hits.append(
                    {
                        "node_id": rel.node_id,
                        "type": rel.node_type,
                        "content": rel.content,
                        "confidence": rel.confidence,
                    }
                )
                if len(graph_hits) >= limit:
                    break
            if len(graph_hits) >= limit:
                break
    except Exception:  # noqa: BLE001
        logger.debug("retrieve_context: KG hits unavailable", exc_info=True)

    # Touch meta_memory for returned nodes so access_count / freshness stay current
    try:
        from aihub.meta_memory import touch_nodes

        hit_ids = [item["id"] for item in episodic + semantic]
        if hit_ids:
            touch_nodes(hit_ids)
    except (OSError, ImportError):
        logger.debug("retrieve_context: touch_nodes failed", exc_info=True)

    append_event(
        user_id,
        "memory.retrieve",
        {
            "query": query,
            "limit": limit,
            "hits": {"l1": len(episodic), "l2": len(semantic)},
        },
    )

    return {
        "user_id": user_id,
        "query": query,
        "stm": stm,
        "episodic": episodic,
        "semantic": semantic,
        "dense_hits": dense_hits,
        "graph_hits": graph_hits,
        "total": len(episodic) + len(semantic),
        "retrieval_priority_order": [
            "L2_semantic",
            "vector_dense",
            "L1_episodic",
            "stm",
            "knowledge_graph",
        ],
    }


def retrieve_context(user_id: str, query: str, limit: int) -> Dict[str, Any]:
    """Canonical unified memory retrieval (L1/L2/STM + Memory V2 ranked items)."""
    from aihub.memory_core import get_memory_core

    return get_memory_core().retrieve_unified(user_id, query, limit)


def health(user_id: str) -> Dict[str, Any]:
    stm = fetch_all(
        "SELECT COUNT(*) AS c FROM stm_messages WHERE user_id=?", (user_id,)
    )
    l1 = fetch_all(
        "SELECT COUNT(*) AS c FROM memory_nodes WHERE user_id=? AND layer='L1' AND deleted=0",
        (user_id,),
    )
    l2 = fetch_all(
        "SELECT COUNT(*) AS c FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
        (user_id,),
    )
    return {
        "user_id": user_id,
        "stm_messages": int(stm[0]["c"]) if stm else 0,
        "episodic_nodes": int(l1[0]["c"]) if l1 else 0,
        "semantic_nodes": int(l2[0]["c"]) if l2 else 0,
        "ts": now_ts(),
    }
