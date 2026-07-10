#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Tuple

from aihub.config import VEC_MAX_DF, VEC_MAX_TOKENS_PER_DOC, VEC_MAX_VOCAB, VEC_MIN_DF

_TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÿ_]+", re.UNICODE)


def tokenize(text: str) -> List[str]:
    text = text.lower()
    toks = _TOKEN_RE.findall(text)
    if len(toks) > VEC_MAX_TOKENS_PER_DOC:
        toks = toks[:VEC_MAX_TOKENS_PER_DOC]
    return toks


def build_df(docs: Iterable[List[str]]) -> Dict[str, int]:
    df = defaultdict(int)
    for toks in docs:
        seen = set(toks)
        for t in seen:
            df[t] += 1
    return dict(df)


def prune_vocab(df: Dict[str, int], n_docs: int) -> Dict[str, int]:
    # Remove too-rare and too-common terms, then cap vocab size by df.
    items = []
    max_df_abs = int(math.floor(VEC_MAX_DF * n_docs)) if n_docs > 0 else 0
    for term, c in df.items():
        if c < VEC_MIN_DF:
            continue
        if max_df_abs > 0 and c > max_df_abs:
            continue
        items.append((term, c))
    items.sort(key=lambda x: x[1], reverse=True)
    if len(items) > VEC_MAX_VOCAB:
        items = items[:VEC_MAX_VOCAB]
    return dict(items)


def tfidf_vector(
    tokens: List[str], df: Dict[str, int], n_docs: int
) -> Dict[str, float]:
    tf = Counter(tokens)
    vec: Dict[str, float] = {}
    for term, f in tf.items():
        if term not in df:
            continue
        # sublinear tf
        tf_w = 1.0 + math.log(1.0 + f)
        idf = math.log((n_docs + 1.0) / (df[term] + 1.0)) + 1.0
        vec[term] = tf_w * idf
    # L2 norm
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    for k in list(vec.keys()):
        vec[k] /= norm
    return vec


def cosine_sparse(a: Dict[str, float], b: Dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    # iterate smaller dict
    if len(a) > len(b):
        a, b = b, a
    s = 0.0
    for k, av in a.items():
        bv = b.get(k)
        if bv is not None:
            s += av * bv
    return float(s)


def topk_cosine(
    query_vec: Dict[str, float], doc_vecs: List[Tuple[str, Dict[str, float]]], k: int
) -> List[Tuple[str, float]]:
    scored = []
    for doc_id, dv in doc_vecs:
        scored.append((doc_id, cosine_sparse(query_vec, dv)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:k]
