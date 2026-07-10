#!/usr/bin/env python3

"""
ETAP 9A comprehensive tests: Embedding, StrategySelector, ExperienceMemory, Telemetry.
"""

from __future__ import annotations

import os
from typing import Any
from unittest import mock

import pytest

from aihub.db import get_experiences_by_user, init_db, write_experience
from aihub.embedding_engine import (
    EmbeddingError,
    embed_document,
    embed_query,
    healthcheck,
)
from aihub.executive_controller import get_executive_controller
from aihub.strategy_selector import REASON_CODES, select_strategy

# ============================================================================
# EMBEDDING ENGINE TESTS
# ============================================================================


class TestEmbeddingEngine:
    """Tests for Voyage API + fallback embedding provider."""

    @pytest.fixture(autouse=True)
    def _reset_embedding_state(self):
        """Reset embedding engine state before each test."""
        from aihub.embedding_engine import reset_providers

        reset_providers()
        yield
        reset_providers()

    def test_embed_query_returns_valid_response(self, monkeypatch) -> None:
        """embed_query returns EmbeddingResponse from a REAL semantic provider.

        The shared ``tests/conftest.py`` sets ``AIHUB_DISABLE_REMOTE_EMBEDDINGS=1`` and
        ``AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK=1`` so the bulk of the suite stays hermetic and
        fast by using ``deterministic-hash`` vectors (no model load, no network). That short-circuit
        (``embedding_engine._get_vector_with_fallback``) would otherwise make this specific
        assertion — "the provider is a real semantic embedder" — impossible to satisfy.

        This test therefore explicitly opts *out* of the deterministic short-circuit so it exercises
        the genuine provider path (Voyage if keyed, else local sentence-transformers). The original
        skip-on-unavailable contract is preserved: if no real provider is installed/loadable the
        engine raises ``EmbeddingError`` and the test skips rather than passing on a fake vector.
        """
        from aihub.embedding_engine import reset_providers

        monkeypatch.setenv("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK", "0")
        reset_providers()
        try:
            response = embed_query("What is the capital of France?")
            assert response.vector is not None
            assert isinstance(response.vector, list)
            assert len(response.vector) > 0
            assert response.input_type == "query"
            assert response.provider in {"voyage", "sentence-transformers"}
            assert response.output_dimension > 0
            assert response.embedding_fallback_used is False
        except EmbeddingError as e:
            pytest.skip(f"Embedding provider not available: {e}")

    def test_embed_document_uses_document_input_type(self) -> None:
        """embed_document uses input_type=document."""
        try:
            response = embed_document("This is a document about AI.")
            assert response.input_type == "document"
            assert response.vector is not None
        except EmbeddingError as e:
            pytest.skip(f"Embedding provider not available: {e}")

    def test_embed_rejects_empty_text(self) -> None:
        """embed_query/document raise error on empty text."""
        from aihub.embedding_engine import reset_providers

        reset_providers()

        error_caught = False
        try:
            embed_query("")
            pytest.fail("embed_query('') should have raised EmbeddingError")
        except Exception as e:
            if type(e).__name__ == "EmbeddingError" and "empty" in str(e).lower():
                error_caught = True
            else:
                raise

        assert error_caught, "EmbeddingError with 'empty' was not caught"

        error_caught = False
        try:
            embed_document("")
            pytest.fail("embed_document('') should have raised EmbeddingError")
        except Exception as e:
            if type(e).__name__ == "EmbeddingError" and "empty" in str(e).lower():
                error_caught = True
            else:
                raise

        assert error_caught, "EmbeddingError with 'empty' was not caught"

    def test_content_hash_deterministic(self) -> None:
        """Content hash is deterministic."""
        try:
            resp1 = embed_query("Deterministic hash test")
            resp2 = embed_query("Deterministic hash test")
            assert resp1.content_hash == resp2.content_hash
        except EmbeddingError as e:
            pytest.skip(f"Embedding provider not available: {e}")

    def test_healthcheck_returns_config(self) -> None:
        """healthcheck returns provider and config details."""
        with mock.patch.dict(os.environ, {"EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0"}):
            health = healthcheck()
        assert "provider" in health
        assert "model" in health
        assert "output_dimension" in health
        assert "timeout_seconds" in health
        assert "max_retries" in health
        assert "will_attempt_voyage_first" in health
        assert "st_only_expected" in health
        assert "runtime_st_produces_embedding" in health
        assert "output_dimension_semantics" in health
        assert "voyage_request_output_dimension" in health


