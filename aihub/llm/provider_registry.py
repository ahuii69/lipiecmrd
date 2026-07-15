#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Provider registry and config-driven provider resolution."""

from __future__ import annotations

from typing import Callable, Dict, List, Optional

from aihub.config import (
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_DEFAULT_TEMPERATURE,
    LLM_MAX_RETRIES,
    LLM_MODEL_NAME,
    LLM_PRIMARY_PROVIDER,
    LLM_PROVIDER_INTERNAL_RETRIES,
    LLM_PROVIDER_NAME,
    LLM_STREAMING_ENABLED,
    LLM_TIMEOUT_S,
    LLM_TOOL_CALLING_ENABLED,
    OLLAMA_API_KEY,
    OLLAMA_LLM_BASE_URL,
    OLLAMA_LLM_MODEL,
    llm_reserve_provider_names,
)
from aihub.llm.provider_base import BaseProvider
from aihub.llm.providers.deepinfra_provider import DeepInfraProvider

ProviderFactory = Callable[[], BaseProvider]


class ProviderRegistry:
    """Mutable registry for available provider implementations."""

    def __init__(self) -> None:
        self._factories: Dict[str, ProviderFactory] = {}

    def register(self, name: str, factory: ProviderFactory) -> None:
        key = str(name or "").strip().lower()
        if not key:
            raise ValueError("provider name is required")
        self._factories[key] = factory

    def create(self, name: str) -> BaseProvider:
        key = str(name or "").strip().lower()
        if key not in self._factories:
            raise KeyError(f"unknown provider: {name}")
        return self._factories[key]()

    def list_providers(self) -> Dict[str, str]:
        return {k: "registered" for k in sorted(self._factories.keys())}


def _internal_retries() -> int:
    return max(0, int(LLM_PROVIDER_INTERNAL_RETRIES))


def _build_deepinfra_provider() -> BaseProvider:
    return DeepInfraProvider(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        default_model=LLM_MODEL_NAME,
        timeout_seconds=LLM_TIMEOUT_S,
        max_retries=_internal_retries(),
        default_temperature=LLM_DEFAULT_TEMPERATURE,
        tool_calling_enabled=LLM_TOOL_CALLING_ENABLED,
        streaming_enabled=LLM_STREAMING_ENABLED,
        provider_name="deepinfra",
        use_max_completion_tokens=False,
    )


def _build_groq_provider() -> BaseProvider:
    return DeepInfraProvider(
        api_key=GROQ_API_KEY,
        base_url=GROQ_BASE_URL,
        default_model=GROQ_MODEL,
        timeout_seconds=LLM_TIMEOUT_S,
        max_retries=_internal_retries(),
        default_temperature=LLM_DEFAULT_TEMPERATURE,
        tool_calling_enabled=LLM_TOOL_CALLING_ENABLED,
        streaming_enabled=LLM_STREAMING_ENABLED,
        provider_name="groq",
        use_max_completion_tokens=True,
    )


def _build_ollama_provider() -> BaseProvider:
    return DeepInfraProvider(
        api_key=OLLAMA_API_KEY,
        base_url=OLLAMA_LLM_BASE_URL,
        default_model=OLLAMA_LLM_MODEL,
        timeout_seconds=LLM_TIMEOUT_S,
        max_retries=_internal_retries(),
        default_temperature=LLM_DEFAULT_TEMPERATURE,
        tool_calling_enabled=LLM_TOOL_CALLING_ENABLED,
        streaming_enabled=LLM_STREAMING_ENABLED,
        provider_name="ollama",
        use_max_completion_tokens=False,
    )


_registry = ProviderRegistry()
_registry.register("deepinfra", _build_deepinfra_provider)
_registry.register("groq", _build_groq_provider)
_registry.register("ollama", _build_ollama_provider)


def get_provider_registry() -> ProviderRegistry:
    return _registry


def get_default_provider() -> BaseProvider:
    """Primary chat provider (backward-compatible alias)."""
    return get_primary_provider()


def get_primary_provider() -> BaseProvider:
    return _registry.create(LLM_PRIMARY_PROVIDER or LLM_PROVIDER_NAME)


def _reserve_has_credentials(name: str) -> bool:
    key = name.strip().lower()
    if key == "groq":
        return bool((GROQ_API_KEY or "").strip())
    if key == "ollama":
        return bool((OLLAMA_API_KEY or "").strip())
    return True


def get_reserve_provider() -> Optional[BaseProvider]:
    """First configured reserve provider (backward compat)."""
    reserves = get_reserve_providers()
    return reserves[0] if reserves else None


def get_reserve_providers() -> List[BaseProvider]:
    """Ordered reserve providers after primary."""
    out: List[BaseProvider] = []
    for name in reserve_provider_names():
        if not _reserve_has_credentials(name):
            continue
        try:
            out.append(_registry.create(name))
        except KeyError:
            continue
    return out


def reserve_provider_names() -> List[str]:
    return llm_reserve_provider_names()


def provider_candidate_names() -> List[str]:
    names = [LLM_PRIMARY_PROVIDER or LLM_PROVIDER_NAME]
    for reserve in reserve_provider_names():
        if reserve not in names:
            names.append(reserve)
    return names


def build_provider_execution_service(primary: BaseProvider | None = None):
    """Canonical provider execution with transparent primary→reserve failover."""
    from aihub.turn.provider_service import ProviderExecutionService

    if primary is None:
        try:
            import aihub.chat_runtime as cr

            primary = cr.get_default_provider()
        except Exception:
            primary = get_primary_provider()
    reserves = get_reserve_providers()
    return ProviderExecutionService(primary=primary, reserves=reserves)
