#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tool registry datatypes and execution contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Literal, Type

from pydantic import BaseModel, Field

from aihub.chat_contracts import CapabilityDescriptor

ToolMode = Literal["chat", "agent", "readonly", "debug"]
ToolVisibility = List[ToolMode]


class ToolExecutionContext(BaseModel):
    """Execution context passed to tool handlers."""

    user_id: str
    session_id: str
    mode: ToolMode
    include_debug: bool = False
    policy_overrides: Dict[str, Any] = Field(default_factory=dict)


ToolHandler = Callable[[ToolExecutionContext, Any], Awaitable[Dict[str, Any]]]


@dataclass
class ToolDefinition:
    """Internal definition for one capability/tool."""

    name: str
    description: str
    capability_group: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    enabled: bool
    read_only: bool
    requires_confirmation: bool
    timeout_seconds: float
    visibility: ToolVisibility
    handler: ToolHandler

    def to_descriptor(self) -> CapabilityDescriptor:
        return CapabilityDescriptor(
            name=self.name,
            description=self.description,
            capability_group=self.capability_group,
            input_schema=self.input_model.model_json_schema(),
            output_schema=self.output_model.model_json_schema(),
            enabled=self.enabled,
            read_only=self.read_only,
            requires_confirmation=self.requires_confirmation,
            timeout_seconds=float(self.timeout_seconds),
            visibility=list(self.visibility),
        )