# ============================================================================
# STRATEGY SELECTOR TESTS
# ============================================================================


class TestStrategySelector:
    """Tests for pre-routing strategy classification."""

    def test_select_strategy_instant_for_simple_query(self) -> None:
        """Simple greeting returns instant strategy."""
        selection = select_strategy(
            user_id="test_user",
            user_text="Hello",
            mode="run",
        )
        assert selection.selected_strategy in {
            "instant",
            "contextual",
        }  # May depend on memory
        assert len(selection.reason_codes) > 0

    def test_select_strategy_research_for_url_query(self) -> None:
        """Query with URL triggers research strategy."""
        selection = select_strategy(
            user_id="test_user",
            user_text="Check this link: https://example.com",
            mode="run",
        )
        assert selection.research_needed is True
        assert (
            "URL_ANALYSIS_REQUIRED" in selection.reason_codes
            or selection.selected_strategy == "research"
        )

    def test_select_strategy_agentic_with_active_goals(self) -> None:
        """Active goals trigger agentic strategy."""
        selection = select_strategy(
            user_id="test_user",
            user_text="Execute the plan",
            mode="run",
            active_goals_summary={"active_count": 1, "max_urgency": 0.9},
        )
        assert selection.agentic_recommended is True
        assert "ACTIVE_GOAL_PRESENT" in selection.reason_codes

    def test_strategy_selection_has_confidence(self) -> None:
        """Strategy selection includes confidence score."""
        selection = select_strategy(
            user_id="test_user",
            user_text="Test query",
            mode="run",
        )
        assert selection.confidence is not None
        assert 0.0 <= selection.confidence <= 1.0

    def test_strategy_selection_has_reason_codes(self) -> None:
        """All reason codes are from stable set."""
        selection = select_strategy(
            user_id="test_user",
            user_text="Test",
            mode="run",
        )
        for code in selection.reason_codes:
            assert code in REASON_CODES, f"Unknown reason code: {code}"

    def test_strategy_selection_degraded_when_retrieval_fails(self) -> None:
        """Degraded flag set when retrieval fails."""
        # This test assumes memory retrieval might fail in some scenarios
        selection = select_strategy(
            user_id="",  # Empty user may cause issues
            user_text="Test",
            mode="run",
        )
        # Should still return valid selection even if degraded
        assert selection.selected_strategy is not None
        assert isinstance(selection.degraded, bool)


# ============================================================================
# EXPERIENCE MEMORY TESTS
# ============================================================================


