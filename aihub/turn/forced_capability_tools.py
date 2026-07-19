"""Forced pre-LLM tool execution for capability closing (image / memory / ingest)."""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from aihub.chat_contracts import ChatMessage, ToolCallRequest, ToolCallResult
from aihub.chat_image_generation import (
    compose_image_prompt_package,
    extract_image_subject,
)
from aihub.chat_stream_session import emit_tool_event, stream_session_active
from aihub.tools.types import ToolExecutionContext

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

_REMEMBER_STRIP = re.compile(
    r"(?is)^\s*(?:zapamiętaj|zapamietaj|remember(?:\s+that)?|note(?:\s+that)?|"
    r"zapisz[,\s]+że|zapisz[,\s]+ze)\s*[:\-–,]?\s*"
)


def extract_remember_fact(message: str) -> str:
    raw = (message or "").strip()
    fact = _REMEMBER_STRIP.sub("", raw).strip()
    return fact if len(fact) >= 2 else raw


def extract_ingest_url(message: str) -> str | None:
    m = _URL_RE.search(message or "")
    if not m:
        return None
    return m.group(0).rstrip(").,;]>\"'")


def build_image_markdown_reply(result: dict[str, Any]) -> str:
    """User-facing reply that embeds the generated image (BFF path)."""
    file_id = str(result.get("file_id") or "").strip()
    if not file_id:
        err = result.get("error") or result.get("message") or "unknown"
        return f"Nie udało się wygenerować obrazu ({err})."
    path = f"/api/aihub/chat/file/{file_id}"
    desc = str(result.get("description_pl") or result.get("subject_used") or "wygenerowany obraz")
    model = str(result.get("model") or "")
    tail = f"\n\n_Model: `{model}`_" if model else ""
    return f"Wygenerowałem obraz.\n\n![{desc}]({path}){tail}"


def build_memory_fact_reply(fact: str, fact_id: str | None) -> str:
    fid = (fact_id or "").strip()
    suffix = f" (id: `{fid}`)" if fid else ""
    return f"Zapamiętałem fakt{suffix}:\n\n> {fact.strip()[:800]}"


def build_ingest_reply(url: str, out: dict[str, Any]) -> str:
    mem = out.get("memory_ids") if isinstance(out, dict) else None
    ok = bool(out.get("ok", True)) if isinstance(out, dict) else True
    if not ok:
        return f"Nie udało się wczytać URL `{url}`: {out.get('error') or 'unknown'}"
    extra = ""
    if isinstance(mem, dict) and mem:
        extra = f"\nZapisano do pamięci: {', '.join(f'{k}={v}' for k, v in list(mem.items())[:4])}."
    title = str((out or {}).get("title") or "").strip()
    head = f"**{title}**\n" if title else ""
    return f"Wczytałem i zapisałem stronę:\n{head}`{url}`{extra}"


