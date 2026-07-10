#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic: ACTIVE embedding stack (embedding_engine + one vector search).

Loads ``.env`` via ``aihub.config``. Run from repo root::

    python -m aihub.scripts.embedding_runtime_probe

Prints healthcheck, one ``embed_query``, optional ``vector_engine.search`` (dense path).
"""

from __future__ import annotations

import argparse
import json
import sys

import aihub.config  # noqa: F401  # pylint: disable=unused-import
from aihub import embedding_engine as ee


def main() -> None:
    parser = argparse.ArgumentParser(description="ACTIVE embedding stack runtime probe")
    parser.add_argument(
        "--no-search",
        action="store_true",
        help="Skip vector_engine.search (only healthcheck + embed_query)",
    )
    args = parser.parse_args()

    h = ee.healthcheck()
    print("=== healthcheck ===")
    print(json.dumps(h, indent=2))

    try:
        r = ee.embed_query("[embedding_runtime_probe] live embed")
    except ee.EmbeddingError as exc:
        print("=== embed_query FAILED ===", exc, file=sys.stderr)
        sys.exit(1)

    print("=== embed_query ===")
    print(
        json.dumps(
            {
                "provider": r.provider,
                "model": r.model,
                "output_dimension": r.output_dimension,
                "embedding_fallback_used": r.embedding_fallback_used,
                "primary_provider_attempted": r.primary_provider_attempted,
                "configured_provider": r.configured_provider,
                "configured_model_env": r.configured_model_env,
            },
            indent=2,
        )
    )

    if args.no_search:
        print("=== vector_engine.search === skipped (--no-search)")
        return

    try:
        from aihub.vector_engine import search as vector_search
    except ImportError:
        print("=== vector search: skipped (import error) ===")
    else:
        vr = vector_search("[embedding_runtime_probe] dense", k=3, user_id="")
        print("=== vector_engine.search ===")
        print(
            json.dumps(
                {
                    "ok": vr.get("ok"),
                    "error": vr.get("error"),
                    "results_count": len(vr.get("results") or []),
                    "dense_path_used": vr.get("dense_path_used"),
                    "embedding_trace": vr.get("embedding_trace"),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