class TestExperienceMemory:
    """Tests for semantic experience storage."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path: Any) -> None:
        """Initialize test database."""
        # Use the standard init_db function - it handles SQLite setup automatically
        init_db()

    def test_write_experience_creates_record(self) -> None:
        """write_experience creates database record."""
        success = write_experience(
            experience_id="exp-001",
            user_id="test_user",
            user_input_summary="Test experience",
            selected_strategy="contextual",
            reason_codes=["MEMORY_CONTINUATION"],
            tools_needed=False,
            tools_executed=False,
            research_needed=False,
            research_executed=False,
            planner_recommended=False,
            planner_executed=False,
            agentic_recommended=False,
            agentic_executed=False,
            outcome_type="success",
            success=True,
            content_hash="abc123",
            embedding_provider="voyage",
            embedding_model="voyage-4-large",
            embedding_dimension=1024,
            embedding_input_type="document",
        )
        assert success is True

    def test_write_experience_idempotent_by_content_hash(self) -> None:
        """Second write with same content_hash is skipped."""
        content_hash = "duplicate-hash"
        write_experience(
            experience_id="exp-001",
            user_id="test_user",
            user_input_summary="First entry",
            selected_strategy="instant",
            reason_codes=[],
            outcome_type="success",
            success=True,
            tools_needed=False,
            tools_executed=False,
            research_needed=False,
            research_executed=False,
            planner_recommended=False,
            planner_executed=False,
            agentic_recommended=False,
            agentic_executed=False,
            content_hash=content_hash,
            embedding_provider="voyage",
            embedding_model="voyage-4-large",
            embedding_dimension=1024,
            embedding_input_type="document",
        )

        result2 = write_experience(
            experience_id="exp-002",
            user_id="test_user",
            user_input_summary="Second entry",
            selected_strategy="instant",
            reason_codes=[],
            outcome_type="success",
            success=True,
            tools_needed=False,
            tools_executed=False,
            research_needed=False,
            research_executed=False,
            planner_recommended=False,
            planner_executed=False,
            agentic_recommended=False,
            agentic_executed=False,
            content_hash=content_hash,  # Same hash
            embedding_provider="voyage",
            embedding_model="voyage-4-large",
            embedding_dimension=1024,
            embedding_input_type="document",
        )
        assert result2 is False  # Duplicate skipped

    def test_read_experiences_by_user(self) -> None:
        """get_experiences_by_user retrieves stored records."""
        write_experience(
            experience_id="exp-read-001",
            user_id="reader_user",
            user_input_summary="Read test",
            selected_strategy="research",
            reason_codes=["CURRENT_INFO_REQUIRED"],
            tools_needed=False,
            tools_executed=False,
            research_needed=True,
            research_executed=False,
            planner_recommended=False,
            planner_executed=False,
            agentic_recommended=False,
            agentic_executed=False,
            outcome_type="success",
            success=True,
            content_hash="read-hash",
            embedding_provider="voyage",
            embedding_model="voyage-4-large",
            embedding_dimension=1024,
            embedding_input_type="document",
        )

        records = get_experiences_by_user("reader_user", limit=10)
        assert len(records) >= 1
        found = next((r for r in records if r["experience_id"] == "exp-read-001"), None)
        assert found is not None
        assert found["selected_strategy"] == "research"
        assert found["research_needed"] is True

    def test_experience_truthfulness_fields(self) -> None:
        """Experience distinguishes needed vs executed."""
        write_experience(
            experience_id="exp-truth-001",
            user_id="truth_user",
            user_input_summary="Tool decision",
            selected_strategy="agentic",
            reason_codes=["MULTI_STEP_TASK"],
            tools_needed=True,
            tools_executed=False,  # Different from needed!
            research_needed=True,
            research_executed=True,  # Same
            planner_recommended=True,
            planner_executed=False,  # Different from recommended!
            agentic_recommended=True,
            agentic_executed=True,
            outcome_type="failure",
            success=False,
            content_hash="truth-hash",
            embedding_provider="voyage",
            embedding_model="voyage-4-large",
            embedding_dimension=1024,
            embedding_input_type="document",
        )

        records = get_experiences_by_user("truth_user", limit=10)
        record = records[0]
        assert record["tools_needed"] is True
        assert record["tools_executed"] is False
        assert record["tools_needed"] != record["tools_executed"]
        assert record["research_executed"] is True
        assert record["planner_recommended"] is True
        assert record["planner_executed"] is False


# ============================================================================
# TRACE / TELEMETRY INTEGRATION TESTS
# ============================================================================


class TestTraceExtension:
    """Tests for extended trace payload with ETAP 9A fields."""

    @pytest.mark.anyio
    async def test_run_cycle_includes_strategy_selection_trace(self) -> None:
        """run_cycle payload includes strategy_selection data."""
        controller = get_executive_controller()
        cycle = await controller.run_cycle(
            {"text": "Hello world"},
            mode="run",
            user_id="trace_test_user",
        )

        assert cycle["ok"] is not None
        assert "strategy_selection" in cycle.get("context_signals", {})
        strategy_seln = cycle["context_signals"]["strategy_selection"]
        assert "selected_strategy" in strategy_seln or "error" in strategy_seln

    @pytest.mark.anyio
    async def test_run_cycle_includes_experience_write_back_markers(self) -> None:
        """run_cycle includes experience_write_back_attempted/succeeded."""
        controller = get_executive_controller()
        cycle = await controller.run_cycle(
            {"text": "Write test"},
            mode="run",
            user_id="exp_trace_user",
        )

        assert "experience_write_back_attempted" in cycle
        assert "experience_write_back_succeeded" in cycle
        # May fail if embedding not available, but should still attempt

    @pytest.mark.anyio
    async def test_run_cycle_truthfulness_execution_vs_intent(self) -> None:
        """Cycle distinguishes planning_attempted vs planning_executed."""
        controller = get_executive_controller()
        cycle = await controller.run_cycle(
            {"text": "Test", "max_steps": 1},
            mode="run",
            user_id="truth_cycle_user",
        )

        # planning_attempted/executed should be distinct fields
        assert (
            "planning_attempted" in cycle.get("execution_result", {}).get("payload", {})
            or "planning_attempted" in cycle
        )
        assert (
            "planning_executed" in cycle.get("execution_result", {}).get("payload", {})
            or "planning_executed" in cycle
        )


# ============================================================================
# REGRESSION TESTS (ensure prior behavior untouched)
# ============================================================================


class TestRegression:
    """Ensure ETAP 1-7 functionality still works."""

    @pytest.mark.anyio
    async def test_basic_chat_turn_still_works(self) -> None:
        """Basic agent_run still returns valid response."""
        from aihub.agent_runner import run_agent

        result = run_agent("Hello", user_id="regression_user")
        assert result["ok"] is not None
        assert "result" in result

    @pytest.mark.anyio
    async def test_executive_controller_compatibility(self) -> None:
        """ExecutiveController.run_cycle backward compatible."""
        controller = get_executive_controller()
        cycle = await controller.run_cycle(
            {"text": "Compat test"},
            mode="run",
            user_id="compat_user",
        )

        # Old fields must still exist
        assert "strategy" in cycle
        assert "strategy_reason" in cycle
        assert "mode" in cycle
        assert "execution_result" in cycle
        assert "reflection" in cycle


# ============================================================================
# BLOCKER FIXES TESTS
# ============================================================================


class TestAgentRunnerAsyncioFix:
    """Test async/sync split in agent_runner.py (Blocker 1)."""

    def test_run_agent_sync_works(self) -> None:
        """Verify run_agent works from sync context."""
        from aihub.agent_runner import run_agent

        result = run_agent(
            text="test query",
            user_id="test_sync_user",
            max_steps=2,
            timeout_seconds=5.0,
        )
        assert result is not None
        assert isinstance(result, dict)
        assert "ok" in result
        assert "result" in result

    @pytest.mark.anyio
    async def test_run_agent_async_works(self) -> None:
        """Verify run_agent_async works from async context (Blocker 1)."""
        from aihub.agent_runner import run_agent_async

        result = await run_agent_async(
            text="test async query",
            user_id="test_async_user",
            max_steps=2,
            timeout_seconds=5.0,
        )
        assert result is not None
        assert isinstance(result, dict)
        assert "ok" in result

    def test_run_agent_empty_rejects(self) -> None:
        """Verify run_agent rejects empty input."""
        from aihub.agent_runner import run_agent

        result = run_agent(text="", user_id="test_user")
        assert result.get("ok") is False
        assert "error" in result


class TestToolAliasNormalizationFix:
    """Test alias normalization for debug tools (Blocker 2)."""

    def test_normalize_tool_names(self) -> None:
        """Verify tool name normalization."""
        from aihub.tools.router import _normalize_tool_name

        assert _normalize_tool_name("debug_info") == "system.debug_info"
        assert _normalize_tool_name("health") == "system.health"
        assert _normalize_tool_name("status") == "runtime.status"

    def test_normalize_idempotent(self) -> None:
        """Verify canonical names pass through unchanged."""
        from aihub.tools.router import _normalize_tool_name

        assert _normalize_tool_name("system.debug_info") == "system.debug_info"
        assert _normalize_tool_name("memory.search") == "memory.search"

    def test_normalize_empty_safe(self) -> None:
        """Verify empty names handled safely."""
        from aihub.tools.router import _normalize_tool_name

        assert _normalize_tool_name("") == ""


class TestChatRuntimeEtap9aFix:
    """Test ETAP 9A trace fields in /chat/turn (Blocker 3)."""

    @pytest.mark.anyio
    async def test_trace_has_etap9a_fields(self) -> None:
        """Verify trace has ETAP 9A fields at top level."""
        from aihub.chat_contracts import ChatTurnInput
        from aihub.chat_runtime import get_chat_runtime

        runtime = get_chat_runtime()
        turn = ChatTurnInput(
            user_id="test_user",
            session_id="test_session",
            message="test message",
            history=[],
            mode="chat",
            include_debug=False,
        )
        result = await runtime.run_turn(turn)
        assert result is not None
        trace = result.model_dump()["trace"]

        # Verify all ETAP 9A fields exist at top level
        required_fields = [
            "selected_strategy",
            "reason_codes",
            "degraded",
            "memory_lookup_happened",
            "psyche_snapshot_happened",
            "research_was_required",
            "experience_write_back_attempted",
            "experience_write_back_succeeded",
        ]
        for field in required_fields:
            assert field in trace, f"Missing ETAP 9A field in trace: {field}"

    @pytest.mark.anyio
    async def test_trace_etap9a_fields_present(self, isolated_db) -> None:
        """Verify all ETAP 9A fields have proper default values."""
        from aihub.chat_contracts import ChatTurnInput
        from aihub.chat_runtime import get_chat_runtime
        from aihub.db import insert_stm_message

        # Isolated DB has no legacy memory hits; seed STM so retrieve_context reports lookup.
        insert_stm_message(
            "stm-seed-etap9a-trace",
            "test_user",
            "user",
            "prior turn",
            {},
        )

        runtime = get_chat_runtime()
        turn = ChatTurnInput(
            user_id="test_user",
            session_id="test_session",
            message="test query",
            history=[],
            mode="chat",
            include_debug=False,
        )
        result = await runtime.run_turn(turn)
        trace = result.model_dump()["trace"]

        # Verify default values for chat mode (strategy_selector is now invoked)
        assert trace["selected_strategy"] in ["instant", "chat", "contextual", None]
        assert isinstance(trace["reason_codes"], list)
        assert trace["degraded"] is False
        assert trace["memory_lookup_happened"] is True
        assert trace["psyche_snapshot_happened"] is True
        assert trace["research_was_required"] is False
        assert trace["experience_write_back_attempted"] is True
        assert trace["experience_write_back_succeeded"] is True
        assert trace.get("experience_episode_id")
        assert isinstance(trace.get("experience_fact_ids"), list)
        assert isinstance(trace.get("experience_stm_ids"), list)
        assert isinstance(trace.get("psyche_state_before"), dict)
        assert isinstance(trace.get("psyche_state_after"), dict)
        assert trace.get("psyche_state_before", {}).get("user_id") == turn.user_id

    @pytest.mark.anyio
    async def test_trace_backward_compatible(self) -> None:
        """Verify legacy trace fields unchanged."""
        from aihub.chat_contracts import ChatTurnInput
        from aihub.chat_runtime import get_chat_runtime

        runtime = get_chat_runtime()
        turn = ChatTurnInput(
            user_id="test_user",
            session_id="test_session",
            message="test",
            history=[],
            mode="chat",
        )
        result = await runtime.run_turn(turn)
        trace = result.model_dump()["trace"]

        # Legacy fields must exist
        assert "provider_calls" in trace
        assert "tool_iterations" in trace
        assert "duration_ms" in trace
        assert "provider" in trace
        assert "model" in trace


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
