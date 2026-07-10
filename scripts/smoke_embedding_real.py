#!/usr/bin/env python3
"""Real embedding + FAISS smoke.

This is a runtime proof, not a static audit.  It uses the actual .env provider
(Voyage or configured SentenceTransformers), writes a vector to FAISS, searches
it back, and refuses deterministic/numpy fallbacks.
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


def _apply_profile(env: dict[str, str], profile: str) -> None:
    if profile == "local":
        env["DB_BACKEND"] = "sqlite"
        env.setdefault("DB_PATH", "data/aihub.sqlite3")
        # Do not override EMBEDDING_PROVIDER.  Local storage still uses real embeddings.
    elif profile == "prod":
        env["ENV"] = env.get("ENV") or "production"
    elif profile:
        raise SystemExit(f"Unsupported profile: {profile}")
    env["AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK"] = "0"
    env["AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK"] = "0"
    env["AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK"] = env.get("AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK", "0")


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
    _apply_profile(env, args.profile)
    os.environ.update(env)
    sys.path.insert(0, str(repo))

    import faiss  # type: ignore  # noqa: F401
    from aihub.embedding_engine import embed_document, embed_query
    from aihub import vector_engine

    doc = embed_document("Mordo wymaga realnej zaawansowanej pamięci semantycznej bez udawania.")
    query = embed_query("Jakie wymagania są wobec pamięci AI?")
    if not doc.vector or not query.vector:
        raise RuntimeError("embedding provider returned empty vector")
    if doc.provider == "deterministic-hash" or query.provider == "deterministic-hash":
        raise RuntimeError("deterministic embedding fallback is forbidden in real smoke")
    if doc.embedding_fallback_used and env.get("AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK", "0") != "1":
        raise RuntimeError("provider fallback was used although it is disabled")
    if len(doc.vector) != len(query.vector):
        raise RuntimeError(f"embedding dimension mismatch: document={len(doc.vector)} query={len(query.vector)}")

    vector_engine.clear()
    add = vector_engine.add_memory(
        "Mordo wymaga realnej zaawansowanej pamięci semantycznej bez udawania.",
        user_id="smoke-real-embedding",
        source="smoke_embedding_real",
        external_id="smoke-real-embedding-1",
    )
    if not add.get("ok"):
        raise RuntimeError(f"vector add failed: {add}")
    search = vector_engine.search("realna pamięć semantyczna wymagania", user_id="smoke-real-embedding", k=3)
    if not search.get("ok"):
        raise RuntimeError(f"vector search failed: {search}")
    if search.get("backend") != "faiss":
        raise RuntimeError(f"vector backend is not FAISS: {search.get('backend')}")
    results = search.get("results") or []
    if not results:
        raise RuntimeError(f"vector search returned no results: {search}")

    print(f"[OK] embedding provider={doc.provider} model={doc.model} dim={len(doc.vector)}")
    print("[OK] vector_engine backend=faiss add/search")
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
