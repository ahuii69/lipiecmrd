#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Provider-layer request/response/error contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from aihub.chat_contracts import ChatMessage


class ProviderToolSpec(BaseModel):
    """Provider-neutral function/tool schema descriptor."""

    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2000)
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class ProviderChatRequest(BaseModel):
    """Normalized chat request consumed by providers."""

    messages: List[ChatMessage] = Field(default_factory=list)
    model: str = Field(min_length=1, max_length=256)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=2.0)
    tools: List[ProviderToolSpec] = Field(default_factory=list)
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    stream: bool = False
    max_tokens: Optional[int] = Field(default=None, ge=1, le=32768)
    timeout_seconds: Optional[float] = Field(default=None, ge=1.0, le=600.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ProviderError(Exception):
    """Normalized provider exception used across runtime layers."""

    def __init__(
        self,
        *,
        provider: str,
        code: str,
        message: str,
        retryable: bool = False,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.details = details or {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "status_code": self.status_code,
            "details": self.details,
        }
