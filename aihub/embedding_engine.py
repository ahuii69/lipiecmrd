#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ACTIVE_CONFIRMED

To jest JEDYNY kanoniczny silnik embeddingów.

Wszystkie nowe funkcje MUSZĄ używać tego modułu.

Legacy:
- aihub/memory/embedder.py → NIE używać

Szczegóły techniczne (Voyage + opcjonalny fallback ST, FAISS / vector_engine):

- Primary: Voyage API, model from ``EMBEDDING_MODEL`` env (default ``voyage-4-large``).
- Config: ``EMBEDDING_PROVIDER=voyage`` (recommended production) or ``auto``.
- ``auto``: if ``VOYAGE_API_KEY`` is set, **attempts Voyage first**; otherwise local ST only.
- Provider fallback is **strict opt-in**.  If ``EMBEDDING_PROVIDER=voyage`` and
  Voyage fails, startup/runtime raises unless ``AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK=1``.
  When explicitly enabled, SentenceTransformers is used with ``all-MiniLM-L6-v2`` for
  Voyage-sized configs and the response is flagged as fallback.

Semantics: embed_query / embed_document — Voyage uses matching ``input_type``; ST ignores it.

Konfiguracja: :mod:`aihub.config` (``EMBEDDING_MODEL``, ``VOYAGE_API_KEY``, ``EMBEDDING_*``).
Osobny stos fastembed / ``AIHUB_EMBED_MODEL``: wyłącznie ``aihub.memory.embedder`` (legacy).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional

from aihub import config as _cfg

logger = logging.getLogger(__name__)

VOYAGE_API_KEY = _cfg.VOYAGE_API_KEY
EMBEDDING_PROVIDER = _cfg.EMBEDDING_PROVIDER
EMBEDDING_MODEL = _cfg.EMBEDDING_MODEL
EMBEDDING_OUTPUT_DIM = _cfg.EMBEDDING_OUTPUT_DIM
EMBEDDING_OUTPUT_DTYPE = _cfg.EMBEDDING_OUTPUT_DTYPE
EMBEDDING_TIMEOUT_SECONDS = _cfg.EMBEDDING_TIMEOUT_SECONDS
EMBEDDING_MAX_RETRIES = _cfg.EMBEDDING_MAX_RETRIES
EMBEDDING_HEALTHCHECK_LIVE_PROBE = _cfg.EMBEDDING_HEALTHCHECK_LIVE_PROBE

# Explicit, documented fallback model when primary is Voyage-sized config.
ST_FALLBACK_MODEL = "all-MiniLM-L6-v2"

# Known SentenceTransformers output sizes for honest healthcheck when ST is the real path.
_KNOWN_ST_OUTPUT_DIMS: Dict[str, int] = {
    "all-MiniLM-L6-v2": 384,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
}

# Lazy-loaded providers
_sentence_transformer: Optional[Any] = None
_sentence_transformers_init_failed: bool = False

# Cached result of one live embed used for FAISS dimension (honest runtime dim).
_FAISS_PROBE_DIM: int | None = None


@dataclass
class EmbeddingResponse:
    """Canonical embedding result contract (ACTIVE stack)."""

    vector: List[float]
    provider: str
    model: str
    input_type: Literal["query", "document"]
    output_dimension: int
    output_dtype: str
    content_hash: str
    ts: float
    embedding_fallback_used: bool = False
    """True iff Voyage was attempted first and SentenceTransformers produced the vector."""
    primary_provider_attempted: str = ""
    """``voyage`` if Voyage was tried first; ``none`` if skipped (e.g. auto without key); ``sentence-transformers`` if ST-only mode."""
    configured_provider: str = ""
    configured_model_env: str = ""
    embedding_primary_provider_attempted: str = ""
    """Same as ``primary_provider_attempted`` (explicit trace name)."""
    embedding_primary_provider_used: str = ""
    """Provider that produced ``vector`` (same as ``provider``)."""
    embedding_runtime_dim: int = 0
    """``len(vector)`` at embed time (single-truth dimension)."""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "vector": self.vector,
            "provider": self.provider,
            "model": self.model,
            "input_type": self.input_type,
            "output_dimension": self.output_dimension,
            "output_dtype": self.output_dtype,
            "content_hash": self.content_hash,
            "ts": self.ts,
            "embedding_fallback_used": self.embedding_fallback_used,
            "primary_provider_attempted": self.primary_provider_attempted,
            "configured_provider": self.configured_provider,
            "configured_model_env": self.configured_model_env,
            "embedding_primary_provider_attempted": self.embedding_primary_provider_attempted,
            "embedding_primary_provider_used": self.embedding_primary_provider_used,
            "embedding_runtime_dim": self.embedding_runtime_dim,
        }


