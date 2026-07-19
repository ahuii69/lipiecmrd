"""HTTP API request/response models used by aihub.main.

Compatibility module restored for package bootstrap and endpoint typing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TurnIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    role: str = Field(default="user", min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=200_000)
    meta: dict[str, Any] = Field(default_factory=dict)


class TurnOut(BaseModel):
    id: str
    ts: float


class PsycheGetOut(BaseModel):
    user_id: str
    mood: float
    energy: float
    focus: float
    style: str
    temperature: float
    traits: dict[str, Any] = Field(default_factory=dict)
    updated_at: float


class PsycheUpdateIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=200_000)
    role: str = Field(default="user", min_length=1, max_length=32)


class PsycheReflectIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    query: str = Field(default="", max_length=5_000)
    limit: int = Field(default=10, ge=1, le=50)


class MemoryAddIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    user_msg: str = Field(min_length=1, max_length=200_000)
    assistant_msg: str = Field(min_length=1, max_length=200_000)
    intent: str = Field(default="chat", min_length=1, max_length=128)
    meta: dict[str, Any] = Field(default_factory=dict)


class MemorySearchIn(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=5_000)
    limit: int = Field(default=10, ge=1, le=100)


class MemoryItem(BaseModel):
    id: str
    layer: str
    content: str
    tags: list[str] = Field(default_factory=list)
    score: float
    ts: float
    meta: dict[str, Any] = Field(default_factory=dict)


class MemorySearchOut(BaseModel):
    user_id: str
    query: str
    stm: list[dict[str, Any]] = Field(default_factory=list)
    episodic: list[MemoryItem] = Field(default_factory=list)
    semantic: list[MemoryItem] = Field(default_factory=list)
    psyche: dict[str, Any] = Field(default_factory=dict)
    total: int
    # Unified retrieval extensions (tools / internal callers share shape)
    dense_hits: list[dict[str, Any]] = Field(default_factory=list)
    graph_hits: list[dict[str, Any]] = Field(default_factory=list)
    memory_v2_items: list[dict[str, Any]] = Field(default_factory=list)
    memory_v2_total: int = 0
    memory_v2_contradictions: list[Any] = Field(
        default_factory=list,
    )
    memory_v2_related_procedures: list[dict[str, Any]] = Field(
        default_factory=list,
    )


class FSWriteIn(BaseModel):
    path: str = Field(min_length=1, max_length=5_000)
    content: str = Field(default="", max_length=5_000_000)
    overwrite: bool = True
    confirmed: bool = False


class FSReadIn(BaseModel):
    path: str = Field(min_length=1, max_length=5_000)
    max_bytes: int = Field(default=200_000, ge=1, le=5_000_000)


class FSListIn(BaseModel):
    path: str = Field(default=".", min_length=1, max_length=5_000)
    recursive: bool = False
    max_items: int = Field(default=200, ge=1, le=10_000)


class WebFetchIn(BaseModel):
    url: str = Field(min_length=5, max_length=8_000)


class WebResearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=5_000)
    research_type: str = Field(default="general", min_length=1, max_length=64)


class WebIngestIn(BaseModel):
    url: str = Field(min_length=5, max_length=8_000)
    importance: float = Field(default=0.6, ge=0.0, le=1.0)
    confidence: float = Field(default=0.72, ge=0.0, le=1.0)
    session_id: str | None = Field(default=None, max_length=256)


class SnapshotCreateIn(BaseModel):
    reason: str = Field(default="manual", min_length=1, max_length=200)
    confirmed: bool = False


class SnapshotRestoreIn(BaseModel):
    snapshot_id: str = Field(min_length=1, max_length=256)
