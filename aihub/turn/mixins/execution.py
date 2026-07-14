"""TurnOps mixin: ExecutionMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound by aihub.turn.ops

class ExecutionMixin:
    async def _execute_agent_handoff(
        self,
        *,
        turn: ChatTurnInput,
        decision_core: dict[str, Any],
        handoff_reason: str,
        started: float,
        psyche_snapshot: dict[str, Any],
        memory_used_trace: list[dict[str, Any]] | None = None,
        memory_lookup_flag: bool = False,
        blocker_verdict: BlockerVerdict | None = None,
        memory_context: dict[str, Any] | None = None,
        ctx: ChatTurnContext | None = None,
    ) -> ChatTurnResult:
            from aihub.turn.mixins.execution_handoff import run_execute_agent_handoff
            return await run_execute_agent_handoff(self, turn=turn, decision_core=decision_core, handoff_reason=handoff_reason, started=started, psyche_snapshot=psyche_snapshot, memory_used_trace=memory_used_trace, memory_lookup_flag=memory_lookup_flag, blocker_verdict=blocker_verdict, memory_context=memory_context, ctx=ctx)

    async def _provider_call(
        self,
        *,
        messages: list[ChatMessage],
        tools: list[ProviderToolSpec],
    ) -> ModelResponse:
        # Canonical provider path — timeout/retry/classification via ProviderExecutionService.
        # Legacy TypeError signature sniffing removed.
        from aihub.turn.models import RuntimeEnvironment
        from aihub.chat_contracts import ToolCallRequest as _TCR

        env = RuntimeEnvironment()
        active = getattr(self, "_active_turn_ctx", None)
        if active is not None:
            env = active.environment
        cancelled = bool(getattr(getattr(active, "cancellation", None), "cancelled", False))
        remaining = float(getattr(active, "remaining_s", None) or env.provider_total_timeout_s)
        exec_res = await self._provider_service.execute(
            messages=messages,
            tools=tools,
            environment=env,
            cancelled=cancelled,
            remaining_s=remaining,
            trace=getattr(self, "_active_trace_builder", None),
        )
        tool_calls = []
        for idx, tc in enumerate(exec_res.tool_calls or []):
            if isinstance(tc, dict):
                tool_calls.append(
                    _TCR(
                        tool_call_id=str(tc.get("tool_call_id") or f"tool-{idx}"),
                        name=str(tc.get("name") or "tool"),
                        arguments=dict(tc.get("arguments") or {}),
                    )
                )
        usage = exec_res.usage if isinstance(exec_res.usage, ProviderUsage) else ProviderUsage()
        if isinstance(exec_res.usage, dict):
            try:
                usage = ProviderUsage(**{k: exec_res.usage.get(k, 0) for k in ("prompt_tokens", "completion_tokens", "total_tokens")} | {"reporting_mode": exec_res.usage.get("reporting_mode", "unavailable")})
            except Exception:
                usage = ProviderUsage()
        return ModelResponse(
            provider=exec_res.provider or self._current_provider_name(),
            model=exec_res.model or LLM_MODEL_NAME,
            content=exec_res.content,
            finish_reason=exec_res.finish_reason or "stop",
            tool_calls=tool_calls,
            usage=usage,
            latency_ms=float(exec_res.latency_ms or 0.0),
        )
        # Unreachable legacy path retained below for reference during migration — never executed
        use_stream = stream_session_active() and LLM_STREAMING_ENABLED and not tools
        req = ProviderChatRequest(
            messages=messages,
            model=LLM_MODEL_NAME,
            tools=tools,
            stream=use_stream,
        )
        generate = getattr(self._provider, "generate")
        try:
            raw = await generate(req)
        except TypeError as exc:
            msg = str(exc)
            if "missing 1 required positional argument" not in msg:
                raise
            raw = await generate(self._provider, req)

        if isinstance(raw, ModelResponse):
            return raw
        if isinstance(raw, dict):
            message = raw.get("message") if isinstance(raw.get("message"), dict) else {}
            content = str(raw.get("content") or message.get("content") or "")
            raw_tool_calls = raw.get("tool_calls") or message.get("tool_calls") or []
            tool_calls: list[ToolCallRequest] = []
            for idx, call in enumerate(raw_tool_calls):
                if isinstance(call, ToolCallRequest):
                    tool_calls.append(call)
                elif isinstance(call, dict):
                    tool_calls.append(
                        ToolCallRequest(
                            tool_call_id=str(
                                call.get("tool_call_id")
                                or call.get("id")
                                or f"tool-{idx}"
                            ),
                            name=str(call.get("name") or call.get("function", {}).get("name") or "tool"),
                            arguments=dict(
                                call.get("arguments")
                                or call.get("function", {}).get("arguments")
                                or {}
                            ),
                        )
                    )
            usage_obj = raw.get("usage")
            usage = usage_obj if isinstance(usage_obj, ProviderUsage) else ProviderUsage()
            return ModelResponse(
                provider=str(getattr(self._provider, "provider_name", "mock")),
                model=str(raw.get("model") or LLM_MODEL_NAME),
                content=content,
                finish_reason=str(raw.get("finish_reason") or raw.get("stop_reason") or "stop"),
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=float(raw.get("latency_ms") or 0.0),
                raw_response_id=str(raw.get("raw_response_id") or raw.get("id") or ""),
            )
        if all(hasattr(raw, attr) for attr in ("content", "model", "provider")):
            raw_tool_calls = list(getattr(raw, "tool_calls", []) or [])
            tool_calls: list[ToolCallRequest] = []
            for idx, call in enumerate(raw_tool_calls):
                if isinstance(call, ToolCallRequest):
                    tool_calls.append(call)
                elif isinstance(call, dict):
                    tool_calls.append(
                        ToolCallRequest(
                            tool_call_id=str(call.get("tool_call_id") or call.get("id") or f"tool-{idx}"),
                            name=str(call.get("name") or call.get("function", {}).get("name") or "tool"),
                            arguments=dict(call.get("arguments") or call.get("function", {}).get("arguments") or {}),
                        )
                    )
            usage_obj = getattr(raw, "usage", None)
            if isinstance(usage_obj, ProviderUsage):
                usage = usage_obj
            elif usage_obj is not None:
                usage = ProviderUsage(
                    prompt_tokens=int(getattr(usage_obj, "prompt_tokens", 0) or 0),
                    completion_tokens=int(getattr(usage_obj, "completion_tokens", 0) or 0),
                    total_tokens=int(getattr(usage_obj, "total_tokens", 0) or 0),
                    reporting_mode=str(getattr(usage_obj, "reporting_mode", "unavailable") or "unavailable"),
                )
            else:
                usage = ProviderUsage()
            return ModelResponse(
                provider=str(getattr(raw, "provider", self._current_provider_name()) or self._current_provider_name()),
                model=str(getattr(raw, "model", LLM_MODEL_NAME) or LLM_MODEL_NAME),
                content=str(getattr(raw, "content", "") or getattr(raw, "text", "") or ""),
                finish_reason=str(getattr(raw, "finish_reason", "stop") or "stop"),
                tool_calls=tool_calls,
                usage=usage,
                latency_ms=float(getattr(raw, "latency_ms", 0.0) or 0.0),
                raw_response_id=str(getattr(raw, "raw_response_id", "") or getattr(raw, "id", "") or ""),
            )
        raise TypeError(f"provider.generate returned unsupported type: {type(raw).__name__}")

    @staticmethod
    def _sum_usage(parts: list[ProviderUsage]) -> ProviderUsage:
        if not parts:
            return ProviderUsage(reporting_mode="unavailable")

        modes = {str(p.reporting_mode or "unavailable") for p in parts}
        if modes == {"provider"}:
            reporting_mode = "provider"
        elif "provider" in modes:
            reporting_mode = "partial"
        else:
            reporting_mode = "unavailable"

        return ProviderUsage(
            prompt_tokens=sum(p.prompt_tokens for p in parts),
            completion_tokens=sum(p.completion_tokens for p in parts),
            total_tokens=sum(p.total_tokens for p in parts),
            reporting_mode=reporting_mode,
        )

    async def _provider_failure_fallback(
        self,
        turn: ChatTurnInput,
        *,
        reason: str,
        decision_core: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Dry technical fallback only — never executive controller on provider failure."""
        fallback_text = dry_fallback_response(user_message=turn.message)
        return fallback_text, {
            "reason": reason,
            "fallback_cycle": None,
            "executive_skipped": True,
            "degraded": True,
        }

    @staticmethod
    def _safe_preview(obj: Any, max_chars: int = 600) -> str:
        try:
            text = json.dumps(obj, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(obj)
        if len(text) > max_chars:
            return text[:max_chars] + " ...[TRUNCATED]"
        return text

    @staticmethod
    def _tokenize_for_similarity(text: str) -> set[str]:
        """Tokenize text for lightweight heuristic similarity matching."""
        raw = re.findall(r"[\wąćęłńóśźż]{3,}", (text or "").lower())
        stop = {
            "oraz",
            "który",
            "która",
            "które",
            "których",
            "przez",
            "jest",
            "było",
            "będzie",
            "tego",
            "that",
            "this",
            "with",
            "from",
            "have",
            "will",
            "into",
            "without",
        }
        return {t for t in raw if t not in stop}

    def _current_provider_name(self) -> str:
        return str(
            getattr(
                self._provider,
                "provider_name",
                getattr(self._provider, "name", "mock"),
            )
        )

