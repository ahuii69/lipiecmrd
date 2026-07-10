#!/usr/bin/env python3
"""Warm and verify the real embedding/vector stack.

This script is intentionally strict.  It loads the configured embedding backend,
verifies FAISS add/search, and exits non-zero if the runtime would have to fake
vector memory.  It prints only operational status, never secrets.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

try:
    from scripts.dotenv_tool import parse_dotenv
except Exception:  # pragma: no cover
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.dotenv_tool import parse_dotenv  # type: ignore


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--env", default=".env")
    ap.add_argument("--profile", choices=["", "local", "prod"], default="")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = repo / env_path
    env = parse_dotenv(env_path)
    if args.profile == "local":
        env["DB_BACKEND"] = "sqlite"
        # Local storage profile keeps the real embedding provider from .env.
        # No implicit SentenceTransformers/HF fallback and no fake vectors.
        env["AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK"] = "0"
        env["AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK"] = "0"
        env["AIHUB_DISABLE_REMOTE_EMBEDDINGS"] = env.get("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "0")
    elif args.profile == "prod":
        env["AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK"] = env.get("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK") or "0"
        env["AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK"] = env.get("AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK") or "0"

    os.environ.update(env)
    sys.path.insert(0, str(repo))

    import faiss  # type: ignore
    import numpy as np

    idx = faiss.IndexFlatL2(4)
    idx.add(np.zeros((1, 4), dtype=np.float32))
    dist, ids = idx.search(np.zeros((1, 4), dtype=np.float32), 1)
    if ids.shape != (1, 1) or int(ids[0][0]) != 0:
        raise RuntimeError("FAISS probe failed")
    print("[OK] faiss add/search")

    from aihub.embedding_engine import embed_document, get_faiss_embedding_dimension
    from aihub import vector_engine

    r = embed_document("AIHub embedding warmup probe")
    if not r.vector:
        raise RuntimeError("embedding provider returned empty vector")
    if r.provider == "deterministic-hash":
        raise RuntimeError("deterministic-hash embedding is not allowed for full runtime")
    print(f"[OK] embedding provider={r.provider} model={r.model} dim={len(r.vector)}")

    dim = get_faiss_embedding_dimension()
    if dim <= 0:
        raise RuntimeError("invalid FAISS embedding dimension")
    print(f"[OK] embedding dimension={dim}")

    vector_engine.clear()
    add = vector_engine.add_memory("AIHub FAISS full runtime probe", user_id="doctor", source="warmup")
    if not add.get("ok"):
        raise RuntimeError(f"vector add failed: {add}")
    sr = vector_engine.search("FAISS runtime probe", user_id="doctor", k=1)
    if not sr.get("ok") or sr.get("backend") != "faiss":
        raise RuntimeError(f"vector search failed/not FAISS: {sr}")
    print("[OK] vector_engine backend=faiss add/search")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
