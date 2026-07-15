"""Canonical secret alias resolution and non-disclosing validation."""

from __future__ import annotations

import os
from collections.abc import Mapping


def resolve_llm_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the active LLM credential from supported aliases."""
    source = os.environ if environ is None else environ
    return (source.get("LLM_API_KEY") or source.get("DEEPINFRA_API_KEY") or "").strip()


def resolve_groq_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Resolve Groq reserve provider credential."""
    source = os.environ if environ is None else environ
    return (source.get("GROQ_API_KEY") or "").strip()


def resolve_ollama_api_key(environ: Mapping[str, str] | None = None) -> str:
    """Resolve Ollama Cloud (OpenAI-compatible) credential."""
    source = os.environ if environ is None else environ
    return (source.get("OLLAMA_API_KEY") or source.get("OLLAMA_LLM_API_KEY") or "").strip()


def validate_vault_secret_material(raw: str) -> None:
    """Reject short and obvious placeholder vault secrets without logging them."""
    encoded = raw.encode("utf-8")
    lowered = raw.lower()
    if len(encoded) < 32:
        raise RuntimeError("AIHUB_USER_VAULT_KEY must contain at least 32 bytes")
    if len(set(encoded)) < 12 or any(
        marker in lowered
        for marker in ("changeme", "change-me", "example", "placeholder", "password")
    ):
        raise RuntimeError("AIHUB_USER_VAULT_KEY is weak or uses placeholder material")
