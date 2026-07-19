"""TurnOps mixin: DecisionMixin."""
from __future__ import annotations

import aihub.turn._ops_ns as _ops_ns

# Method bodies resolve names via this module's globals (incl. _names).
globals().update({k: v for k, v in vars(_ops_ns).items() if not k.startswith('__')})
TurnOps = None  # late-bound by aihub.turn.ops

class DecisionMixin:
    def _pre_exec_decision_core(
        self,
        *,
        turn: ChatTurnInput,
        ctx: ChatTurnContext,
        psyche_snapshot: dict[str, Any],
        memory_v2_runtime_ctx: Any = None,
        psyche_v2_behavior_ctx: Any = None,
    ) -> dict[str, Any]:
            from aihub.turn.mixins.decision_pre_exec import run_pre_exec_decision_core
            return run_pre_exec_decision_core(self, turn=turn, ctx=ctx, psyche_snapshot=psyche_snapshot, memory_v2_runtime_ctx=memory_v2_runtime_ctx, psyche_v2_behavior_ctx=psyche_v2_behavior_ctx)

    def _finalize_escalation(self, decision_core: dict[str, Any]) -> None:
        """Derive execution_mode / escalation_path from final selected_strategy."""
        from aihub.decision_engine import decide_execution_path

        strat = str(decision_core.get("selected_strategy") or "instant")
        conf = decision_core.get("strategy_confidence")
        merged: dict[str, Any] = dict(
            decision_core.get("selector_output_snapshot") or {}
        )
        merged["strategy"] = strat
        if conf is not None:
            merged["confidence"] = float(conf)
        merged["requires_memory"] = strat in ("contextual", "agentic")
        merged["requires_research"] = strat == "research"
        merged["requires_planning"] = strat == "agentic"
        if not str(merged.get("reason") or "").strip():
            merged["reason"] = str(
                decision_core.get("strategy_short_explanation")
                or decision_core.get("strategy_hints")
                or ""
            )[:400]

        path = decide_execution_path(merged)
        decision_core["strategy_selected"] = merged
        decision_core["execution_mode"] = path["final_mode"]
        decision_core["escalation_path"] = dict(path)
        decision_core["escalation_final_mode"] = path["final_mode"]
        decision_core["escalation_use_reasoning"] = path["use_reasoning"]
        decision_core["escalation_use_tools"] = path["use_tools"]

    @staticmethod
    def _decision_core_trace_escalation(
        decision_core: dict[str, Any],
    ) -> dict[str, Any]:
        """Stable trace slice: strategy + escalation engine output (observability)."""
        out = {
            "strategy_selected": decision_core.get("strategy_selected", {}),
            "execution_mode": decision_core.get("execution_mode"),
            "escalation_path": decision_core.get("escalation_path", {}),
            "escalation_use_reasoning": bool(
                decision_core.get("escalation_use_reasoning", False)
            ),
            "escalation_use_tools": bool(
                decision_core.get("escalation_use_tools", False)
            ),
            "selector_output_snapshot": dict(
                decision_core.get("selector_output_snapshot") or {}
            ),
        }
        if decision_core.get("chat_handoff_evaluated"):
            out["chat_handoff_evaluated"] = True
            if "chat_handoff_executed" in decision_core:
                out["chat_handoff_executed"] = decision_core["chat_handoff_executed"]
            out["chat_handoff_skip_reason"] = decision_core.get(
                "chat_handoff_skip_reason"
            )
        return out

    @staticmethod
    @staticmethod
    def _evaluate_blocker_verdict(
        decision_core: dict[str, Any],
    ) -> BlockerVerdict:
        from aihub.turn.mixins.decision_blocker import run_evaluate_blocker_verdict
        return run_evaluate_blocker_verdict(decision_core)

    @staticmethod
    def _apply_strategy_to_tools(
        tools: list[ProviderToolSpec],
        strategy: str,
        tool_order_hint: list[str] | None = None,
        forced_tool_prefixes: list[str] | None = None,
    ) -> list[ProviderToolSpec]:
        """Restrict available tools to those relevant for the selected strategy."""
        _WHITELIST: dict[str, list[str] | None] = {
            # instant: model-only; lightweight memory reads for grounding only
            "instant": ["memory.search", "memory.get_context"],
            # direct: same light surface as instant; escalation clears tools for meta
            "direct": ["memory.search", "memory.get_context"],
            # contextual: memory-heavy; exclude web/research/planner/agent
            "contextual": [
                "memory.",
                "psyche.",
                "goal.",
                "runtime.status",
                "system.health",
                "image.",
            ],
            # research: web-forward; include memory for context but skip heavy agentic tools
            "research": [
                "research.",
                "web.",
                "memory.search",
                "memory.get_context",
                "goal.",
                "psyche.",
                "runtime.",
                "image.",
            ],
            # agentic: full tool set — no restriction
            "agentic": None,
        }
        whitelist = _WHITELIST.get(strategy)
        if whitelist is None:
            filtered = list(tools)
        else:
            filtered = [t for t in tools if any(t.name.startswith(p) for p in whitelist)]
            # Capability closing: forced prefixes always pierce the strategy whitelist.
            forced = [str(p) for p in (forced_tool_prefixes or []) if str(p).strip()]
            if forced:
                have = {t.name for t in filtered}
                for t in tools:
                    if t.name in have:
                        continue
                    if any(t.name.startswith(p) for p in forced):
                        filtered.append(t)
                        have.add(t.name)
            # Safety: never return an empty tool list — fall back to full set
            if not filtered:
                filtered = list(tools)

        # Cognitive tool-order hint: stable sort by family priority
        if tool_order_hint:
            family_rank = {str(f).lower(): i for i, f in enumerate(tool_order_hint)}

            def _rank(spec: ProviderToolSpec) -> tuple[int, str]:
                name = str(getattr(spec, "name", "") or "")
                fam = name.split(".", 1)[0].lower() if name else ""
                # Map aliases used in cognitive hints
                alias = {
                    "planner": "goal",
                    "reasoning": "runtime",
                    "code": "runtime",
                    "web": "research",
                }.get(fam, fam)
                for key, idx in family_rank.items():
                    if fam == key or alias == key or name.startswith(key + ".") or key in name:
                        return (idx, name)
                return (len(family_rank) + 5, name)

            filtered = sorted(filtered, key=_rank)
        return filtered

    def _should_handoff_to_agent(
        self,
        *,
        decision_core: dict[str, Any],
        message: str,
    ) -> tuple[bool, str]:
        """Determine if chat should handoff to agent runtime (planner+reasoning).

        Returns: (should_handoff, reason_code)

        Criteria (evidence-based from decision_core):
        1. selected_strategy in {research, agentic}
        2. simulation best_action in {research, action} + confidence >= 0.70
        3. active goal with urgency >= 0.7
        4. operational keywords indicating multi-step planning
        """
        strategy = decision_core.get("selected_strategy", "instant")
        handoff_bias = decision_core.get("experience_handoff_bias")

        # Policy feedback handoff bias (from reflection hindsight)
        policy_hoff_bias = float(decision_core.get("policy_handoff_bias") or 0.0)

        # Merge: experience handoff bias + policy feedback bias
        effective_handoff_bias = float(handoff_bias or 0.0) + policy_hoff_bias

        esc_mode = str(decision_core.get("escalation_final_mode") or "direct")

        # Criterion 0 (before experience/planner handoff): web required/optional
        # must stay on chat LLM+tools so Brave/fetch run in chat trace, not only
        # in executive handoff (which does not populate chat tool_results).
        web_decision = decision_core.get("web_decision", "off")
        # Tylko jawna potrzeba webu trzyma wykonanie w czacie (trace narzędzi).
        # agentic + optional bez URL nie blokuje planera — wieloetapowe zadania mogą iść w handoff.
        web_overrides_handoff = strategy == "research" or (
            web_decision == "required" and strategy == "agentic"
        )
        if web_overrides_handoff:
            return (
                False,
                f"web_decision={web_decision}_overrides_handoff(strategy={strategy})",
            )

        # Capability Plan→Execute: explicit execute intent forces handoff.
        if decision_core.get("force_agent_execute") and not web_overrides_handoff:
            if effective_handoff_bias <= -0.25:
                return (
                    False,
                    f"capability_execute_veto_handoff_bias={effective_handoff_bias:.2f}",
                )
            return True, "capability_force_agent_execute"

        # Plan-only asks stay on chat LLM (agentic budget + planner hints) so the
        # user receives a real written plan, not an executive telemetry stub.
        message_lower_early = (message or "").lower()
        exec_force = decision_core.get("force_agent_execute") or any(
            x in message_lower_early
            for x in (
                "wykonaj teraz",
                "wykonaj migracj",
                "zrób migracj",
                "zrob migracj",
                "odpal migracj",
                "zrób to",
                "zrob to",
                "wykonaj plan",
                "zastosuj plan",
                "odpal to",
                "uruchom to",
                "do it",
                "execute now",
                "go ahead",
            )
        )
        plan_only = (not exec_force) and any(
            x in message_lower_early
            for x in (
                "niczego nie wykonuj",
                "nie wykonuj",
                "tylko plan",
                "bez wykonywania",
                "don't execute",
                "do not execute",
                "napisz plan",
                "rozpisz plan",
                "wygeneruj plan",
            )
        )
        if plan_only:
            return False, "plan_only_chat_path"

        # Agentic → executive agent runtime by default (planner+reasoning), unless
        # web/research keeps chat tools, or policy/experience strongly vetoes handoff.
        if strategy == "agentic" and not web_overrides_handoff:
            if effective_handoff_bias <= -0.25:
                return (
                    False,
                    f"agentic_veto_handoff_bias={effective_handoff_bias:.2f}",
                )
            return True, "strategy_agentic_escalation|escalation_final_mode=planner"

        # Experience-driven handoff only when escalation already chose planner
        # (agentic → planner+reasoning). Do not bypass strategy/escalation layer.
        if effective_handoff_bias >= 0.25 and esc_mode == "planner":
            return True, f"effective_handoff_bias={effective_handoff_bias:.2f}"

        # Criterion 1: Escalation engine — planner mode → agent runtime handoff
        if esc_mode == "planner":
            if effective_handoff_bias <= -0.25:
                return False, f"experience_veto_handoff_bias={effective_handoff_bias:.2f}"
            else:
                return True, f"escalation_final_mode=planner(strategy={strategy})"

        # Criterion 1b: experience can veto planner handoff
        if esc_mode == "planner" and effective_handoff_bias <= -0.25:
            veto_reason = f"effective_bias_against_handoff={effective_handoff_bias:.2f}"
        else:
            veto_reason = None

        # Criterion 2: Simulation suggests complex action + high confidence
        if esc_mode == "planner" and decision_core.get("simulation_ran"):
            best_action = decision_core.get("simulation_best_action")
            confidence = decision_core.get("strategy_confidence") or 0.0
            if best_action in {"research", "action"} and confidence >= 0.70:
                return True, f"simulation={best_action}_conf={confidence:.2f}"

        # Criterion 3: High-urgency active goal
        selected_goal = decision_core.get("selected_goal")
        if selected_goal and esc_mode == "planner":
            urgency = float(selected_goal.get("urgency", 0.0))
            if urgency >= 0.7:
                return True, f"goal_urgency={urgency:.2f}"

        # Criterion 4: Multi-step operational keywords (only with planner escalation)
        message_lower = (message or "").lower()
        operational_patterns = [
            "zaplanuj",
            "wykonaj",
            "zrób",
            "sprawdź wszystkie",
            "przeanalizuj całość",
            "znajdź wszystkie",
            "zbadaj szczegółowo",
            "wygeneruj plan",
        ]
        if esc_mode == "planner" and any(
            pattern in message_lower for pattern in operational_patterns
        ):
            return True, "multi_step_operational"

        if veto_reason is not None:
            return False, veto_reason

        return False, "standard_chat_sufficient"

