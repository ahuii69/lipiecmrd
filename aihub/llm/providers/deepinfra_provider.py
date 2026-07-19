#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""DeepInfra provider in OpenAI-compatible chat completions mode."""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import time
from typing import Any, Dict, List, Optional

import httpx

from aihub.chat_contracts import ModelResponse, ProviderUsage, ToolCallRequest
from aihub.chat_stream_session import chat_stream_emit, current_stream_session
from aihub.config import HTTP_CA_BUNDLE, HTTP_TRUST_ENV
from aihub.llm.provider_base import BaseProvider
from aihub.llm.provider_types import ProviderChatRequest, ProviderError

logger = logging.getLogger(__name__)


class _ToolCallsInStreamError(Exception):
    """Model started tool calls over SSE — caller should retry without stream."""


def _build_ssl_context() -> ssl.SSLContext:
    if HTTP_CA_BUNDLE:
        return ssl.create_default_context(cafile=HTTP_CA_BUNDLE)
    return ssl.create_default_context()


class DeepInfraProvider(BaseProvider):
    """OpenAI-compatible chat completions adapter (DeepInfra, Groq, etc.)."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        default_model: str,
        timeout_seconds: float,
        max_retries: int,
        default_temperature: float,
        tool_calling_enabled: bool,
        streaming_enabled: bool,
        client: Optional[httpx.AsyncClient] = None,
        provider_name: str = "deepinfra",
        use_max_completion_tokens: bool = False,
    ) -> None:
        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._default_model = default_model
        self._timeout_seconds = float(timeout_seconds)
        self._max_retries = max(0, int(max_retries))
        self._default_temperature = float(default_temperature)
        self._tool_calling_enabled = bool(tool_calling_enabled)
        self._streaming_enabled = bool(streaming_enabled)
        self._client = client
        self._provider_name = str(provider_name or "deepinfra").strip().lower()
        self._use_max_completion_tokens = bool(use_max_completion_tokens)

    @property
    def provider_name(self) -> str:
        return self._provider_name

    def _build_messages(self, request: ProviderChatRequest) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for message in request.messages:
            item: Dict[str, Any] = {
                "role": message.role,
                "content": message.content,
            }
            if message.name:
                item["name"] = message.name
            if message.tool_call_id:
                item["tool_call_id"] = message.tool_call_id
            if message.role == "assistant" and message.tool_calls:
                item["tool_calls"] = [
                    {
                        "id": tc.tool_call_id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                        },
                    }
                    for tc in message.tool_calls
                ]
            out.append(item)
        return out

    def _build_tools(self, request: ProviderChatRequest) -> List[Dict[str, Any]]:
        if not self._tool_calling_enabled:
            return []
        tools: List[Dict[str, Any]] = []
        for t in request.tools:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
            )
        return tools

    def _normalize_http_error(
        self,
        response: httpx.Response,
        *,
        default_code: str,
    ) -> ProviderError:
        status = int(response.status_code)
        payload: Dict[str, Any] = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}

        err_obj = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(err_obj, dict):
            err_obj = {}

        message = str(
            err_obj.get("message") or payload.get("message")
            if isinstance(payload, dict)
            else response.text
        )
        code = str(err_obj.get("code") or default_code)

        retryable = status in {408, 409, 425, 429} or status >= 500
        if status == 402:
            retryable = False
            code = code if code and code != "http_error" else "insufficient_balance"

        return ProviderError(
            provider=self.provider_name,
            code=code,
            message=message or f"HTTP {status}",
            retryable=retryable,
            status_code=status,
            details={"response": payload},
        )

    def _normalize_transport_error(self, exc: Exception) -> ProviderError:
        retryable = isinstance(exc, (httpx.TimeoutException, httpx.TransportError))
        code = "timeout" if isinstance(exc, httpx.TimeoutException) else "transport"
        return ProviderError(
            provider=self.provider_name,
            code=code,
            message=str(exc),
            retryable=retryable,
            details={"exception_type": type(exc).__name__},
        )

    @classmethod
    def _extract_text_content(cls, raw: Any) -> str:
        from aihub.response_runtime_guard import extract_assistant_text

        return extract_assistant_text(raw)

    @classmethod
    def _prefer_final_answer(cls, text: str) -> str:
        """If blob looks like planner CoT, keep only a concrete final answer when present."""
        from aihub.response_persona_guard import strip_reasoning_leak

        cleaned, _changed = strip_reasoning_leak(text or "")
        return cleaned

    def _parse_model_response(
        self,
        *,
        data: Dict[str, Any],
        model_name: str,
        latency_ms: float,
    ) -> ModelResponse:
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            raise ProviderError(
                provider=self.provider_name,
                code="invalid_response",
                message="provider response missing choices",
                retryable=False,
                details={"response": data},
            )

        first_raw = choices[0]
        first: Dict[str, Any] = first_raw if isinstance(first_raw, dict) else {}
        message_raw = first.get("message")
        message: Dict[str, Any] = message_raw if isinstance(message_raw, dict) else {}
        # Prefer visible assistant content only. Do not promote raw `reasoning`
        # fields to user-facing text (gpt-oss / open-weight planner dumps).
        content = (
            self._extract_text_content(message.get("content"))
            or self._extract_text_content(message.get("output_text"))
            or self._extract_text_content(first.get("text"))
            or self._extract_text_content(data.get("output_text"))
            or self._extract_text_content(data.get("output"))
        )
        content = self._prefer_final_answer(content)
        if not content.strip():
            # Last resort only when provider put the entire answer in reasoning.
            reasoning_blob = (
                self._extract_text_content(message.get("reasoning"))
                or self._extract_text_content(message.get("reasoning_content"))
            )
            content = self._prefer_final_answer(reasoning_blob)
        finish_reason = str(first.get("finish_reason") or "stop")

        tool_calls: List[ToolCallRequest] = []
        raw_tool_calls = message.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            for idx, raw_call in enumerate(raw_tool_calls):
                if not isinstance(raw_call, dict):
                    continue
                fn_raw = raw_call.get("function")
                fn: Dict[str, Any] = fn_raw if isinstance(fn_raw, dict) else {}
                call_id = str(raw_call.get("id") or f"tool_call_{idx + 1}")
                name = str(fn.get("name") or "")
                if not name:
                    continue
                raw_args = fn.get("arguments")
                args: Dict[str, Any]
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args)
                        args = parsed if isinstance(parsed, dict) else {"value": parsed}
                    except Exception:
                        args = {"_raw": raw_args}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}

                tool_calls.append(
                    ToolCallRequest(
                        tool_call_id=call_id,
                        name=name,
                        arguments=args,
                    )
                )

        usage_raw = data.get("usage")
        usage_data: Dict[str, Any] = usage_raw if isinstance(usage_raw, dict) else {}
        usage_present = any(
            key in usage_data
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        usage = ProviderUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_data.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_data.get("total_tokens", 0) or 0),
            reporting_mode="provider" if usage_present else "unavailable",
        )

        return ModelResponse(
            provider=self.provider_name,
            model=str(data.get("model") or model_name),
            content=content,
            finish_reason=finish_reason,
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=latency_ms,
            raw_response_id=str(data.get("id") or ""),
        )

    async def _request_once(
        self,
        *,
        payload: Dict[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        if not self._api_key:
            raise ProviderError(
                provider=self.provider_name,
                code="missing_api_key",
                message="LLM API key is missing",
                retryable=False,
            )

        url = f"{self._base_url}/chat/completions"
        client = self._client or httpx.AsyncClient(
            timeout=timeout_seconds,
            verify=_build_ssl_context(),
            trust_env=HTTP_TRUST_ENV,
        )
        owns_client = self._client is None

        try:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise self._normalize_http_error(resp, default_code="http_error")
            try:
                data = resp.json()
            except Exception as exc:
                raise ProviderError(
                    provider=self.provider_name,
                    code="invalid_json",
                    message=f"provider returned non-JSON payload: {exc}",
                    retryable=False,
                ) from exc
            if not isinstance(data, dict):
                raise ProviderError(
                    provider=self.provider_name,
                    code="invalid_response",
                    message="provider JSON payload is not an object",
                    retryable=False,
                )
            return data
        except ProviderError:
            raise
        except Exception as exc:
            raise self._normalize_transport_error(exc) from exc
        finally:
            if owns_client:
                await client.aclose()

    async def _stream_chat_completions(
        self,
        *,
        payload: Dict[str, Any],
        model_name: str,
        timeout_seconds: float,
    ) -> ModelResponse:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if not self._api_key:
            raise ProviderError(
                provider=self.provider_name,
                code="missing_api_key",
                message="LLM API key is missing",
                retryable=False,
            )
        url = f"{self._base_url}/chat/completions"
        client = self._client or httpx.AsyncClient(
            timeout=timeout_seconds,
            verify=_build_ssl_context(),
            trust_env=HTTP_TRUST_ENV,
        )
        owns_client = self._client is None
        start = time.monotonic()
        pieces: list[str] = []
        finish_reason = "stop"
        raw_id = ""
        usage_data: Dict[str, Any] = {}
        try:
            async with client.stream(
                "POST",
                url,
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            ) as resp:
                if resp.status_code >= 400:
                    body = await resp.aread()
                    raise self._normalize_http_error(
                        httpx.Response(resp.status_code, content=body),
                        default_code="http_error",
                    )
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(chunk, dict):
                        continue
                    if not raw_id and chunk.get("id"):
                        raw_id = str(chunk.get("id") or "")
                    choices = chunk.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    ch0 = choices[0] if isinstance(choices[0], dict) else {}
                    delta = ch0.get("delta")
                    if not isinstance(delta, dict):
                        delta = {}
                    tcd = delta.get("tool_calls")
                    if isinstance(tcd, list) and len(tcd) > 0:
                        raise _ToolCallsInStreamError()
                    piece = delta.get("content")
                    if isinstance(piece, str) and piece:
                        pieces.append(piece)
                        sess = current_stream_session()
                        if sess is not None:
                            sess.append_provider_delta(piece)
                        await chat_stream_emit({"type": "delta", "content": piece})
                    fr = ch0.get("finish_reason")
                    if fr:
                        finish_reason = str(fr)
                    u = chunk.get("usage")
                    if isinstance(u, dict):
                        usage_data = u
        except ProviderError:
            raise
        except _ToolCallsInStreamError:
            raise
        except Exception as exc:
            raise self._normalize_transport_error(exc) from exc
        finally:
            if owns_client:
                await client.aclose()

        latency_ms = (time.monotonic() - start) * 1000.0
        content = "".join(pieces)
        usage_present = any(
            key in usage_data
            for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        )
        usage = ProviderUsage(
            prompt_tokens=int(usage_data.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage_data.get("completion_tokens", 0) or 0),
            total_tokens=int(usage_data.get("total_tokens", 0) or 0),
            reporting_mode="provider" if usage_present else "unavailable",
        )
        return ModelResponse(
            provider=self.provider_name,
            model=str(model_name),
            content=content,
            finish_reason=finish_reason or "stop",
            tool_calls=[],
            usage=usage,
            latency_ms=latency_ms,
            raw_response_id=raw_id,
        )

    async def generate(self, request: ProviderChatRequest) -> ModelResponse:
        model_name = request.model or self._default_model
        timeout_seconds = float(request.timeout_seconds or self._timeout_seconds)
        stream = bool(request.stream and self._streaming_enabled)

        payload: Dict[str, Any] = {
            "model": model_name,
            "messages": self._build_messages(request),
            "temperature": (
                self._default_temperature
                if request.temperature is None
                else float(request.temperature)
            ),
            "stream": stream,
        }
        if request.max_tokens is not None:
            if self._use_max_completion_tokens:
                # gpt-oss spends completion budget on hidden reasoning first —
                # a too-low floor returns planning prose instead of the answer.
                payload["max_completion_tokens"] = max(int(request.max_tokens), 768)
            else:
                payload["max_tokens"] = int(request.max_tokens)
        elif self._use_max_completion_tokens:
            # gpt-oss on Groq uses reasoning tokens; ensure room for visible content.
            payload["max_completion_tokens"] = 1024

        tools_payload = self._build_tools(request)
        if tools_payload:
            payload["tools"] = tools_payload
            if request.tool_choice is not None:
                payload["tool_choice"] = request.tool_choice

        if stream and tools_payload:
            payload["stream"] = False
            stream = False

        attempts = self._max_retries + 1
        last_error: Optional[ProviderError] = None

        for attempt in range(1, attempts + 1):
            start = time.monotonic()
            try:
                if stream:
                    try:
                        parsed = await self._stream_chat_completions(
                            payload=payload,
                            model_name=model_name,
                            timeout_seconds=timeout_seconds,
                        )
                        if not (parsed.content or "").strip() and not parsed.tool_calls:
                            raise ProviderError(
                                provider=self.provider_name,
                                code="empty_response",
                                message="provider returned empty content",
                                retryable=False,
                                details={"stream": True},
                            )
                        return parsed
                    except _ToolCallsInStreamError:
                        payload["stream"] = False
                        stream = False
                data = await self._request_once(
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
                latency_ms = (time.monotonic() - start) * 1000.0
                parsed = self._parse_model_response(
                    data=data,
                    model_name=model_name,
                    latency_ms=latency_ms,
                )
                if not (parsed.content or "").strip() and not parsed.tool_calls:
                    raise ProviderError(
                        provider=self.provider_name,
                        code="empty_response",
                        message="provider returned empty content",
                        retryable=False,
                        details={"response": data},
                    )
                return parsed
            except ProviderError as exc:
                last_error = exc
                if (not exc.retryable) or attempt >= attempts:
                    raise
                backoff = min(2.0, 0.25 * (2 ** (attempt - 1)))
                logger.warning(
                    "provider.retry provider=%s attempt=%d/%d code=%s status=%s backoff=%.2fs",
                    self.provider_name,
                    attempt,
                    attempts,
                    exc.code,
                    exc.status_code,
                    backoff,
                )
                await asyncio.sleep(backoff)

        # Safety fallback (should be unreachable because of raise above)
        if last_error is not None:
            raise last_error

        raise ProviderError(
            provider=self.provider_name,
            code="unknown_error",
            message="provider call failed unexpectedly",
            retryable=False,
        )
