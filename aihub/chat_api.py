#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Frontend-facing chat API endpoints.

Jedyna ścieżka decyzyjna tur czatu HTTP: ``get_chat_runtime().run_turn`` → ``ChatRuntime._run_turn_core``.
Nie dodawaj równoległego routingu czatu poza tym modułem.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from io import BytesIO
from typing import Any, Dict, Union

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from aihub.chat_context_compose import derive_context_chips_from_trace
from aihub.chat_contracts import ChatTurnInput, ChatTurnResult, ToolCallRequest
from aihub.ops_trace import attach_runtime_trace_summary
from aihub.chat_file_service import save_multipart_upload
from aihub.chat_runtime import get_chat_runtime
from aihub.chat_stream_session import CHAT_STREAM_SESSION, STREAM_END, ChatStreamSession
from aihub.chat_stt_service import transcribe_audio_bytes
from aihub.tools.registry import get_tool_registry
from aihub.tools.router import ToolRouter
from aihub.tools.types import ToolExecutionContext, ToolMode

router = APIRouter(prefix="/chat", tags=["chat"])
logger = logging.getLogger(__name__)


class ToolPolicyOverridesInput(BaseModel):
    """Explicit, validated policy switches accepted by the operator API."""

    allow_sensitive_mutations: bool = False

    model_config = {"extra": "forbid"}


class CapabilityExecuteInput(BaseModel):
    user_id: str = Field(default="default", min_length=1, max_length=128)
    session_id: str = Field(default="default", min_length=1, max_length=128)
    mode: ToolMode = "chat"
    include_debug: bool = False
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: Dict[str, Any] = Field(default_factory=dict)
    confirmed: bool = False
    tool_policy_overrides: ToolPolicyOverridesInput = Field(
        default_factory=ToolPolicyOverridesInput
    )

    model_config = {"extra": "forbid"}


def _sse_data_line(obj: dict[str, Any]) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")


def _finalize_chat_turn_result(res: ChatTurnResult) -> ChatTurnResult:
    if isinstance(res.trace, dict):
        attach_runtime_trace_summary(res.trace)
    return res


def _chunk_response_text(text: str, chunk_size: int = 48) -> list[str]:
    if not text:
        return []
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


async def _sse_chat_turn(
    runtime: Any,
    payload: ChatTurnInput,
    request: Request,
    *,
    include_turn_result: bool,
) -> AsyncIterator[bytes]:
    """Concurrent SSE: live events from runtime + final text / done."""
    q: asyncio.Queue = asyncio.Queue(maxsize=2048)
    sess = ChatStreamSession(queue=q)
    token = CHAT_STREAM_SESSION.set(sess)
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    async def worker() -> None:
        try:
            result["r"] = await runtime.run_turn(payload)
        except BaseException as exc:  # noqa: BLE001
            error["e"] = exc
        finally:
            await q.put(STREAM_END)

    task = asyncio.create_task(worker())
    next_heartbeat_at = time.monotonic() + 1.2
    try:
        while True:
            if task.done():
                while True:
                    try:
                        evt = q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if evt is STREAM_END:
                        break
                    if await request.is_disconnected():
                        task.cancel()
                        return
                    yield _sse_data_line(evt)
                break
            try:
                evt = await asyncio.wait_for(q.get(), timeout=0.05)
            except TimeoutError:
                if await request.is_disconnected():
                    task.cancel()
                    return
                now = time.monotonic()
                if now >= next_heartbeat_at:
                    yield _sse_data_line(
                        {
                            "type": "status",
                            "stage": "thinking",
                            "label_pl": "AI-Hub pracuje…",
                        }
                    )
                    next_heartbeat_at = now + 1.2
                continue
            if evt is STREAM_END:
                break
            if await request.is_disconnected():
                task.cancel()
                return
            yield _sse_data_line(evt)
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("Chat stream producer task cancelled during cleanup")
        else:
            try:
                _ = task.exception()
            except asyncio.CancelledError:
                logger.debug("Chat stream producer task was already cancelled")
        CHAT_STREAM_SESSION.reset(token)

    exc = error.get("e")
    if exc is not None:
        if isinstance(exc, asyncio.CancelledError):
            return
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    r = result.get("r")
    if r is None:
        raise HTTPException(status_code=500, detail="empty turn result")

    r = _finalize_chat_turn_result(r)

    final_text = (r.response_text or "").strip()
    streamed = (sess.text_accum or "").strip()
    if not sess.from_provider:
        for part in _chunk_response_text(r.response_text or ""):
            if await request.is_disconnected():
                return
            yield _sse_data_line({"type": "delta", "content": part})
            await asyncio.sleep(0.012)
    elif final_text != streamed:
        if await request.is_disconnected():
            return
        yield _sse_data_line({"type": "replace", "content": r.response_text or ""})

    done_payload: dict[str, Any] = {
        "type": "done",
        "ok": bool(getattr(r, "ok", False)),
    }
    if not bool(getattr(r, "ok", False)):
        # Always surface failure so clients cannot treat HTTP 200 as success.
        done_payload["error"] = "turn_failed"
        errs = list(getattr(r, "errors", None) or [])
        if errs:
            done_payload["errors"] = errs[:5]
        tr = r.trace if isinstance(r.trace, dict) else {}
        if tr.get("effective_runtime_path"):
            done_payload["effective_runtime_path"] = tr.get("effective_runtime_path")
        # Lightweight result for UI even when include_turn_result=false
        done_payload["result"] = {
            "ok": False,
            "response_text": r.response_text or "",
            "trace": {
                "selected_strategy": tr.get("selected_strategy"),
                "effective_runtime_path": tr.get("effective_runtime_path"),
                "agent_handoff_error": tr.get("agent_handoff_error"),
            },
            "errors": errs[:5],
        }
    chips = derive_context_chips_from_trace(
        r.trace if isinstance(r.trace, dict) else {},
        input_via_stt=bool(payload.input_via_stt),
    )
    if chips:
        done_payload["context_chips"] = chips
    if include_turn_result or payload.include_debug:
        done_payload["result"] = r.model_dump(mode="json")
    if r.attachments_summary:
        done_payload["attachments_summary"] = r.attachments_summary
    yield _sse_data_line(done_payload)