if "EmbeddingError" not in globals():
    class EmbeddingError(Exception):
        """Base exception for embedding operations."""


if "EmbeddingProviderError" not in globals():
    class EmbeddingProviderError(EmbeddingError):
        """Provider-level error: auth, timeout, bad response."""


if "EmbeddingTimeoutError" not in globals():
    class EmbeddingTimeoutError(EmbeddingError):
        """Explicit timeout during embedding generation."""


def _st_model_name_for_fallback() -> str:
    if EMBEDDING_MODEL and EMBEDDING_MODEL != "voyage-4-large":
        return EMBEDDING_MODEL
    return ST_FALLBACK_MODEL


def _known_st_output_dimension_for_fallback_model() -> Optional[int]:
    """Best-effort ST vector length for :func:`_st_model_name_for_fallback` (healthcheck only)."""
    name = (_st_model_name_for_fallback() or "").strip()
    if not name:
        return None
    if name in _KNOWN_ST_OUTPUT_DIMS:
        return _KNOWN_ST_OUTPUT_DIMS[name]
    tail = name.split("/")[-1]
    if tail in _KNOWN_ST_OUTPUT_DIMS:
        return _KNOWN_ST_OUTPUT_DIMS[tail]
    return None


def _remote_embeddings_disabled() -> bool:
    """True when tests/local profile must not call external embedding providers."""
    return os.getenv("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "0").strip().lower() in {"1", "true", "yes", "on"}


def _deterministic_fallback_enabled() -> bool:
    return os.getenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}


def _provider_fallback_enabled() -> bool:
    """Allow Voyage→SentenceTransformers fallback only when explicitly requested."""
    return os.getenv("AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK", "0").strip().lower() in {"1", "true", "yes", "on"}


def _should_attempt_voyage_first() -> bool:
    if _remote_embeddings_disabled():
        return False
    if EMBEDDING_PROVIDER == "voyage":
        return True
    if EMBEDDING_PROVIDER == "auto":
        return bool(VOYAGE_API_KEY and VOYAGE_API_KEY.strip())
    return False


def _init_sentence_transformer() -> Optional[Any]:
    """Initialize SentenceTransformer (explicit fallback or ST-only mode)."""
    global _sentence_transformer, _sentence_transformers_init_failed
    if _sentence_transformer is not None:
        return _sentence_transformer

    if _sentence_transformers_init_failed:
        return None

    try:
        from sentence_transformers import SentenceTransformer

        model_name = _st_model_name_for_fallback()
        logger.info("Loading SentenceTransformer model: %s", model_name)
        _sentence_transformer = SentenceTransformer(model_name)
        logger.info("SentenceTransformer loaded successfully")
        return _sentence_transformer
    except ImportError:
        logger.error(
            "sentence_transformers not installed; embedding fallback unavailable",
            exc_info=True,
        )
        _sentence_transformers_init_failed = True
        return None
    except Exception as e:
        logger.error("Failed to initialize SentenceTransformer: %s", e, exc_info=True)
        _sentence_transformers_init_failed = True
        return None


def _compute_content_hash(text: str) -> str:
    """Deterministic hash for deduplication."""
    h = hashlib.sha256()
    h.update(text.encode("utf-8", errors="ignore"))
    return h.hexdigest()


