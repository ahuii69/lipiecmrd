"""Compatibility embedding adapter backed by the canonical embedding engine.

Legacy memory modules expect NumPy arrays from ``embed_texts``/``embed_one``.
This adapter delegates to ``aihub.embedding_engine`` and fails loudly when the
configured embedding stack is not available.  It does not synthesize fake
vectors, because memory quality depends on real semantic embeddings.
"""

from __future__ import annotations

from typing import List

import numpy as np

from aihub.embedding_engine import embed_document

EMBEDDING_STACK = "CANONICAL_EMBEDDING_ENGINE_ADAPTER"


def embed_texts(texts: List[str]) -> np.ndarray:
    vectors: list[list[float]] = []
    for text in texts:
        resp = embed_document(str(text or " "))
        vectors.append(resp.vector)
    if not vectors:
        return np.empty((0, 0), dtype=np.float32)
    return np.asarray(vectors, dtype=np.float32)


def embed_one(text: str) -> np.ndarray:
    arr = embed_texts([text])
    if arr.size == 0:
        raise RuntimeError("embedding provider returned no vector")
    return arr[0]