@router.post("/stt")
async def chat_stt(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Transkrypcja mowy → tekst (multipart). Self-hosted: faster-whisper + ffmpeg."""
    raw = await file.read()
    name = file.filename or "audio.webm"
    return await transcribe_audio_bytes(data=raw, filename=name)


@router.post("/upload")
async def chat_upload(
    user_id: str = Form(...),
    session_id: str = Form(...),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Multipart upload dla czatu: .txt / .md / .pdf oraz obrazy .png / .jpeg / .webp."""
    raw = await file.read()
    res = save_multipart_upload(
        user_id=(user_id or "").strip() or "default",
        session_id=(session_id or "").strip() or "default",
        filename=file.filename or "upload.bin",
        content_type=file.content_type or "",
        file_obj=BytesIO(raw),
    )
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res)
    out: dict[str, Any] = {
        "file_id": res["file_id"],
        "filename": res["filename"],
        "content_type": res["content_type"],
        "size": res["size"],
        "extracted_text_preview": res["extracted_text_preview"],
        "status": res["status"],
    }
    if res.get("extract_error"):
        out["extract_error"] = res["extract_error"]
    return out


@router.post("/turn", response_model=None)
async def chat_turn(
    request: Request,
    payload: ChatTurnInput,
    stream: bool = Query(
        False, description="If true, SSE (text/event-stream) with delta chunks."
    ),
    include_turn_result: bool = Query(
        False,
        description="If true (with stream), append full ChatTurnResult on done event.",
    ),
) -> Union[ChatTurnResult, StreamingResponse]:
    runtime = get_chat_runtime()
    # Bound turn identity to authenticated principal (never silent default fallback).
    principal = getattr(request.state, "principal", None)
    if principal is not None and getattr(principal, "user_id", None):
        payload = payload.model_copy(
            update={"user_id": str(principal.user_id).strip()}
        )
    if stream:
        return StreamingResponse(
            _sse_chat_turn(
                runtime,
                payload,
                request,
                include_turn_result=include_turn_result,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    try:
        out = _finalize_chat_turn_result(await runtime.run_turn(payload))
        chips = derive_context_chips_from_trace(
            out.trace if isinstance(out.trace, dict) else {},
            input_via_stt=bool(payload.input_via_stt),
        )
        if chips:
            out = out.model_copy(update={"context_chips": chips})
        return out
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/capabilities")
def chat_capabilities(
    mode: ToolMode = Query(default="chat"),
    include_debug: bool = Query(default=False),
) -> Dict[str, Any]:
    registry = get_tool_registry()
    capabilities = registry.list_capabilities(
        mode=mode,
        include_debug=include_debug,
        policy_overrides={},
    )
    return {
        "ok": True,
        "mode": mode,
        "include_debug": include_debug,
        "count": len(capabilities),
        "capabilities": [c.model_dump() for c in capabilities],
    }


@router.post("/capabilities/execute")
async def chat_capabilities_execute(payload: CapabilityExecuteInput) -> Dict[str, Any]:
    """Execute one registered capability through existing ToolRouter policies/schemas.

    This endpoint is intended for operator cockpit workflows (planner preview,
    reasoning preview, goals control, runtime introspection) without relying on
    model-mediated tool-calling.
    """
    registry = get_tool_registry()
    tool_router = ToolRouter(registry)

    arguments = dict(payload.arguments or {})
    if payload.confirmed:
        arguments["_confirmed"] = True

    call = ToolCallRequest(
        tool_call_id=f"manual_{int(time.time() * 1000)}",
        name=payload.tool_name,
        arguments=arguments,
    )
    ctx = ToolExecutionContext(
        user_id=payload.user_id,
        session_id=payload.session_id,
        mode=payload.mode,
        include_debug=payload.include_debug,
        policy_overrides=payload.tool_policy_overrides.model_dump(),
    )

    result = await tool_router.execute(call, ctx)
    if not result.ok:
        error = str(result.error or "capability execution failed")
        if error.startswith("policy_blocked:"):
            status_code = 403
        elif error.startswith("input_validation_error:"):
            status_code = 422
        elif error.startswith("tool_timeout"):
            status_code = 504
        elif "not registered" in error.lower() or "unknown tool" in error.lower():
            status_code = 404
        else:
            status_code = 502
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": error,
                "tool_name": payload.tool_name,
                "tool_call_id": result.tool_call_id,
            },
        )
    return {
        "ok": True,
        "mode": payload.mode,
        "tool_name": payload.tool_name,
        "tool_result": result.model_dump(),
    }