def _call_voyage_api(
    text: str,
    input_type: Literal["query", "document"] = "query",
) -> Optional[List[float]]:
    """Call Voyage API and return vector or None on failure."""
    if not VOYAGE_API_KEY or not VOYAGE_API_KEY.strip():
        return None

    try:
        import requests

        start_time = time.time()
        timeout_sec = max(1.0, EMBEDDING_TIMEOUT_SECONDS)
        url = "https://api.voyageai.com/v1/embeddings"
        headers = {
            "Authorization": f"Bearer {VOYAGE_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "input": text,
            "model": EMBEDDING_MODEL,
            "input_type": input_type,
            "output_dimension": EMBEDDING_OUTPUT_DIM,
            "output_dtype": EMBEDDING_OUTPUT_DTYPE,
        }

        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout_sec,
        )
        elapsed = time.time() - start_time

        if elapsed > timeout_sec:
            logger.warning(
                "Voyage API call exceeded timeout: %.2fs > %ss",
                elapsed,
                timeout_sec,
            )
            raise EmbeddingTimeoutError(
                f"Voyage request timed out after {elapsed:.2f}s"
            )

        if response.status_code == 401:
            logger.error("Voyage API authentication failed; check VOYAGE_API_KEY")
            raise EmbeddingProviderError("Voyage API: invalid authentication")

        if response.status_code >= 500:
            logger.warning(
                "Voyage API returned %s; transient error", response.status_code
            )
            raise EmbeddingProviderError(f"Voyage API: {response.status_code}")

        response.raise_for_status()

        data = response.json()
        embeddings = data.get("data", [])
        if not embeddings or not embeddings[0].get("embedding"):
            logger.error("Voyage API returned invalid response schema: %s", data)
            raise EmbeddingProviderError("Voyage API: invalid response schema")

        vector = embeddings[0]["embedding"]
        logger.debug(
            "Voyage embedding generated: dims=%d latency=%.2fs",
            len(vector),
            elapsed,
        )
        return vector

    except requests.Timeout as e:
        logger.warning("Voyage API timeout: %s", e)
        raise EmbeddingTimeoutError(f"Voyage API timeout: {e}") from e
    except requests.ConnectionError as e:
        logger.warning("Voyage API connection error: %s", e)
        raise EmbeddingProviderError(f"Voyage API connection error: {e}") from e
    except (ValueError, KeyError) as e:
        logger.error("Voyage API response parsing error: %s", e)
        raise EmbeddingProviderError(f"Voyage API: {e}") from e
    except Exception as e:
        logger.error("Unexpected Voyage API error: %s", e, exc_info=True)
        raise EmbeddingProviderError(f"Voyage API: {e}") from e


def _call_sentence_transformer(
    text: str,
    input_type: Literal["query", "document"] = "query",
) -> Optional[List[float]]:
    """Call local SentenceTransformer and return vector or None on failure."""
    del input_type  # ST API does not distinguish query vs document
    client = _init_sentence_transformer()
    if client is None:
        return None

    try:
        start_time = time.time()
        embeddings = client.encode(
            [text], convert_to_numpy=True, show_progress_bar=False
        )
        elapsed = time.time() - start_time

        if embeddings is None or len(embeddings) == 0:
            logger.error("SentenceTransformer returned empty embeddings")
            return None

        vector = (
            embeddings[0].tolist()
            if hasattr(embeddings[0], "tolist")
            else list(embeddings[0])
        )
        logger.debug(
            "SentenceTransformer embedding generated: dims=%d latency=%.2fs",
            len(vector),
            elapsed,
        )
        return vector

    except Exception as e:
        logger.error("SentenceTransformer error: %s", e, exc_info=True)
        return None




def _call_deterministic_embedding(text: str) -> List[float]:
    """Dependency-free deterministic embedding fallback.

    This is not a semantic model, but it is a real, stable vectorization path for
    local tests/offline installs. Production should use Voyage or
    sentence-transformers; healthcheck exposes this provider explicitly instead
    of pretending the primary provider worked.
    """
    import math
    import re

    dim = max(64, int(EMBEDDING_OUTPUT_DIM or 384))
    vec = [0.0] * dim
    tokens = re.findall(r"[\wąćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", (text or "").lower())
    if not tokens:
        tokens = [text or " "]
    for token in tokens:
        digest = hashlib.blake2b(token.encode("utf-8", errors="ignore"), digest_size=16).digest()
        idx = int.from_bytes(digest[:8], "big") % dim
        sign = 1.0 if digest[8] % 2 == 0 else -1.0
        weight = 1.0 + (len(token) % 7) / 10.0
        vec[idx] += sign * weight
        idx2 = int.from_bytes(digest[8:], "big") % dim
        vec[idx2] += sign * 0.35
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [float(v / norm) for v in vec]

