#!/usr/bin/env python3
"""Bootstrap the configured local SentenceTransformers model.

Use only when EMBEDDING_PROVIDER=sentence-transformers or when explicit provider
fallback to ST is enabled. Voyage-only production does not need this. The script
fails loudly if the model cannot be loaded/downloaded.
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
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    env_path = Path(args.env)
    if not env_path.is_absolute():
        env_path = repo / env_path
    env = parse_dotenv(env_path)
    os.environ.update(env)
    sys.path.insert(0, str(repo))

    provider = (env.get("EMBEDDING_PROVIDER") or "voyage").strip().lower()
    configured = (env.get("EMBEDDING_MODEL") or "").strip()
    if args.model:
        model_name = args.model
    elif provider == "sentence-transformers":
        model_name = configured or "all-MiniLM-L6-v2"
    elif (env.get("AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK") or "0") == "1":
        model_name = "all-MiniLM-L6-v2" if configured == "voyage-4-large" else (configured or "all-MiniLM-L6-v2")
    else:
        print("[OK] Voyage-only config; no local SentenceTransformers bootstrap needed")
        return 0

    from sentence_transformers import SentenceTransformer

    print(f"[INFO] Loading/downloading SentenceTransformers model: {model_name}")
    model = SentenceTransformer(model_name)
    emb = model.encode(["AIHub embedding bootstrap probe"], convert_to_numpy=True, show_progress_bar=False)
    if emb is None or len(emb) != 1 or len(emb[0]) <= 0:
        raise RuntimeError("SentenceTransformers model returned invalid embedding")
    print(f"[OK] model={model_name} dim={len(emb[0])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
