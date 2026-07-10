#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Base class for model provider implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from aihub.chat_contracts import ModelResponse
from aihub.llm.provider_types import ProviderChatRequest


class BaseProvider(ABC):
    """Provider-agnostic model adapter contract."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Stable provider identifier."""

    @abstractmethod
    async def generate(self, request: ProviderChatRequest) -> ModelResponse:
        """Execute one normalized chat request."""
