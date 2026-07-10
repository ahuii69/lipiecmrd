#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Provider registry and config-driven provider resolution."""

from __future__ import annotations

from typing import Callable, Dict

from aihub.config import (
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_DEFAULT_TEMPERATURE,
    LLM_MAX_RETRIES,
    LLM_MODEL_NAME,
    LLM_PROVIDER_NAME,
    LLM_STREAMING_ENABLED,
    LLM_TIMEOUT_S,
    LLM_TOOL_CALLING_ENABLED,
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


def _build_deepinfra_provider() -> BaseProvider:
    return DeepInfraProvider(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        default_model=LLM_MODEL_NAME,
        timeout_seconds=LLM_TIMEOUT_S,
        max_retries=LLM_MAX_RETRIES,
        default_temperature=LLM_DEFAULT_TEMPERATURE,
        tool_calling_enabled=LLM_TOOL_CALLING_ENABLED,
        streaming_enabled=LLM_STREAMING_ENABLED,
    )


_registry = ProviderRegistry()
_registry.register("deepinfra", _build_deepinfra_provider)


def get_provider_registry() -> ProviderRegistry:
    return _registry


def get_default_provider() -> BaseProvider:
    return _registry.create(LLM_PROVIDER_NAME)