def _get_vector_with_fallback(
    text: str,
    input_type: Literal["query", "document"] = "query",
) -> tuple[Optional[List[float]], str, bool, str]:
    """
    Returns:
        (vector, provider_used, embedding_fallback_used, primary_provider_attempted)
    """
    if not text or not text.strip():
        logger.warning("Attempted to embed empty text")
        return None, "", False, ""

    if _remote_embeddings_disabled() and _deterministic_fallback_enabled():
        logger.info("EMBEDDING: remote providers disabled; using deterministic-hash vectors")
        return _call_deterministic_embedding(text), "deterministic-hash", True, "remote-disabled"

    if EMBEDDING_PROVIDER == "sentence-transformers":
        vec = _call_sentence_transformer(text, input_type=input_type)
        prov = "sentence-transformers" if vec is not None else ""
        return vec, prov, False, "sentence-transformers"

    fallback_used = False
    primary_attempted = ""

    if _should_attempt_voyage_first():
        primary_attempted = "voyage"
        for _attempt in range(max(1, EMBEDDING_MAX_RETRIES)):
            try:
                vector = _call_voyage_api(text, input_type=input_type)
                if vector is not None:
                    return vector, "voyage", False, primary_attempted
            except (EmbeddingTimeoutError, EmbeddingProviderError) as e:
                if not _provider_fallback_enabled():
                    logger.error(
                        "EMBEDDING STRICT: Voyage failed and provider fallback is disabled: %s",
                        e,
                    )
                    return None, "", False, primary_attempted
                logger.warning(
                    "EMBEDDING FALLBACK: Voyage failed (%s); switching to SentenceTransformers (%s)",
                    e,
                    _st_model_name_for_fallback(),
                )
                fallback_used = True
                break
            except Exception as e:
                if not _provider_fallback_enabled():
                    logger.error(
                        "EMBEDDING STRICT: Voyage unexpected error and provider fallback is disabled: %s",
                        e,
                    )
                    return None, "", False, primary_attempted
                logger.warning(
                    "EMBEDDING FALLBACK: Voyage unexpected error (%s); switching to SentenceTransformers (%s)",
                    e,
                    _st_model_name_for_fallback(),
                )
                fallback_used = True
                break
        if not fallback_used and primary_attempted == "voyage":
            if not _provider_fallback_enabled():
                logger.error("EMBEDDING STRICT: Voyage returned no vector and provider fallback is disabled")
                return None, "", False, primary_attempted
            logger.warning(
                "EMBEDDING FALLBACK: Voyage returned no vector after retries; using SentenceTransformers (%s)",
                _st_model_name_for_fallback(),
            )
            fallback_used = True
    else:
        primary_attempted = "none"
        if EMBEDDING_PROVIDER == "auto":
            logger.info(
                "EMBEDDING: provider=auto without VOYAGE_API_KEY — SentenceTransformers only (%s)",
                _st_model_name_for_fallback(),
            )

    vector = _call_sentence_transformer(text, input_type=input_type)
    if vector is not None:
        return vector, "sentence-transformers", fallback_used, primary_attempted

    allow_deterministic = _deterministic_fallback_enabled()
    if allow_deterministic:
        logger.warning(
            "EMBEDDING FALLBACK: no Voyage/SentenceTransformers provider available; using deterministic-hash vectors"
        )
        return _call_deterministic_embedding(text), "deterministic-hash", True, primary_attempted

    logger.error("No embedding provider available for text length=%d", len(text))
    return None, "", fallback_used, primary_attempted


def _build_response(
    vector: List[float],
    provider: str,
    input_type: Literal["query", "document"],
    content_hash: str,
    embedding_fallback_used: bool,
    primary_provider_attempted: str,
) -> EmbeddingResponse:
    model_name = (
        EMBEDDING_MODEL if provider == "voyage" else _st_model_name_for_fallback()
    )
    dim = len(vector)
    return EmbeddingResponse(
        vector=vector,
        provider=provider,
        model=model_name,
        input_type=input_type,
        output_dimension=dim,
        output_dtype=EMBEDDING_OUTPUT_DTYPE,
        content_hash=content_hash,
        ts=time.time(),
        embedding_fallback_used=embedding_fallback_used,
        primary_provider_attempted=primary_provider_attempted,
        configured_provider=str(EMBEDDING_PROVIDER),
        configured_model_env=str(EMBEDDING_MODEL),
        embedding_primary_provider_attempted=primary_provider_attempted,
        embedding_primary_provider_used=provider,
        embedding_runtime_dim=dim,
    )


