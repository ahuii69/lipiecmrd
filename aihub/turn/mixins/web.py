"""TurnOps mixin: WebMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound by aihub.turn.ops

class WebMixin:
    async def _run_controlled_web_prefetch(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        web_decision: str = "off",
    ) -> dict[str, Any]:
        """Deterministic web decision for ACTIVE chat path before provider call.

        Controlled Web Orchestration V1: execution is driven by web_decision
        from strategy selector (decision_core), not by independent heuristics.

        web_decision values:
          - "required": always trigger (URL or research.query)
          - "optional": trigger only if explicit URL present
          - "off": skip entirely
        """
        if web_decision == "off":
            return {
                "triggered": False,
                "reason": "decision_off",
                "tool_name": None,
                "tool_call": None,
                "tool_result": None,
                "messages": [],
            }

        url = self._extract_first_url(turn.message)
        call: ToolCallRequest | None = None
        reason = ""

        if url:
            call = ToolCallRequest(
                tool_call_id=f"controlled_web_{int(time.time() * 1000)}",
                name="web.fetch_url",
                arguments={"url": url},
            )
            reason = "explicit_url"
        elif web_decision == "required":
            # Strategy says web is required, no explicit URL — use research.query
            call = ToolCallRequest(
                tool_call_id=f"controlled_web_{int(time.time() * 1000)}",
                name="research.query",
                arguments={"query": turn.message, "research_type": "general"},
            )
            reason = "web_decision_required"

        if call is None:
            return {
                "triggered": False,
                "reason": "not_required",
                "tool_name": None,
                "tool_call": None,
                "tool_result": None,
                "messages": [],
            }

        exec_ctx = ToolExecutionContext(
            user_id=turn.user_id,
            session_id=turn.session_id,
            mode=ctx.mode,
            include_debug=turn.include_debug,
            policy_overrides=dict(turn.tool_policy_overrides or {}),
        )
        started = time.monotonic()
        tlabel = TurnOps._sse_tool_display_name(call.name)
        if stream_session_active():
            await emit_tool_event(tlabel, "start")
        try:
            result = await self._tool_router.execute(call, exec_ctx)
        except Exception as exc:  # noqa: BLE001
            result = ToolCallResult(
                tool_call_id=call.tool_call_id,
                name=call.name,
                ok=False,
                error=f"tool_error: {exc}",
                latency_ms=(time.monotonic() - started) * 1000.0,
            )
        if stream_session_active():
            await emit_tool_event(tlabel, "done")

        payload = {
            "ok": result.ok,
            "name": call.name,
            "reason": reason,
            "error": result.error,
            "output_preview": self._safe_preview(result.output, max_chars=2200),
        }

        messages = [
            ChatMessage(
                role="assistant",
                content=(
                    "Prefetch web (runtime): wynik w wiadomości narzędzia — "
                    "użyj jako źródło, nie powtarzaj suchej deklaracji bez treści."
                ),
                tool_calls=[call],
            ),
            ChatMessage(
                role="tool",
                name=call.name,
                tool_call_id=call.tool_call_id,
                content=json.dumps(payload, ensure_ascii=False),
            ),
        ]

        return {
            "triggered": True,
            "reason": reason,
            "tool_name": call.name,
            "tool_call": call,
            "tool_result": result,
            "messages": messages,
        }

    @staticmethod
    def _web_required_grounding_unsatisfied(
        decision_core: dict[str, Any],
        controlled_web: dict[str, Any],
    ) -> bool:
        """Jawny fail weba WYŁĄCZNIE przy spełnionych łącznie (AND):

        1. ``web_decision == "required"`` (selector),
        2. ``controlled_web["triggered"] is True`` (prefetch faktycznie uruchomiony),
        3. ``ok is not True`` LUB ``has_results is not True`` (brak zweryfikowanego wyniku).

        Samo ``required`` bez ``triggered`` NIE ucina tury — dalsza ścieżka (LLM + tools)
        może dowieźć grounding.
        """
        if str(decision_core.get("web_decision") or "off") != "required":
            return False
        if not controlled_web.get("triggered"):
            return False
        if controlled_web.get("ok") is not True:
            return True
        return controlled_web.get("has_results") is not True

    @staticmethod
    def _web_stage_trace_fields(
        decision_core: dict[str, Any],
        controlled_web: dict[str, Any],
        *,
        explicit_fail_applied: bool,
    ) -> dict[str, Any]:
        """Truthful web-stage slice: decyzja vs prefetch vs wynik (bez mylenia „required” z „failed”)."""
        wd = str(decision_core.get("web_decision") or "off")
        req = wd == "required"
        trig = bool(controlled_web.get("triggered"))
        ok = controlled_web.get("ok")
        hr = controlled_web.get("has_results")
        verified = trig and ok is True and hr is True
        out: dict[str, Any] = {
            "web_stage_decision": wd,
            "web_explicit_fail_applied": bool(explicit_fail_applied),
            "web_prefetch_executed": trig,
            "web_continued_after_required_without_prefetch": bool(req and not trig),
        }
        if explicit_fail_applied:
            out["web_final_grounding_outcome"] = "explicit_fail_after_prefetch"
        elif verified:
            out["web_final_grounding_outcome"] = "prefetch_verified_in_thread"
        elif req and not trig:
            out["web_final_grounding_outcome"] = "required_prefetch_not_run_continuing"
        else:
            out["web_final_grounding_outcome"] = (
                "optional_or_off_web_decision"
                if wd in ("optional", "off")
                else "no_verified_prefetch_not_required_fail"
            )
        return out

    @staticmethod
    def _classify_web_required_failure(
        controlled_web: dict[str, Any],
    ) -> tuple[str, str]:
        """(web_grounding_outcome, web_subsystem_operation)."""
        op = str(controlled_web.get("tool_name") or "")
        if "fetch" in op or "url" in op:
            operation = "url_fetch"
        elif "research" in op or "query" in op:
            operation = "research_query"
        else:
            operation = "web_unknown"
        if not controlled_web.get("triggered"):
            return "prefetch_skipped", operation
        if controlled_web.get("ok") is not True:
            return "tool_failed", operation
        hr = controlled_web.get("has_results")
        if hr is False:
            return "empty_results", operation
        if hr is None:
            return "unverified_payload", operation
        return "unknown", operation

    def _web_required_ungrounded_user_message(
        self,
        *,
        outcome: str,
        controlled_web: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> str:
        """Naturalna odpowiedź gdy web był wymagany, ale brak zweryfikowanego ugruntowania."""
        logger.debug(
            "web_required_ungrounded clamp: outcome=%s tool=%s",
            outcome,
            controlled_web.get("tool_name"),
        )
        if errors:
            logger.debug("web_required_ungrounded errors=%s", len(errors))
        query = str(
            controlled_web.get("query")
            or controlled_web.get("controlled_web_query")
            or ""
        ).strip()
        query_hint = f" (m.in. „{query}”)" if query else ""
        if outcome == "empty_results":
            return (
                f"Przeszukałem sieć kilkoma wariantami zapytania{query_hint}, "
                "ale w dostępnych źródłach nie znalazłem wiarygodnego potwierdzenia. "
                "Podaj datę, ligę, dokładniejszy kontekst albo link — wtedy doprecyzuję "
                "i sprawdzę ponownie."
            )
        if outcome == "tool_failed":
            return (
                "Wyszukiwanie sieciowe nie oddało wyniku — narzędzie zwróciło błąd "
                "albo pustą odpowiedź. Mogę spróbować innym sformułowaniem; doprecyzuj też, "
                "co dokładnie chcesz zweryfikować."
            )
        if outcome == "unverified_payload":
            return (
                "Dostałem sygnał z wyszukiwania, ale nie udało się go zweryfikować jako "
                "wiarygodne źródło. Reformułuję zapytanie albo podaj więcej kontekstu — "
                "wtedy idę dalej."
            )
        return (
            "Nie udało się ugruntować odpowiedzi w sieci mimo próby wyszukiwania. "
            "Powiedz, co dokładnie mam sprawdzić i w jakim kontekście — zrobię kolejny strzał."
        )

    @staticmethod
    def _web_fail_detail_for_trace(
        *,
        outcome: str,
        controlled_web: dict[str, Any],
        errors: list[dict[str, Any]],
    ) -> str:
        prov = str(controlled_web.get("provider_info") or "").strip()
        base = (
            "prefetch web bez zweryfikowanego wyniku (pusty wynik, błąd narzędzia "
            "albo nieczytelna odpowiedź)"
        )
        if outcome == "tool_failed":
            err = next(
                (e for e in errors if e.get("type") == "controlled_web_error"),
                None,
            )
            detail = str((err or {}).get("error") or prov or "").strip()
            if detail:
                return f"{base}; szczegół: {detail[:500]}"
        if outcome == "empty_results":
            return f"{base}; 0 wyników" + (f" ({prov})" if prov else "")
        if outcome == "unverified_payload":
            return base + "; niezweryfikowany payload"
        return base

    @staticmethod
    def _attach_web_observability_trace(
        trace: dict[str, Any],
        *,
        controlled_web: dict[str, Any],
        tool_results: list[ToolCallResult],
        web_verified_in_prompt: bool,
    ) -> None:
        """Ujednolicone pola: web_used, sources_count (obok controlled_web_*).

        ``web_used`` = faktycznie użyte zweryfikowane źródła (nie sam fakt ``ok`` przy pustym wyniku).
        """
        w_in = web_verified_in_prompt
        used = llm_path_verified_research_grounding(w_in, tool_results)
        if bool(controlled_web.get("triggered")):
            if controlled_web.get("ok") is not True:
                used = False
            elif controlled_web.get("has_results") is not True:
                used = False
        trace["web_used"] = bool(used)
        trace["sources_count"] = int(controlled_web.get("source_count") or 0)

    async def _finish_turn_web_required_ungrounded(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        started: float,
        decision_core: dict[str, Any],
        blocker_verdict: BlockerVerdict,
        controlled_web: dict[str, Any],
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
        errors: list[dict[str, Any]],
        memory_lookup_flag: bool,
        memory_used_trace: dict[str, Any] | None,
        include_stm_in_memory_brief: bool,
        psyche_snapshot: dict[str, Any],
        attachment_meta: list[Any],
        attachments_summary: str,
        hist_for_prompt_len: int,
        vault_user_redacted: bool,
        hist_smart_trim: dict[str, Any] | None = None,
    ) -> ChatTurnResult:
        duration_ms = (time.monotonic() - started) * 1000.0
        outcome, web_op = self._classify_web_required_failure(controlled_web)
        dc = decision_core
        reason_codes = list(dc.get("reason_codes") or [])
        reason_codes.append("WEB_EXPLICIT_FAIL_PREFETCH_TRIGGERED")
        reason_codes.append("WEB_REQUIRED_NO_VERIFIED_GROUNDING")

        trace: dict[str, Any] = {
            "provider_calls": 0,
            "tool_iterations": 0,
            "used_tools": len(tool_results) > 0,
            "used_fallback": False,
            "response_grounding_mode": "web_required_ungrounded",
            "duration_ms": duration_ms,
            "provider": self._current_provider_name(),
            "model": LLM_MODEL_NAME,
            "selected_strategy": dc["selected_strategy"],
            **self._decision_core_trace_escalation(dc),
            "reason_codes": reason_codes,
            "strategy_confidence": dc["strategy_confidence"],
            "degraded": dc.get("strategy_degraded", False),
            "memory_lookup_happened": memory_lookup_flag,
            "memory_results_count": memory_results_count_for_trace(ctx.memory_context),
            "psyche_snapshot_happened": False,
            "research_was_required": True,
            "experience_write_back_attempted": False,
            "experience_write_back_succeeded": False,
            "controlled_web_decision": dc.get("web_decision", "off"),
            "controlled_web_decision_reason": dc.get(
                "web_decision_reason", "not_evaluated"
            ),
            "controlled_web_triggered": bool(controlled_web.get("triggered")),
            "controlled_web_reason": controlled_web.get("reason"),
            "controlled_web_tool": controlled_web.get("tool_name"),
            "controlled_web_ok": controlled_web.get("ok"),
            "controlled_web_has_results": controlled_web.get("has_results"),
            "controlled_web_provider_info": controlled_web.get("provider_info"),
            "controlled_web_query": controlled_web.get("query"),
            "controlled_web_source_count": int(controlled_web.get("source_count") or 0),
            "controlled_web_freshness_needed": controlled_web.get(
                "freshness_needed", False
            ),
            "web_subsystem_operation": web_op,
            "consistency_check_ran": dc["consistency_check_ran"],
            "consistency_classification": dc["consistency_classification"],
            "contradictions_found": dc["contradictions_found"],
            "policy_hints_loaded": dc["policy_hints_loaded"],
            "policy_profile_name": dc["policy_profile_name"],
            "simulation_ran": dc["simulation_ran"],
            "simulation_best_action": dc["simulation_best_action"],
            "simulation_variants_count": dc["simulation_variants_count"],
            "simulation_risk_summary": dc["simulation_risk_summary"],
            "experience_lookup_happened": dc.get("experience_lookup_happened", False),
            "experience_matches_count": dc.get("experience_matches_count", 0),
            "experience_influenced_strategy": dc.get(
                "experience_influenced_strategy", False
            ),
            "experience_confidence_adjustment": dc.get(
                "experience_confidence_adjustment"
            ),
            "experience_handoff_bias": dc.get("experience_handoff_bias"),
            "experience_blocker_reason": dc.get("experience_blocker_reason"),
            "experience_signal_summary": dc.get("experience_signal_summary"),
            "selected_goal": dc.get("selected_goal"),
            "policy_feedback_loaded": bool(dc.get("policy_feedback_loaded")),
            "policy_feedback_applied": bool(dc.get("policy_feedback_applied")),
            "policy_feedback_summary": dc.get("policy_feedback_summary", ""),
            "policy_confidence_delta": dc.get("policy_confidence_delta", 0.0),
            "policy_handoff_bias": dc.get("policy_handoff_bias", 0.0),
            "policy_blocker_sensitivity": dc.get("policy_blocker_sensitivity", 0.0),
            "policy_simulation_risk_cal": dc.get("policy_simulation_risk_cal", 0.0),
            "policy_strategy_adjustments": dc.get("policy_strategy_adjustments", {}),
            "attached_files": attachment_meta,
            "attachments_summary": attachments_summary,
            "blocker_verdict": blocker_verdict.model_dump(),
            "tool_calls_requested": len(tool_calls),
            "tool_calls_executed": len(tool_results),
            "tool_calls_successful": len([r for r in tool_results if r.ok]),
            "tool_failures": len([r for r in tool_results if not r.ok]),
            **self._web_stage_trace_fields(
                dc, controlled_web, explicit_fail_applied=True
            ),
            **build_history_trace(turn),
        }
        response_text = self._web_required_ungrounded_user_message(
            outcome=outcome,
            controlled_web=controlled_web,
            errors=errors,
        )
        if memory_used_trace:
            trace["memory_used"] = memory_used_trace
        self._augment_memory_observability(trace, memory_used_trace, ctx.memory_context)
        trace_blocker_gate_outcome(trace, gate_evaluated=True, hard_applied=False)
        merge_canonical_web_required_ungrounded(
            trace,
            memory_lookup_happened=memory_lookup_flag,
            planner_used=False,
            outcome_reason=outcome,
            blocker_verdict_snapshot=blocker_verdict.model_dump(),
        )
        trace["web_fail_detail"] = self._web_fail_detail_for_trace(
            outcome=outcome,
            controlled_web=controlled_web,
            errors=errors,
        )
        self._attach_web_observability_trace(
            trace,
            controlled_web=controlled_web,
            tool_results=tool_results,
            web_verified_in_prompt=False,
        )
        _mem_t = memory_truth_for_prompt(ctx.memory_context)
        trace["memory_substantive_in_prompt"] = bool(
            _mem_t["memory_substantive_in_prompt"]
        )
        trace["memory_stm_brief_included"] = include_stm_in_memory_brief
        trace["context_history_messages_attached"] = hist_for_prompt_len
        trace["vault_user_message_redacted"] = vault_user_redacted
        if hist_smart_trim:
            trace.update(hist_smart_trim)
        augment_trace_context_truth(
            trace,
            mem_truth=memory_truth_for_prompt(ctx.memory_context),
            controlled_web=controlled_web,
            decision_core=dc,
            force_no_web_verified=True,
        )
        try:
            from aihub.runtime_psyche_bridge import build_psyche_v2_behavior_context

            trace.update(
                self._final_behavior_trace_fields(
                    build_psyche_v2_behavior_context(turn.user_id)
                )
            )
        except Exception as exc:
            logger.debug(
                "web_required_ungrounded: psyche behavior fields skipped: %s", exc
            )
            trace.update(self._final_behavior_trace_fields(None))

        self._write_back_experience(
            turn=turn,
            response_text=response_text,
            grounding_mode="web_required_ungrounded",
            tool_calls=tool_calls,
            tool_results=tool_results,
            trace=trace,
            errors=errors,
            psyche_snapshot=psyche_snapshot,
            decision_core=dc,
        )
        if str(getattr(turn, "runtime_mode", "") or "").lower() == "audit":
            trace["experience_write_back_attempted"] = False
            trace["experience_write_back_succeeded"] = False
        self._run_runtime_experience_feedback(turn.user_id, trace)
        _cr_hook("append_event", append_event)(
            turn.user_id,
            "chat.turn",
            {
                "ok": False,
                "provider": self._current_provider_name(),
                "model": LLM_MODEL_NAME,
                "errors": errors,
                "trace": trace,
            },
        )
        result = ChatTurnResult(
            ok=False,
            response_text=response_text,
            model=LLM_MODEL_NAME,
            provider=self._current_provider_name(),
            tool_calls=tool_calls,
            tool_results=tool_results,
            selected_mode=ctx.mode,
            usage=ProviderUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0),
            trace=trace,
            errors=errors,
            debug={"context": ctx.model_dump()} if turn.include_debug else None,
            attachments_summary=attachments_summary,
        )
        _TRACE_CACHE[turn.user_id].append(result.trace)
        return result

    def _assess_web_result_quality(self, result: ToolCallResult | None) -> bool | None:
        """Assess if web/research tool result contains meaningful data."""
        if not isinstance(result, ToolCallResult) or not result.ok:
            return False

        try:
            # Handle both dict and string output
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                import json

                data = json.loads(result.output)
            else:
                return None

            if isinstance(data.get("result"), dict):
                data = data["result"]

            # For research.query: grounding is satisfied by the presence of real results.
            # Fresh search snippets (title + content) are injected into the LLM prompt as
            # grounding regardless of regex fact-extraction. Requiring total_facts>0 wrongly
            # discarded valid results (e.g. news whose content didn't match the brittle fact
            # patterns), yielding "BRAK DANYCH (web)" despite real sources being available.
            if "total_results" in data and "total_facts" in data:
                return data.get("total_results", 0) > 0

            # For web.fetch_url: check bytes and text length
            if "bytes" in data and "text" in data:
                return (
                    data.get("bytes", 0) > 100
                    and len(data.get("text", "").strip()) > 50
                )

        except (json.JSONDecodeError, KeyError, AttributeError) as exc:
            logger.debug("Web result sufficiency parse failed: %s", exc)

        return None

    def _extract_web_provider_info(self, result: ToolCallResult | None) -> str | None:
        """Extract helpful provider/status info from web result."""
        if not isinstance(result, ToolCallResult):
            return None

        try:
            # Handle both dict and string output
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                import json

                data = json.loads(result.output)
            else:
                return result.error if result.error else "unknown"

            if isinstance(data.get("result"), dict):
                data = data["result"]

            # Research result provider info
            if "web_provider" in data:
                provider = data.get("web_provider", "unknown")
                total_results = data.get("total_results", 0)
                reason = data.get("reason", "")

                if reason:
                    return f"{provider} - {reason}"
                elif total_results == 0:
                    return f"{provider} - no results"
                else:
                    return f"{provider} - {total_results} results"

            # Web fetch result status
            if "status" in data:
                status = data.get("status", "unknown")
                bytes_count = data.get("bytes", 0)
                return f"HTTP {status} - {bytes_count} bytes"

        except (json.JSONDecodeError, KeyError, AttributeError) as exc:
            logger.debug("Web result sufficiency parse failed: %s", exc)

        return result.error if result.error else "unknown"

    def _extract_web_query(self, call: ToolCallRequest | None) -> str | None:
        """Extract the query string or URL that was sent to the web tool."""
        if call is None:
            return None
        args = call.arguments or {}
        # web.fetch_url → url; research.query → query
        if "url" in args:
            return str(args["url"])[:500]
        if "query" in args:
            return str(args["query"])[:500]
        return None

    def _count_web_sources(self, result: ToolCallResult | None) -> int:
        """Count how many distinct sources the web/research tool returned."""
        if not isinstance(result, ToolCallResult) or not result.ok:
            return 0
        try:
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                data = json.loads(result.output)
            else:
                return 0

            if isinstance(data.get("result"), dict):
                data = data["result"]

            # research.query returns total_results
            if "total_results" in data:
                return int(data.get("total_results", 0))
            # web.fetch_url returns a single page
            if "bytes" in data and data.get("bytes", 0) > 0:
                return 1
        except (json.JSONDecodeError, KeyError, AttributeError, TypeError, ValueError) as exc:
            logger.debug("Web result count parse failed: %s", exc)
        return 0

    def _extract_web_data(self, result: ToolCallResult | None) -> dict[str, Any] | None:
        """Unwrap ToolRouter envelopes and return the normalized web payload."""
        if not isinstance(result, ToolCallResult) or not result.ok:
            return None
        try:
            if isinstance(result.output, dict):
                data = result.output
            elif isinstance(result.output, str):
                data = json.loads(result.output)
            else:
                return None
            if isinstance(data.get("result"), dict):
                data = data["result"]
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
            return None

    @staticmethod
    def _compact_web_text(text: str, *, max_len: int = 420) -> str:
        compact = str(text or "")
        compact = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", compact)
        compact = re.sub(r"(?is)<[^>]+>", " ", compact)
        compact = compact.replace("&nbsp;", " ")
        compact = re.sub(r"\s+", " ", compact).strip()
        if len(compact) > max_len:
            compact = compact[:max_len].rstrip() + "…"
        return compact

    def _build_controlled_web_synthesis(
        self,
        *,
        controlled_web: dict[str, Any],
        tool_results: list[ToolCallResult],
    ) -> str | None:
        """Produce a short user-facing synthesis when controlled web succeeded."""
        if not controlled_web.get("triggered") or not controlled_web.get("ok"):
            return None
        if int(controlled_web.get("source_count", 0) or 0) <= 0:
            return None

        tool_name = str(controlled_web.get("tool_name") or "")
        query = str(controlled_web.get("query") or "").strip()
        web_result = next(
            (
                result
                for result in tool_results
                if result.ok and (result.name or "") == tool_name
            ),
            None,
        )
        data = self._extract_web_data(web_result)
        if not data:
            return None

        if tool_name == "research.query":
            results = data.get("results") or []
            if not isinstance(results, list) or not results:
                return None
            ranked = sorted(
                [item for item in results if isinstance(item, dict)],
                key=lambda item: (
                    float(item.get("facts_extracted", 0) or 0),
                    float(item.get("relevance", 0.0) or 0.0),
                ),
                reverse=True,
            )
            highlights: list[str] = []
            for item in ranked[:3]:
                title = str(item.get("title") or "").strip()
                source = str(item.get("source") or "").strip()
                if not title:
                    continue
                highlights.append(f"- {title}" + (f" [{source}]" if source else ""))
            if not highlights:
                return None
            topic = query or str(data.get("query") or "").strip() or "ten temat"
            source_count = int(
                data.get("total_results", controlled_web.get("source_count", 0)) or 0
            )
            intro = f"Przejrzałem {source_count} źródła dla „{topic}”. Najważniejsze, co się przewija:"
            return intro + "\n" + "\n".join(highlights)

        if tool_name == "web.fetch_url" or query.startswith(("http://", "https://")):
            raw_text = str(data.get("text") or "")
            cleaned = self._compact_web_text(raw_text)
            if not cleaned:
                return None
            title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw_text)
            title = (
                self._compact_web_text(title_match.group(1), max_len=120)
                if title_match
                else ""
            )
            url = str(data.get("url") or query or "ten URL").strip()
            if title:
                return f"Sprawdziłem {url}. To strona „{title}”. W skrócie: {cleaned}"
            return f"Sprawdziłem {url}. W skrócie: {cleaned}"

        return None

    @staticmethod
    def _is_freshness_needed(reason_codes: list[str]) -> bool:
        """Determine if the query was freshness-sensitive based on reason codes."""
        freshness_codes = {
            "CURRENT_INFO_REQUIRED",
            "SOURCE_VERIFICATION_NEEDED",
            "FACTUAL_ASSERTION_HIGH_STAKES",
        }
        return bool(freshness_codes & set(reason_codes))

    @staticmethod
    def _classify_grounding_mode(
        *,
        used_fallback: bool,
        tool_calls: list[ToolCallRequest],
        tool_results: list[ToolCallResult],
    ) -> str:
        if used_fallback:
            return "fallback"
        if tool_results and any(r.ok for r in tool_results):
            return "tool_verified"
        if tool_calls or tool_results:
            return "unknown_not_verified"
        return "model_only"

    @staticmethod
    def _user_turn_texts_for_grounding(turn: ChatTurnInput) -> list[str]:
        """Ostatnie wiadomości użytkownika z historii — korpus do grounding clampu."""
        out: list[str] = []
        for m in turn.history or []:
            if getattr(m, "role", None) != "user":
                continue
            c = (getattr(m, "content", None) or "").strip()
            if c:
                out.append(c)
        return out[-12:]

    @staticmethod
    def _has_research_tool(tool_calls: list[ToolCallRequest]) -> bool:
        research_tokens = ("web", "research", "fetch", "browser")
        for call in tool_calls:
            name = (call.name or "").lower()
            if any(token in name for token in research_tokens):
                return True
        return False

    @staticmethod
    def _extract_first_url(message: str) -> str:
        match = re.search(r"https?://[^\s\]\)\}\>\"']+", message or "", re.IGNORECASE)
        return match.group(0) if match else ""

    @staticmethod
    def _has_web_intent(message: str) -> bool:
        t = (message or "").lower()
        return any(
            k in t
            for k in [
                "sprawdź w sieci",
                "sprawdz w sieci",
                "sprawdź online",
                "sprawdz online",
                "wyszukaj",
                "szukaj",
                "internet",
                "web",
                "research",
                "źródł",
                "zrodl",
                "news",
            ]
        )

