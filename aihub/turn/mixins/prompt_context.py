"""TurnOps mixin: PromptContextMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound by aihub.turn.ops

class PromptContextMixin:
    def _build_memory_brief(
        self,
        memory_context: dict[str, Any],
        *,
        include_stm: bool = True,
        correction_hints: str = "",
    ) -> str:
        if not isinstance(memory_context, dict):
            return "BRAK DANYCH"

        stm = memory_context.get("stm") or []
        episodic = memory_context.get("episodic") or []
        semantic = memory_context.get("semantic") or []
        dense = memory_context.get("dense_hits") or []
        graph = memory_context.get("graph_hits") or []
        total = int(memory_context.get("total") or 0)

        if (
            total <= 0
            and not episodic
            and not semantic
            and not dense
            and not graph
            and not (include_stm and stm)
        ):
            return "Brak trafień pamięci dla tej wiadomości."

        from aihub.memory_context_pack import (
            is_junk_memory_content,
            memory_contradicts_correction_hints,
        )

        def _line_ok(text: str) -> bool:
            if not text or is_junk_memory_content(text):
                return False
            if memory_contradicts_correction_hints(text, correction_hints):
                return False
            return True

        stm_lines: list[str] = []
        if include_stm:
            for item in stm[-10:]:
                if isinstance(item, dict):
                    role = str(item.get("role") or "")
                    body = str(item.get("content") or "")
                    if _line_ok(body):
                        stm_lines.append(f"- [{role}] {body[:220]}")

        epi_lines = []
        for item in episodic[:2]:
            if isinstance(item, dict):
                body = str(item.get("content", ""))
                if _line_ok(body):
                    epi_lines.append(f"- {body[:180]}")

        sem_lines = []
        for item in semantic[:4]:
            if isinstance(item, dict):
                body = str(item.get("content", ""))
                if _line_ok(body):
                    sem_lines.append(f"- {body[:180]}")

        dense_lines = []
        for item in dense[:3]:
            if isinstance(item, dict):
                body = str(item.get("text", ""))
                if _line_ok(body):
                    dense_lines.append(f"- {body[:160]}")

        graph_lines = []
        for item in graph[:4]:
            if isinstance(item, dict):
                body = str(item.get("content") or item.get("text") or "")
                ntype = str(item.get("type") or "").strip()
                if _line_ok(body):
                    prefix = f"[{ntype}] " if ntype else ""
                    graph_lines.append(f"- {prefix}{body[:160]}")

        if include_stm:
            stm_block = (
                "STM (ostatnia sesja, chronologicznie — najniższy priorytet faktów):\n"
                f"{chr(10).join(stm_lines) if stm_lines else '- brak'}\n"
            )
        else:
            stm_block = (
                "STM: pominięty w skrócie — bieżąca sesja jest w historii wiadomości; "
                "poniżej tylko LTM / retrieval.\n"
            )

        # Priorytet: L2 → vector → graph → episodic → STM.
        return (
            f"total={total}; stm={len(stm)}; episodic={len(episodic)}; semantic={len(semantic)}; "
            f"dense={len(dense)}; graph={len(graph)}\n"
            f"PRIORYTET: najpierw FAKTY (L2), potem VECTOR, potem GRAPH, potem EPISODIC; nie używaj epizodu jeśli "
            f"jest trafienie L2 na to samo pytanie.\n"
            f"Semantic (L2 fakty) top:\n{chr(10).join(sem_lines) if sem_lines else '- brak'}\n"
            f"Dense (vector) top:\n{chr(10).join(dense_lines) if dense_lines else '- brak'}\n"
            f"Graph (knowledge) top:\n{chr(10).join(graph_lines) if graph_lines else '- brak'}\n"
            f"Episodic (L1) top:\n{chr(10).join(epi_lines) if epi_lines else '- brak'}\n"
            f"{stm_block}"
        )

    @staticmethod
    def _build_memory_used_trace(
        memory_context: dict[str, Any],
        *,
        include_stm: bool = True,
        correction_hints: str = "",
    ) -> list[dict[str, Any]]:
        """Observability: snapshot tego, co faktycznie poszło do promptu (STM opcjonalnie)."""
        out: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def _sha1_text(s: str) -> str:
            return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()

        def _add(
            source: str,
            mid: str,
            text: str,
            extra: dict[str, Any] | None = None,
        ) -> None:
            t = (text or "").strip()
            if not t:
                return
            try:
                from aihub.memory_context_pack import (
                    is_junk_memory_content,
                    memory_contradicts_correction_hints,
                )

                if is_junk_memory_content(t):
                    return
                if memory_contradicts_correction_hints(t, correction_hints):
                    return
            except Exception as junk_exc:
                logger.debug("junk memory filter skipped: %s", junk_exc)
            key = (source, mid)
            if key in seen:
                return
            seen.add(key)
            row: dict[str, Any] = {
                "id": mid,
                "text": t[:2000],
                "source": source,
                "used": True,
            }
            if extra:
                row.update(extra)
            out.append(row)

        if not isinstance(memory_context, dict):
            return out

        for m in (memory_context.get("stm") or [])[:6] if include_stm else []:
            if not isinstance(m, dict):
                continue
            raw_id = str(m.get("id") or "").strip()
            content = str(m.get("content") or "")
            mid = raw_id or _sha1_text(content)
            role = str(m.get("role") or "")
            _add("stm", mid, f"[{role}] {content}" if role else content)

        for m in (memory_context.get("semantic") or [])[:6]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("id") or "").strip() or _sha1_text(content)
            _add("L2", mid, content)

        for m in (memory_context.get("dense_hits") or [])[:4]:
            if not isinstance(m, dict):
                continue
            text = str(m.get("text") or "")
            _add("vector", _sha1_text(text), text)

        for m in (memory_context.get("episodic") or [])[:6]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("id") or "").strip() or _sha1_text(content)
            _add("L1", mid, content)

        for m in (memory_context.get("graph_hits") or [])[:4]:
            if not isinstance(m, dict):
                continue
            content = str(m.get("content") or "")
            mid = str(m.get("node_id") or "").strip() or _sha1_text(content)
            _add("graph", mid, content)

        for m in (memory_context.get("memory_v2_items") or [])[:8]:
            if not isinstance(m, dict):
                continue
            title = str(m.get("title") or "")
            content = str(m.get("content") or "")
            combined = f"{title}: {content}".strip(": ").strip() if title else content
            mid = str(m.get("id") or "").strip() or _sha1_text(f"{title}|{content}")
            extra_v2: dict[str, Any] = {}
            for fk in ("is_suppressed", "is_pinned", "is_archived"):
                if fk in m:
                    extra_v2[fk] = bool(m.get(fk))
            _add("memory_v2", mid, combined, extra_v2 if extra_v2 else None)

        return out[:12]

    @staticmethod
    def _augment_memory_observability(
        trace: dict[str, Any],
        memory_used_trace: list[dict[str, Any]] | None,
        memory_context: dict[str, Any] | None = None,
    ) -> None:
        used = list(memory_used_trace or [])
        trace["memory_used_bool"] = bool(used)
        trace["memory_hits"] = len(used)
        sources: list[str] = []
        for row in used:
            src = str(row.get("source") or "").strip()
            if src and src not in sources:
                sources.append(src)
        trace["memory_source"] = sources
        mc = memory_context if isinstance(memory_context, dict) else None
        if mc:
            errs = mc.get("memory_read_errors")
            if isinstance(errs, list) and errs:
                trace["memory_read_errors"] = list(errs)
            ro = mc.get("retrieval_priority_order")
            if isinstance(ro, list) and ro:
                trace["memory_retrieval_priority_order"] = list(ro)
            pack = mc.get("context_pack")
            if isinstance(pack, dict):
                trace["memory_context_pack_selected_ids"] = list(pack.get("selected_ids") or [])
                trace["memory_context_pack_used_chars"] = int(pack.get("used_chars") or 0)
                trace["memory_context_pack_source_distribution"] = dict(pack.get("source_distribution") or {})
                trace["memory_context_pack_injected"] = bool(pack.get("selected_ids"))

    @staticmethod
    def _correction_trace_flat(
        corr: dict[str, Any] | None, *, hints_chars: int = 0
    ) -> dict[str, Any]:
        t = corr if isinstance(corr, dict) else {}
        return {
            "user_correction_recorded": bool(t.get("recorded")),
            "user_correction_kind": t.get("kind"),
            "user_correction_durable_marked": bool(t.get("durable")),
            "correction_hints_in_prompt_chars": int(hints_chars),
        }

    @staticmethod
    def _correction_trace_fields(ctx: ChatTurnContext | None) -> dict[str, Any]:
        if not ctx:
            return {}
        ct = ctx.system_context.get("correction_turn_trace")
        if not isinstance(ct, dict):
            ct = {}
        hints = str(ctx.system_context.get("correction_hints_text") or "")
        return TurnOps._correction_trace_flat(ct, hints_chars=len(hints))

    @staticmethod
    def _effective_attached_file_ids(turn: ChatTurnInput) -> list[str]:
        """ID załączników z żądania lub (fallback) ostatnie pliki sesji przy odwołaniach wskazujących."""
        raw = [
            str(x).strip()
            for x in (turn.attached_file_ids or [])
            if str(x).strip()
        ][:MAX_FILES_PER_TURN]
        if raw:
            return raw
        msg = (turn.message or "").strip()
        if not msg or _SESSION_ATTACHMENT_DEICTIC_RE.search(msg) is None:
            return []
        return fetch_recent_session_attachment_ids(
            user_id=turn.user_id,
            session_id=turn.session_id,
            limit=MAX_FILES_PER_TURN,
        )

    def _build_psyche_brief(self, psyche_state: dict[str, Any]) -> str:
        compact = self._compact_psyche_state(psyche_state)
        if not compact:
            return "BRAK DANYCH"

        mood = compact.get("mood")
        energy = compact.get("energy")
        focus = compact.get("focus")
        style = compact.get("style")
        traits = compact.get("traits") or {}

        directness = traits.get("directness", "BRAK DANYCH")

        # NOTE (06.07 response-quality fix): we intentionally no longer inject raw sarcasm/swearing
        # trait values into the prompt. Surfacing them pushed the model toward theatrical, personified
        # replies. Psyche now only hints at *tone modulation* (directness / brevity / warmth); it must
        # never justify fake biography, aggression, or mirroring the user's hostile tone.
        return (
            f"[Psyche V1 — snapshot stanu] style={style}, mood={mood}, energy={energy}, "
            f"focus={focus}, directness={directness}. "
            "To tylko kompaktowy label stanu (mood/energy) — nie polityka zachowania. "
            "Gdy dostępne są wskazówki Psyche V2, one mają pierwszeństwo nad tym snapshotem. "
            "Nigdy nie zmieniaj faktów, nie personifikuj się i nie kopiuj agresywnego tonu użytkownika."
        )

    def _build_system_prompt(
        self,
        ctx: ChatTurnContext,
        *,
        memory_brief: str,
        psyche_brief: str,
        decision_hints: str = "",
        correction_hints: str = "",
        memory_v2_context=None,
        psyche_v2_context=None,
        files_context: str = "",
        first_turn_in_thread: bool,
        history_rollup: str | None = None,
        listing_sales_boost: bool = False,
    ) -> str:
            from aihub.turn.mixins.prompt_system import run_build_system_prompt
            return run_build_system_prompt(self, ctx=ctx, memory_brief=memory_brief, psyche_brief=psyche_brief, decision_hints=decision_hints, correction_hints=correction_hints, memory_v2_context=memory_v2_context, psyche_v2_context=psyche_v2_context, files_context=files_context, first_turn_in_thread=first_turn_in_thread, history_rollup=history_rollup, listing_sales_boost=listing_sales_boost)

    @staticmethod
    def _local_non_research_guardrails(
        turn: ChatTurnInput, decision_core: dict[str, Any]
    ) -> None:
        """Keep local copy/edit tasks out of planner/research drift.

        This runs after selector/policy/simulation layers and acts as the final
        truth-preserving clamp for simple local tasks.
        """
        msg = str(turn.message or "")
        hist = list(turn.history or [])
        # Use the same effective attachment set as the prompt/attachment pipeline
        # (request IDs + recent session uploads), not only the current request field.
        has_attachments = bool(PromptContextMixin._effective_attached_file_ids(turn))
        listing_local = listing_copy_no_web_intent(msg) and "://" not in msg
        followup_local = short_followup_no_web_intent(msg, hist)
        # Word-boundary matching: naive substring match forced web for messages like
        # "co WIDZISZ na obrazku" (contains "dzis") — a false freshness trigger.
        from aihub.strategy_selector import _keyword_in_text, _strip_diacritics

        lower = msg.lower()
        ascii_l = _strip_diacritics(lower)

        from aihub.strategy_selector import (
            is_assistant_meta_ask,
            meta_ask_refers_to_prior_conversation,
        )

        if is_assistant_meta_ask(msg):
            decision_core["selected_strategy"] = (
                "direct"
                if meta_ask_refers_to_prior_conversation(msg)
                else "instant"
            )
            decision_core["web_decision"] = "off"
            decision_core["web_decision_reason"] = "assistant_meta_guardrail"

            for code in (
                "META_ASK_LIGHTWEIGHT_PATH",
                "META_ASK_MEMORY_SKIPPED",
                "META_ASK_GOAL_SKIPPED",
                "META_ASK_HEAVY_STAGES_SKIPPED",
            ):
                if code not in decision_core["reason_codes"]:
                    decision_core["reason_codes"].append(code)

            return

        freshness_needed = any(
            _keyword_in_text(tok, lower, ascii_l) for tok in WEB_REQUIRED_QUERY_KEYWORDS
        )
        # Pragmatics may have demoted false research (deixis like „ten wczorajszy…”) —
        # do not re-force web via substring freshness keywords.
        reason_codes = list(decision_core.get("reason_codes") or [])
        pragmatics_blocks_freshness = (
            "PRAGMATICS_CONTEXT_REQUIRED" in reason_codes
            and "PRAGMATICS_WEB_QUERY_REWRITE" not in reason_codes
            and str(decision_core.get("web_decision_reason") or "").startswith("pragmatics_")
        ) or (
            str(decision_core.get("web_decision_reason") or "")
            == "pragmatics_demote_research_to_contextual"
        )
        # Also block when temporal keyword was only a morphological substring
        # (wczoraj ⊂ wczorajszy) without real sports/news research intent.
        if freshness_needed and not has_attachments and not pragmatics_blocks_freshness:
            # Strengthen: if deixis follow-up (“ten/ta/to …”) do not force web.
            if re.search(r"(?iu)\b(ten|ta|to|tego|tamten)\b", lower) and not re.search(
                r"(?iu)\b(mecz|wynik|liga|news|cena|kurs)\b", lower
            ):
                freshness_needed = False
        from aihub.strategy_selector import (
            _IMPERATIVE_NOW_NO_WEB,
            _LOCAL_INFRA_NO_WEB,
            _explicit_check_intent,
            local_howto_no_web_intent,
        )
        from aihub.turn.prompt_budget import (
            looks_correction,
            looks_procedural,
            looks_remember,
        )

        if _LOCAL_INFRA_NO_WEB.search(msg) or local_howto_no_web_intent(msg):
            freshness_needed = False
        # Durable preference / procedure / remember writes are not web lookups.
        if looks_procedural(msg) or looks_correction(msg) or looks_remember(msg):
            freshness_needed = False
        # "sprawdzenie" noun / local howto must not trip sprawdz* freshness.
        if freshness_needed and not _explicit_check_intent(lower, ascii_l):
            # Drop bare check-stem hits that word-boundary already excluded; keep other markers.
            check_only = any(
                _keyword_in_text(tok, lower, ascii_l)
                for tok in ("sprawdź", "sprawdz", "zbadaj")
            )
            other = any(
                _keyword_in_text(tok, lower, ascii_l)
                for tok in WEB_REQUIRED_QUERY_KEYWORDS
                if tok not in {"sprawdź", "sprawdz", "zbadaj"}
            )
            if check_only and not other:
                freshness_needed = False
        # Imperative "wykonaj teraz" is not a freshness query unless another marker remains.
        if freshness_needed and _IMPERATIVE_NOW_NO_WEB.search(msg):
            other = any(
                _keyword_in_text(tok, lower, ascii_l)
                for tok in WEB_REQUIRED_QUERY_KEYWORDS
                if tok != "teraz"
            )
            if not other:
                freshness_needed = False
        # An attached image/file makes the turn about that attachment; do not force web
        # research on top of it (the vision/description path must win).
        if freshness_needed and not has_attachments and not pragmatics_blocks_freshness:
            decision_core["selected_strategy"] = "research"
            decision_core["web_decision"] = "required"
            decision_core["web_decision_reason"] = "freshness_guardrail"
            if "CURRENT_INFO_REQUIRED" not in decision_core["reason_codes"]:
                decision_core["reason_codes"].append("CURRENT_INFO_REQUIRED")
            from aihub.strategy_selector import research_trigger_reason_codes

            for code in research_trigger_reason_codes(msg):
                if code not in decision_core["reason_codes"]:
                    decision_core["reason_codes"].append(code)
            return
        if listing_local or followup_local:
            decision_core["selected_strategy"] = "contextual" if hist else "instant"
            decision_core["web_decision"] = "off"
            decision_core["web_decision_reason"] = (
                "listing_copy_local_guardrail"
                if listing_local
                else "short_followup_local_guardrail"
            )

    @staticmethod
    def _is_capability_question(message: str) -> bool:
        m = (message or "").lower()
        return any(
            k in m
            for k in [
                "capabil",
                "narzędzi",
                "narzedzi",
                "jakie możesz",
                "jakie mozesz",
                "co potrafisz",
                "do czego masz dostęp",
                "do czego masz dostep",
            ]
        )

    @staticmethod
    def _is_trace_status_question(message: str) -> bool:
        m = (message or "").lower()
        return any(
            k in m
            for k in [
                "fallback",
                "provider",
                "providera",
                "realnego providera",
                "normalnego tora",
                "normalnego toru",
            ]
        )

    @staticmethod
    def _has_unverified_tool_claim(text: str) -> bool:
        t = (text or "").lower()
        claim_patterns = [
            r"\bsprawdził(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bwyszukał(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\buruchomił(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bpobrał(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"\bzweryfikował(?:em|am|o|eś|aś|śmy|liśmy)?\b",
            r"korzystam teraz z realnych narzędzi",
            r"korzystam teraz z realnych narzedzi",
        ]
        return any(re.search(p, t) for p in claim_patterns)

    @staticmethod
    def _rewrite_unverified_claims(text: str) -> str:
        rewrites = [
            (r"\bsprawdził(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę sprawdzić"),
            (r"\bwyszukał(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę wyszukać"),
            (r"\buruchomił(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę uruchomić"),
            (r"\bpobrał(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę pobrać"),
            (r"\bzweryfikował(?:em|am|o|eś|aś|śmy|liśmy)?\b", "mogę zweryfikować"),
            (
                r"korzystam teraz z realnych narzędzi|korzystam teraz z realnych narzedzi",
                "mam dostęp do narzędzi, ale w tej odpowiedzi ich nie uruchamiałem",
            ),
        ]
        out = text or ""
        for pattern, replacement in rewrites:
            out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
        return out

    @staticmethod
    def _infer_intent(
        message: str,
        tool_calls: list[ToolCallRequest],
    ) -> str:
        names = [(call.name or "").lower() for call in tool_calls]
        if any(name.startswith("memory.") for name in names):
            return "memory"
        if any(name.startswith("psyche.") for name in names):
            return "psyche"
        if any(
            name.startswith(prefix)
            for prefix in ("web.", "research.", "browser.", "internet.")
            for name in names
        ):
            return "research"
        if any(
            name.startswith("planner.") or name.startswith("goal.") for name in names
        ):
            return "plan"

        text = (message or "").lower()
        if any(
            k in text
            for k in ["research", "wyszuk", "szukaj", "http", "url", "artykuł"]
        ):
            return "research"
        if any(k in text for k in ["plan", "cel", "strateg", "roadmap", "task"]):
            return "plan"
        if any(
            k in text for k in ["naucz", "zapamiętaj", "zapamietaj", "learn", "note"]
        ):
            return "learn"
        if any(
            k in text
            for k in ["zrób", "wykonaj", "stwórz", "stwor", "deploy", "uruchom"]
        ):
            return "action"
        return "query"

    @staticmethod
    def _compact_psyche_state(state: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(state, dict):
            return {}
        allowed = [
            "user_id",
            "mood",
            "energy",
            "focus",
            "style",
            "temperature",
            "traits",
            "updated_at",
        ]
        return {key: state.get(key) for key in allowed if key in state}

    def _build_context(
        self,
        turn: ChatTurnInput,
        *,
        correction_turn_trace: dict[str, Any],
    ) -> ChatTurnContext:
        mode = turn.mode or CHAT_DEFAULT_MODE
        hints = build_correction_hints_for_prompt(turn.user_id, turn.session_id)
        from aihub.strategy_selector import (
            is_assistant_meta_ask,
            meta_ask_refers_to_prior_conversation,
        )
        from aihub.turn.prompt_budget import is_casual_smalltalk

        pure_meta = is_assistant_meta_ask(turn.message or "") and not meta_ask_refers_to_prior_conversation(
            turn.message or ""
        )
        meta_ask = is_assistant_meta_ask(turn.message or "")
        casual = is_casual_smalltalk(turn.message or "")
        system_context: dict[str, Any] = {
            "tool_calling_enabled": LLM_TOOL_CALLING_ENABLED,
            "streaming_enabled": LLM_STREAMING_ENABLED,
            "correction_turn_trace": correction_turn_trace,
            "correction_hints_text": hints,
            "assistant_meta_ask": meta_ask,
            "assistant_meta_ask_pure": pure_meta,
            "casual_smalltalk": casual,
        }
        if pure_meta or casual or meta_ask:
            # Meta (incl. prior-ref) and casual: zero vector / Memory V2 pack.
            # Prior-ref continuity comes from clipped chat history, not retrieval.
            mem_ctx: dict[str, Any] = {
                "stm": [],
                "semantic": [],
                "episodic": [],
                "dense_hits": [],
                "graph_hits": [],
                "memory_v2_items": [],
                "total": 0,
                "memory_lookup_skipped": True,
                "memory_lookup_happened": False,
                "meta_ask_lightweight": bool(meta_ask),
                "casual_light_lightweight": bool(casual),
            }
            system_context["memory_context_pack"] = {
                "selected_ids": [],
                "used_chars": 0,
                "source_distribution": {},
            }
            system_context["memory_context_pack_prompt"] = ""
            system_context["memory_context_pack_trace"] = {
                "selected_count": 0,
                "used_chars": 0,
                "skipped": (
                    "CASUAL_LIGHT_MEMORY_SKIPPED"
                    if casual and not meta_ask
                    else "META_ASK_MEMORY_SKIPPED"
                ),
            }
            if meta_ask:
                system_context["META_ASK_MEMORY_SKIPPED"] = True
            if casual:
                system_context["CASUAL_LIGHT_MEMORY_SKIPPED"] = True
        else:
            mem_ctx = retrieve_context(turn.user_id, turn.message, limit=6)
            try:
                from aihub.memory_core import get_memory_core

                pack_limit = 4 if meta_ask else 6
                pack_chars = 1200 if meta_ask else 1800
                pack = get_memory_core().build_context_pack(
                    turn.user_id,
                    turn.message,
                    limit=pack_limit,
                    max_chars=pack_chars,
                    include_graph=not meta_ask,
                    correction_hints=hints,
                )
                pack_dump = pack.model_dump(mode="json")
                pack_prompt = pack.to_prompt_text(max_chars=pack_chars)
                system_context["memory_context_pack"] = pack_dump
                system_context["memory_context_pack_prompt"] = pack_prompt
                system_context["memory_context_pack_trace"] = pack.to_trace_summary()
                if isinstance(mem_ctx, dict):
                    mem_ctx["context_pack"] = pack_dump
                    mem_ctx["context_pack_selected_ids"] = list(pack.selected_ids)
                    mem_ctx["context_pack_source_distribution"] = dict(pack.source_distribution)
                    mem_ctx["context_pack_used_chars"] = int(pack.used_chars)
                    # Cap raw retrieve buckets so memory_hits cannot explode with STM dumps.
                    for key, lim in (
                        ("stm", 4 if meta_ask else 6),
                        ("semantic", 4),
                        ("episodic", 4),
                        ("dense_hits", 3),
                        ("graph_hits", 0 if meta_ask else 4),
                        ("memory_v2_items", 4 if meta_ask else 6),
                    ):
                        bucket = mem_ctx.get(key)
                        if isinstance(bucket, list) and len(bucket) > lim:
                            mem_ctx[key] = bucket[:lim]
                    if meta_ask:
                        mem_ctx["stm"] = []
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory_context_pack_build_failed: %s", exc, exc_info=True)
                if isinstance(mem_ctx, dict):
                    mem_ctx.setdefault("memory_read_errors", []).append({
                        "source": "context_pack",
                        "error": str(exc)[:500],
                    })
                system_context["memory_context_pack_error"] = str(exc)[:500]
        capabilities = self._tool_registry.list_capabilities(
            mode=mode,
            include_debug=bool(turn.include_debug),
            policy_overrides=dict(turn.tool_policy_overrides or {}),
        )
        return ChatTurnContext(
            user_id=turn.user_id,
            session_id=turn.session_id,
            mode=mode,
            include_debug=turn.include_debug,
            memory_context=mem_ctx,
            system_context=system_context,
            capabilities=capabilities,
        )

    def _build_provider_tools(self, ctx: ChatTurnContext) -> list[ProviderToolSpec]:
        if not LLM_TOOL_CALLING_ENABLED:
            return []
        return [
            ProviderToolSpec(
                name=c.name,
                description=c.description,
                input_schema=c.input_schema,
            )
            for c in ctx.capabilities
        ]

    @staticmethod
    def _debug_context_payload(turn: ChatTurnInput, ctx: ChatTurnContext) -> dict[str, Any] | None:
        """Operator-only debug blob. Never used as response_text."""
        if not turn.include_debug:
            return None
        from aihub.response_runtime_guard import debug_context_dump

        dump = debug_context_dump(ctx)
        return {"context": dump} if dump is not None else None

    @staticmethod
    def _sse_tool_display_name(name: str) -> str:
        n = (name or "").strip()
        if len(n) > 56:
            return f"{n[:53]}…"
        return n

    @staticmethod
    def _neutral_final_behavior_profile(*, mode: str = "neutral") -> dict[str, Any]:
        return {
            "mode": mode,
            "directness": 0.5,
            "verbosity": 0.5,
            "caution": 0.5,
            "pressure": 0.5,
            "trust": 0.5,
            "friction": 0.5,
            "warmth": 0.5,
            "autonomy": 0.5,
            "structuredness": 0.5,
            "tool_bias": 0.5,
            "web_bias": 0.5,
            "reassurance": 0.5,
        }

    def _final_behavior_trace_fields(
        self, psyche_v2_behavior_ctx: Any
    ) -> dict[str, Any]:
        """Spójne z główną ścieżką LLM: ``final_behavior_profile`` + ``psyche_v2_style_mode``."""
        final_behavior_profile = self._neutral_final_behavior_profile()
        psyche_v2_style_mode = "neutral"
        if psyche_v2_behavior_ctx and getattr(psyche_v2_behavior_ctx, "loaded", False):
            psyche_v2_style_mode = psyche_v2_behavior_ctx.mode
            final_behavior_profile = {
                "mode": psyche_v2_style_mode,
                "directness": psyche_v2_behavior_ctx.directness_bias,
                "verbosity": psyche_v2_behavior_ctx.verbosity_bias,
                "caution": psyche_v2_behavior_ctx.caution_bias,
                "pressure": psyche_v2_behavior_ctx.pressure,
                "trust": psyche_v2_behavior_ctx.trust,
                "friction": psyche_v2_behavior_ctx.friction,
                "warmth": psyche_v2_behavior_ctx.warmth,
                "autonomy": psyche_v2_behavior_ctx.autonomy_bias,
                "structuredness": psyche_v2_behavior_ctx.structuredness_bias,
                "tool_bias": psyche_v2_behavior_ctx.tool_bias,
                "web_bias": psyche_v2_behavior_ctx.web_bias,
                "reassurance": psyche_v2_behavior_ctx.reassurance_bias,
            }
        return {
            "final_behavior_profile": final_behavior_profile,
            "psyche_v2_style_mode": psyche_v2_style_mode,
            "psyche_v2_behavior_applied": bool(
                psyche_v2_behavior_ctx and getattr(psyche_v2_behavior_ctx, "loaded", False)
            ),
        }

    @staticmethod
    def _apply_persona_guard(turn: ChatTurnInput, res: ChatTurnResult) -> None:
        """Safety net: trim first-person personification leakage from a real model answer.

        Applies ONLY to free-text model responses — never to deterministic/vault/memory-fact replies
        (those are factual recall and must pass through verbatim). It also never overwrites a
        substantive answer with the dry fallback: :func:`sanitize_persona_leakage` only returns the
        fallback when the WHOLE reply was leakage (i.e. the model didn't actually answer).
        """
        try:
            if not (res and res.ok and res.response_text):
                return
            if (res.model or "") == "deterministic" or (res.provider or "") == "aihub":
                return
            gmode = str((res.trace or {}).get("response_grounding_mode") or "")
            if gmode.startswith("deterministic") or gmode == "fallback":
                return
            cleaned, changed = sanitize_persona_leakage(
                res.response_text, user_message=turn.message
            )
            cot_cleaned, cot_changed = strip_reasoning_leak(cleaned)
            if cot_changed:
                cleaned = cot_cleaned
                changed = True
                if isinstance(res.trace, dict):
                    res.trace["reasoning_leak_sanitized"] = True
            prov_cleaned, prov_changed = sanitize_false_provider_identity(
                cleaned,
                user_message=turn.message or "",
                final_provider=getattr(res, "provider", None),
                final_model=getattr(res, "model", None),
            )
            if prov_changed:
                cleaned = prov_cleaned
                changed = True
                if isinstance(res.trace, dict):
                    res.trace["false_provider_identity_sanitized"] = True
            if changed:
                dry_marker = "model nie oddał treści"
                if cleaned and dry_marker in cleaned.lower():
                    web_fallback = TurnOps._web_synthesis_from_tool_results(res)
                    if web_fallback:
                        cleaned = web_fallback
                    else:
                        res.ok = False
                        if isinstance(res.trace, dict):
                            res.trace["persona_guard_empty_after_sanitize"] = True
                elif not cleaned:
                    web_fallback = TurnOps._web_synthesis_from_tool_results(res)
                    if web_fallback:
                        cleaned = web_fallback
                    else:
                        cleaned = dry_fallback_response(user_message=turn.message)
                        res.ok = False
                        if isinstance(res.trace, dict):
                            res.trace["reasoning_leak_empty_after_sanitize"] = True
                res.response_text = cleaned
                if isinstance(res.trace, dict):
                    res.trace["persona_leakage_sanitized"] = True
        except Exception:  # noqa: BLE001
            logger.debug("persona guard skipped", exc_info=True)

    @staticmethod
    def _web_synthesis_from_tool_results(res: ChatTurnResult) -> str | None:
        """Build a short grounded answer when persona guard would drop the whole LLM reply."""
        trace = res.trace if isinstance(res.trace, dict) else {}
        controlled_web = trace.get("controlled_web") if isinstance(trace.get("controlled_web"), dict) else {}
        if not controlled_web.get("triggered") and not trace.get("web_used"):
            for tr in res.tool_results or []:
                if tr.ok and (tr.name or "") in {"research.query", "web.fetch_url"}:
                    controlled_web = {
                        "triggered": True,
                        "ok": True,
                        "tool_name": tr.name,
                        "query": "",
                        "source_count": 1,
                    }
                    break
            else:
                return None
        return TurnOps._build_controlled_web_synthesis(
            controlled_web=controlled_web,
            tool_results=res.tool_results or [],
        )

