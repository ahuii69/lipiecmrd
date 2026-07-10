#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Łączenie sygnałów rankingu pamięci (FTS / TF-IDF) z metadanymi węzła i świeżością."""

from __future__ import annotations

import math
import time
from typing import Any


def dynamic_vector_top_k(limit: int) -> int:
    """Szerokość zapytania wektorowego względem limitu wyników użytkownika."""
    lim = max(1, int(limit))
    return max(8, min(120, lim * 8))


def combined_memory_score(
    *,
    retrieval_score: float,
    importance: float,
    confidence: float,
    ts: float,
    meta: Any,
    layer: str,
    query: str,
) -> float:
    """Scala retrieval + importance + confidence + recency (+ lekki bias warstwy)."""
    _ = meta, query  # rezerwa pod przyszłe sygnały (np. tagi w meta)
    now = time.time()
    age_s = max(0.0, now - float(ts))
    recency = 1.0 / (1.0 + math.log1p(age_s / 3600.0))
    imp = max(0.0, min(1.0, float(importance)))
    conf = max(0.0, min(1.0, float(confidence)))
    rs = max(0.0, min(1.0, float(retrieval_score)))
    layer_boost = 0.05 if (layer or "").upper() == "L2" else 0.0
    blended = (
        0.52 * rs + 0.18 * imp + 0.12 * conf + 0.13 * recency + layer_boost
    )
    return float(min(1.0, max(0.0, blended)))
