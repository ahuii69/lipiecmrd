#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical vector engine.

Production/runtime requires FAISS.  A NumPy backend exists only as an explicit,
opt-in diagnostic fallback (``AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK=1``) and is never
used silently.  If FAISS is missing, startup/doctor must fail instead of
pretending that advanced vector memory is fully available.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class FAISSDimensionMismatchError(RuntimeError):
    """On-disk FAISS index dimension does not match runtime embedding output."""


VECTOR_INDEX_PATH = Path(os.getenv("VECTOR_INDEX_PATH", "./data/vector.index"))
VECTOR_META_PATH = Path(os.getenv("VECTOR_META_PATH", "./data/vector_meta.json"))
VECTOR_NUMPY_PATH = Path(os.getenv("VECTOR_NUMPY_PATH", "./data/vector_numpy.npy"))

_index: Any = None
_meta: Optional[List[Dict[str, Any]]] = None
_effective_dim: Optional[int] = None
_backend: Optional[str] = None


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _allow_numpy_vector_fallback() -> bool:
    return _env_flag("AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK", "0")


def _allow_deterministic_embedding_fallback() -> bool:
    return _env_flag("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "0")

class _NumpyIndex:
    def __init__(self, dim: int, vectors: Optional[np.ndarray] = None) -> None:
        self.d = int(dim)
        if vectors is None:
            self.vectors = np.empty((0, self.d), dtype=np.float32)
        else:
            arr = np.asarray(vectors, dtype=np.float32)
            if arr.ndim != 2 or arr.shape[1] != self.d:
                raise FAISSDimensionMismatchError(
                    f"NumPy vector store dimension {arr.shape if arr.ndim else arr.ndim} != {self.d}"
                )
            self.vectors = arr

    @property
    def ntotal(self) -> int:
        return int(self.vectors.shape[0])

    def add(self, arr: np.ndarray) -> None:
        rows = np.asarray(arr, dtype=np.float32)
        if rows.ndim != 2 or rows.shape[1] != self.d:
            raise FAISSDimensionMismatchError(
                f"added vectors shape={rows.shape} expected second dim={self.d}"
            )
        self.vectors = np.vstack([self.vectors, rows])

    def search(self, arr: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        q = np.asarray(arr, dtype=np.float32)
        if q.ndim != 2 or q.shape[1] != self.d:
            raise FAISSDimensionMismatchError(
                f"query shape={q.shape} expected second dim={self.d}"
            )
        if self.ntotal == 0 or k <= 0:
            return np.empty((q.shape[0], 0), dtype=np.float32), np.empty((q.shape[0], 0), dtype=np.int64)
        k = min(int(k), self.ntotal)
        # L2 distance, same semantics as IndexFlatL2.
        diff = self.vectors[None, :, :] - q[:, None, :]
        distances = np.sum(diff * diff, axis=2)
        order = np.argsort(distances, axis=1)[:, :k]
        sorted_distances = np.take_along_axis(distances, order, axis=1).astype(np.float32)
        return sorted_distances, order.astype(np.int64)


def _get_effective_dim() -> int:
    global _effective_dim
    if _effective_dim is not None:
        return _effective_dim
    try:
        from aihub.embedding_engine import get_faiss_embedding_dimension

        _effective_dim = int(get_faiss_embedding_dimension())
    except Exception as exc:  # noqa: BLE001
        if not _allow_deterministic_embedding_fallback():
            raise RuntimeError(
                "embedding dimension probe failed and deterministic fallback is disabled; "
                "install/configure Voyage or sentence-transformers"
            ) from exc
        logger.warning("Embedding dimension probe failed; using explicit deterministic fallback dimension: %s", exc)
        from aihub.embedding_engine import _call_deterministic_embedding

        _effective_dim = int(len(_call_deterministic_embedding("[vector_dimension_probe]")))
    return _effective_dim


def _encode_text(text: str, input_type: str = "document") -> tuple[np.ndarray | None, int, Dict[str, Any]]:
    trace: Dict[str, Any] = {}
    try:
        from aihub.embedding_engine import embed_document, embed_query

        resp = embed_query(text) if input_type == "query" else embed_document(text)
        vector = np.asarray(resp.vector, dtype=np.float32)
        trace = {
            "embedding_stack_used": "active",
            "embedding_provider": resp.provider,
            "embedding_model": resp.model,
            "embedding_dimension": len(resp.vector),
            "embedding_runtime_dim": resp.embedding_runtime_dim,
            "embedding_fallback_used": resp.embedding_fallback_used,
            "primary_provider_attempted": resp.primary_provider_attempted,
            "embedding_primary_provider_attempted": resp.embedding_primary_provider_attempted,
            "embedding_primary_provider_used": resp.embedding_primary_provider_used,
            "configured_provider": resp.configured_provider,
            "configured_model_env": resp.configured_model_env,
        }
        return vector, int(vector.size), trace
    except Exception as exc:  # noqa: BLE001
        if not _allow_deterministic_embedding_fallback():
            logger.error("Encoding provider failed and deterministic fallback is disabled: %s", exc, exc_info=True)
            return None, 0, {"error": str(exc), "fallback_disabled": True}
        logger.warning("Encoding provider failed; using explicit deterministic fallback: %s", exc)
        try:
            from aihub.embedding_engine import _call_deterministic_embedding

            vector = np.asarray(_call_deterministic_embedding(text), dtype=np.float32)
            return vector, int(vector.size), {
                "embedding_stack_used": "active",
                "embedding_provider": "deterministic-hash",
                "embedding_model": "deterministic-hash",
                "embedding_dimension": int(vector.size),
                "embedding_runtime_dim": int(vector.size),
                "embedding_fallback_used": True,
                "primary_provider_attempted": "vector_engine_explicit_diagnostic_fallback",
            }
        except Exception as fallback_exc:  # noqa: BLE001
            logger.error("Encoding fallback failed: %s", fallback_exc, exc_info=True)
            return None, 0, {"error": str(exc), "fallback_error": str(fallback_exc)}


def _load_meta() -> List[Dict[str, Any]]:
    global _meta
    if _meta is not None:
        return _meta
    if VECTOR_META_PATH.exists():
        try:
            raw = json.loads(VECTOR_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Vector metadata corrupt; starting empty", exc_info=True)
            raw = []
    else:
        raw = []
    meta: List[Dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            meta.append({"text": item, "user_id": "", "ts": 0.0, "source": "legacy", "external_id": None, "metadata": {}})
        elif isinstance(item, dict):
            item.setdefault("text", "")
            item.setdefault("user_id", "")
            item.setdefault("ts", 0.0)
            item.setdefault("source", "")
            item.setdefault("external_id", None)
            item.setdefault("metadata", {})
            meta.append(item)
    _meta = meta
    return _meta


def _init_index() -> Any:
    global _index, _backend
    if _index is not None:
        return _index
    dim = _get_effective_dim()
    try:
        import faiss  # type: ignore

        if VECTOR_INDEX_PATH.exists():
            loaded = faiss.read_index(str(VECTOR_INDEX_PATH))
            if int(loaded.d) != dim:
                raise FAISSDimensionMismatchError(
                    f"FAISS index dimension {loaded.d} != runtime embedding dimension {dim}"
                )
            _index = loaded
        else:
            _index = faiss.IndexFlatL2(dim)
        _backend = "faiss"
        return _index
    except FAISSDimensionMismatchError:
        raise
    except Exception as exc:  # noqa: BLE001
        if not _allow_numpy_vector_fallback():
            raise RuntimeError(
                "FAISS is required for vector memory but is unavailable. "
                "Install faiss-cpu from requirements.txt or set "
                "AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK=1 only for explicit diagnostics."
            ) from exc
        logger.warning("FAISS unavailable; using explicit NumPy diagnostic vector backend: %s", exc)
        vectors = None
        if VECTOR_NUMPY_PATH.exists():
            try:
                vectors = np.load(VECTOR_NUMPY_PATH)
            except Exception:
                logger.warning("NumPy vector store corrupt; starting empty", exc_info=True)
                vectors = None
        _index = _NumpyIndex(dim, vectors=vectors)
        _backend = "numpy-diagnostic"
        return _index


def _save() -> None:
    VECTOR_META_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _meta is not None:
        VECTOR_META_PATH.write_text(json.dumps(_meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    if _index is None:
        return
    if _backend == "faiss":
        try:
            import faiss  # type: ignore

            VECTOR_INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
            faiss.write_index(_index, str(VECTOR_INDEX_PATH))
        except Exception:
            logger.error("Failed to save FAISS index", exc_info=True)
            raise
    elif _backend == "numpy":
        VECTOR_NUMPY_PATH.parent.mkdir(parents=True, exist_ok=True)
        np.save(VECTOR_NUMPY_PATH, _index.vectors)


def add_memory(
    text: str,
    user_id: str = "",
    *,
    external_id: str | None = None,
    source: str = "memory",
    metadata: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    if not text or not text.strip():
        return {"ok": False, "error": "empty text"}
    try:
        index = _init_index()
        meta = _load_meta()
        embedding, actual_dim, enc_trace = _encode_text(text, input_type="document")
        if embedding is None:
            return {"ok": False, "error": "encoding_failed", "embedding_trace": enc_trace}
        if hasattr(index, "d") and int(index.d) != actual_dim:
            return {"ok": False, "error": f"dimension_mismatch: embedding={actual_dim} index={index.d}", "embedding_trace": enc_trace}
        index.add(np.asarray([embedding], dtype=np.float32))
        vector_id = int(index.ntotal) - 1
        meta.append(
            {
                "text": text,
                "user_id": user_id or "",
                "ts": time.time(),
                "source": source,
                "external_id": external_id,
                "metadata": metadata or {},
            }
        )
        _save()
        return {
            "ok": True,
            "vector_id": vector_id,
            "external_id": external_id,
            "source": source,
            "text_length": len(text),
            "total_vectors": int(index.ntotal),
            "faiss_index_dimension": int(index.d) if hasattr(index, "d") else None,
            "backend": _backend,
            "embedding_trace": enc_trace,
            "embedding_stack_used": "active",
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Error adding memory: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


def search(query: str, k: int = 5, user_id: str = "") -> Dict[str, Any]:
    if not query or not query.strip():
        return {"ok": False, "results": [], "error": "empty query"}
    try:
        index = _init_index()
        meta = _load_meta()
        if int(index.ntotal) == 0:
            return {"ok": True, "results": [], "query_length": len(query), "backend": _backend}
        query_embedding, query_dim, enc_trace = _encode_text(query, input_type="query")
        if query_embedding is None:
            return {"ok": False, "results": [], "error": "encoding_failed", "embedding_trace": enc_trace}
        if hasattr(index, "d") and int(index.d) != query_dim:
            return {"ok": False, "results": [], "error": f"dimension_mismatch: query={query_dim} index={index.d}", "embedding_trace": enc_trace}
        k_fetch = min(max(1, int(k)) * 5, int(index.ntotal))
        distances, indices = index.search(np.asarray([query_embedding], dtype=np.float32), k_fetch)
        results: list[dict[str, Any]] = []
        for idx, distance in zip(indices[0], distances[0]):
            idx_int = int(idx)
            if idx_int < 0 or idx_int >= len(meta):
                continue
            entry = meta[idx_int]
            entry_uid = str(entry.get("user_id", ""))
            if user_id and entry_uid != user_id:
                continue
            dist = float(distance)
            results.append(
                {
                    "index": idx_int,
                    "text": str(entry.get("text", "")),
                    "distance": dist,
                    "similarity": 1.0 / (1.0 + max(0.0, dist)),
                    "user_id": entry_uid,
                    "source": entry.get("source", ""),
                    "external_id": entry.get("external_id"),
                    "metadata": entry.get("metadata", {}),
                    "ts": entry.get("ts", 0.0),
                }
            )
            if len(results) >= int(k):
                break
        return {
            "ok": True,
            "query_length": len(query),
            "results": results,
            "total_vectors": int(index.ntotal),
            "faiss_index_dimension": int(index.d) if hasattr(index, "d") else None,
            "backend": _backend,
            "embedding_trace": enc_trace,
            "dense_path_used": True,
            "embedding_stack_used": "active",
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Error in vector search: %s", exc, exc_info=True)
        return {"ok": False, "results": [], "error": str(exc)}


def health() -> Dict[str, Any]:
    try:
        index = _init_index()
        meta = _load_meta()
        from aihub.embedding_engine import EMBEDDING_MODEL, EMBEDDING_PROVIDER

        return {
            "ok": True,
            "vector_operations_available": True,
            "backend": _backend,
            "total_vectors": int(index.ntotal),
            "metadata_items": len(meta),
            "embedding_provider": EMBEDDING_PROVIDER,
            "embedding_model": EMBEDDING_MODEL,
            "dimension": int(index.d) if hasattr(index, "d") else _get_effective_dim(),
            "index_path": str(VECTOR_INDEX_PATH),
            "meta_path": str(VECTOR_META_PATH),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("Error getting vector engine health: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}


def clear() -> Dict[str, Any]:
    global _index, _meta, _effective_dim, _backend
    try:
        from aihub.embedding_engine import clear_faiss_dimension_probe_cache

        clear_faiss_dimension_probe_cache()
        _effective_dim = None
        dim = _get_effective_dim()
        try:
            import faiss  # type: ignore

            _index = faiss.IndexFlatL2(dim)
            _backend = "faiss"
        except Exception as exc:  # noqa: BLE001
            if not _allow_numpy_vector_fallback():
                raise RuntimeError("FAISS required to clear/recreate vector index") from exc
            _index = _NumpyIndex(dim)
            _backend = "numpy-diagnostic"
        _meta = []
        for path in (VECTOR_INDEX_PATH, VECTOR_META_PATH, VECTOR_NUMPY_PATH):
            try:
                path.unlink(missing_ok=True)
            except Exception as exc:
                logger.debug("Vector index cleanup skipped for %s: %s", path, exc)
        _save()
        return {"ok": True, "message": "Vector index cleared", "backend": _backend}
    except Exception as exc:  # noqa: BLE001
        logger.error("Error clearing vector index: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)}