async def run_forced_capability_tools(
    *,
    self: Any,
    turn: Any,
    ctx: Any,
    decision_core: dict[str, Any],
    tool_calls: list[ToolCallRequest],
    tool_results: list[ToolCallResult],
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Execute forced tools before the LLM when capability flags demand it.

    Returns:
      short_circuit: optional ChatTurnResult fields dict if LLM can be skipped
      messages: list[ChatMessage] to prepend into the prompt
    """
    out: dict[str, Any] = {
        "short_circuit_text": None,
        "short_circuit_reason": None,
        "messages": [],
        "ran": [],
    }

    async def _exec(name: str, arguments: dict[str, Any], reason: str) -> ToolCallResult:
        call = ToolCallRequest(
            tool_call_id=f"forced_{name.replace('.', '_')}_{int(time.time() * 1000)}",
            name=name,
            arguments=arguments,
        )
        tool_calls.append(call)
        exec_ctx = ToolExecutionContext(
            user_id=turn.user_id,
            session_id=turn.session_id,
            mode=ctx.mode,
            include_debug=turn.include_debug,
            policy_overrides=dict(turn.tool_policy_overrides or {}),
        )
        started = time.monotonic()
        tlabel = self._sse_tool_display_name(name)
        if stream_session_active():
            await emit_tool_event(tlabel, "start")
        try:
            result = await self._tool_router.execute(call, exec_ctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=name,
                ok=False,
                error=f"tool_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        if stream_session_active():
            await emit_tool_event(tlabel, "done")
        tool_results.append(result)
        if not result.ok:
            errors.append(
                {"type": "forced_capability_tool_error", "error": result.error or "unknown", "tool": name, "reason": reason}
            )
        out["ran"].append({"tool": name, "ok": result.ok, "reason": reason})
        return result

    # --- image.generate ---
    if decision_core.get("force_image_generate"):
        hint = extract_image_subject(turn.message or "")
        pkg = compose_image_prompt_package(hint)
        result = await _exec(
            "image.generate",
            {
                "user_message": turn.message or "",
                "subject": hint,
                "subject_hint": hint,
            },
            "capability_force_image_generate",
        )
        payload: dict[str, Any] = {}
        envelope_ok = True
        if isinstance(result.output, dict):
            envelope_ok = bool(result.output.get("ok", True))
            payload = dict(result.output.get("result") or {})
        effective_ok = bool(result.ok) and envelope_ok and bool(payload.get("file_id"))
        # Enrich with prompt package for reply text
        payload.setdefault("description_pl", pkg.get("description_pl"))
        payload.setdefault("subject_used", pkg.get("subject_used"))
        if not effective_ok:
            payload.setdefault(
                "error",
                payload.get("error") or result.error or "image_failed",
            )
        reply = build_image_markdown_reply(payload if effective_ok else {"error": payload.get("error") or "image_failed"})
        out["short_circuit_text"] = reply
        out["short_circuit_reason"] = "forced_image_generate"
        out["messages"].append(
            ChatMessage(
                role="tool",
                name="image.generate",
                tool_call_id=result.tool_call_id,
                content=json.dumps(
                    {"ok": effective_ok, "output": result.output, "error": result.error},
                    ensure_ascii=False,
                ),
            )
        )
        return out

    # --- memory.add_fact ---
    if decision_core.get("force_memory_add_fact"):
        fact = extract_remember_fact(turn.message or "")
        result = await _exec(
            "memory.add_fact",
            {"fact": fact, "tags": ["user_request", "capability_forced"], "meta": {"source": "capability_escalation"}},
            "capability_force_memory_add_fact",
        )
        fact_id = None
        if result.ok and isinstance(result.output, dict):
            inner = result.output.get("result") or result.output
            if isinstance(inner, dict):
                fact_id = inner.get("fact_id")
        reply = (
            build_memory_fact_reply(fact, str(fact_id) if fact_id else None)
            if result.ok
            else f"Nie udało się zapisać faktu: {result.error or 'unknown'}"
        )
        out["short_circuit_text"] = reply
        out["short_circuit_reason"] = "forced_memory_add_fact"
        return out

    # --- web.ingest_url ---
    if decision_core.get("force_web_ingest"):
        url = str(decision_core.get("ingest_url") or extract_ingest_url(turn.message or "") or "").strip()
        if url:
            result = await _exec(
                "web.ingest_url",
                {
                    "url": url,
                    "importance": 0.7,
                    "confidence": 0.75,
                    "session_id": turn.session_id,
                },
                "capability_force_web_ingest",
            )
            inner: dict[str, Any] = {}
            if result.ok and isinstance(result.output, dict):
                inner = dict(result.output.get("result") or result.output)
                inner["ok"] = True
            else:
                inner = {"ok": False, "error": result.error}
            reply = build_ingest_reply(url, inner)
            out["short_circuit_text"] = reply
            out["short_circuit_reason"] = "forced_web_ingest"
            return out

    return out