def embed_query(text: str) -> EmbeddingResponse:
    """
    Embed a query text for semantic search.

    Raises:
        EmbeddingError: If all providers fail or text is empty
    """
    text = str(text or "").strip()
    if not text:
        raise EmbeddingError("Cannot embed empty text")

    content_hash = _compute_content_hash(text)
    vector, provider, fb_used, primary = _get_vector_with_fallback(
        text, input_type="query"
    )

    if vector is None:
        logger.error("Failed to embed query: content_hash=%s", content_hash)
        raise EmbeddingError("No embedding provider available")

    return _build_response(vector, provider, "query", content_hash, fb_used, primary)


def embed_document(text: str) -> EmbeddingResponse:
    """
    Embed a document/fact for semantic indexing.

    Raises:
        EmbeddingError: If all providers fail or text is empty
    """
    text = str(text or "").strip()
    if not text:
        raise EmbeddingError("Cannot embed empty text")

    content_hash = _compute_content_hash(text)
    vector, provider, fb_used, primary = _get_vector_with_fallback(
        text, input_type="document"
    )

    if vector is None:
        logger.error("Failed to embed document: content_hash=%s", content_hash)
        raise EmbeddingError("No embedding provider available")

    return _build_response(vector, provider, "document", content_hash, fb_used, primary)


def embed_batch(
    texts: List[str],
    input_type: Literal["query", "document"] = "document",
) -> List[EmbeddingResponse]:
    """
    Embed multiple texts. Blocks on all; individual failures do not stop batch.

    Returns:
        List of EmbeddingResponse, with None for failed texts
    """
    if not texts:
        return []

    results: List[EmbeddingResponse] = []
    for text in texts:
        try:
            if input_type == "query":
                resp = embed_query(text)
            else:
                resp = embed_document(text)
            results.append(resp)
        except EmbeddingError as e:
            logger.warning(
                "Batch embedding failed for text length=%d: %s", len(text), e
            )
            results.append(None)  # type: ignore

    return results


def clear_faiss_dimension_probe_cache() -> None:
    """Clear cached FAISS dimension (e.g. after env change or tests)."""
    global _FAISS_PROBE_DIM
    _FAISS_PROBE_DIM = None


def get_faiss_embedding_dimension() -> int:
    """
    One live embed to determine vector length for FAISS (matches Voyage or fallback).

    Cached per process; cleared by :func:`reset_providers` / :func:`clear_faiss_dimension_probe_cache`.
    """
    global _FAISS_PROBE_DIM
    if _FAISS_PROBE_DIM is not None:
        return _FAISS_PROBE_DIM
    r = embed_query("[faiss_dimension_probe]")
    _FAISS_PROBE_DIM = len(r.vector)
    logger.info(
        "FAISS embedding dimension probe: dim=%d provider=%s model=%s fallback_used=%s",
        _FAISS_PROBE_DIM,
        r.provider,
        r.model,
        r.embedding_fallback_used,
    )
    return _FAISS_PROBE_DIM


def reset_providers() -> None:
    """Reset provider state for testing."""
    global _sentence_transformer, _sentence_transformers_init_failed
    _sentence_transformer = None
    _sentence_transformers_init_failed = False
    clear_faiss_dimension_probe_cache()


def healthcheck() -> Dict[str, Any]:
    """Return config and behavior aligned with :func:`_get_vector_with_fallback`.

    When ``EMBEDDING_HEALTHCHECK_LIVE_PROBE`` is true (default), runs one real
    :func:`embed_query` so ``output_dimension`` matches runtime (single truth).
    Set ``EMBEDDING_HEALTHCHECK_LIVE_PROBE=0`` for fast unit tests without side effects.
    """
    voyage_api_key_present = bool(VOYAGE_API_KEY and VOYAGE_API_KEY.strip())

    will_attempt_voyage_first = _should_attempt_voyage_first()
    st_only_expected = EMBEDDING_PROVIDER == "sentence-transformers" or (
        EMBEDDING_PROVIDER == "auto" and not voyage_api_key_present
    )
    runtime_st_produces_embedding = (
        EMBEDDING_PROVIDER == "sentence-transformers"
        or (EMBEDDING_PROVIDER == "auto" and not voyage_api_key_present)
        or (EMBEDDING_PROVIDER == "voyage" and not voyage_api_key_present)
    )
    st_known_dim = _known_st_output_dimension_for_fallback_model()

    live_probe = bool(EMBEDDING_HEALTHCHECK_LIVE_PROBE)

    if runtime_st_produces_embedding:
        reported_output_dimension: Optional[int] = st_known_dim
        output_dimension_semantics = (
            "sentence_transformers_expected"
            if st_known_dim is not None
            else "unknown_until_embed"
        )
    elif will_attempt_voyage_first and voyage_api_key_present:
        reported_output_dimension = EMBEDDING_OUTPUT_DIM
        output_dimension_semantics = "voyage_primary_expected"
    else:
        reported_output_dimension = EMBEDDING_OUTPUT_DIM
        output_dimension_semantics = "config"

    result: Dict[str, Any] = {
        "provider": EMBEDDING_PROVIDER,
        "model": EMBEDDING_MODEL,
        "output_dimension": reported_output_dimension,
        "output_dtype": EMBEDDING_OUTPUT_DTYPE,
        "voyage_api_key_present": voyage_api_key_present,
        "provider_fallback_enabled": _provider_fallback_enabled(),
        "will_attempt_voyage_first": will_attempt_voyage_first,
        "st_only_expected": st_only_expected,
        "runtime_st_produces_embedding": runtime_st_produces_embedding,
        "output_dimension_semantics": output_dimension_semantics,
        "voyage_request_output_dimension": EMBEDDING_OUTPUT_DIM,
        "sentence_transformers_fallback_model": _st_model_name_for_fallback(),
        "timeout_seconds": EMBEDDING_TIMEOUT_SECONDS,
        "max_retries": EMBEDDING_MAX_RETRIES,
        "sentence_transformers_loaded": _sentence_transformer is not None,
        "sentence_transformers_init_failed": _sentence_transformers_init_failed,
        "active_stack": "embedding_engine (Voyage + optional ST + deterministic fallback)",
        "legacy_stack_note": "memory.embedder + AIHUB_EMBED_MODEL is separate; not this stack",
        "will_use_voyage": will_attempt_voyage_first,
        "embedding_healthcheck_live_probe": live_probe,
        "deterministic_fallback_enabled": _deterministic_fallback_enabled(),
        "remote_embeddings_disabled": _remote_embeddings_disabled(),
    }
    if (
        will_attempt_voyage_first
        and voyage_api_key_present
        and st_known_dim is not None
    ):
        result["st_fallback_output_dimension_if_voyage_fails"] = st_known_dim

    if live_probe:
        try:
            probe = embed_query("__embedding_healthcheck_runtime_probe__")
            od = probe.embedding_runtime_dim
            result["output_dimension"] = od
            result["embedding_runtime_dim"] = od
            result["embedding_runtime_probe_provider"] = probe.provider
            result["embedding_runtime_probe_model"] = probe.model
            result["embedding_runtime_probe_fallback_used"] = (
                probe.embedding_fallback_used
            )
            result["embedding_runtime_probe_primary_attempted"] = (
                probe.embedding_primary_provider_attempted
            )
            result["embedding_runtime_probe_primary_used"] = (
                probe.embedding_primary_provider_used
            )
            if will_attempt_voyage_first and probe.primary_provider_attempted != "voyage":
                result["healthcheck_voyage_path_warning"] = (
                    "will_attempt_voyage_first true but probe primary_provider_attempted="
                    f"{probe.primary_provider_attempted!r}"
                )
        except EmbeddingError as exc:
            result["output_dimension"] = None
            result["embedding_runtime_dim"] = None
            result["embedding_healthcheck_probe_error"] = str(exc)
    return result
