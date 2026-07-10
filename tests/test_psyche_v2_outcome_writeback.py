#!/usr/bin/env python3
"""Tests for Psyche V2 outcome event application."""

import pytest

from aihub.psyche_v2_service import PsycheV2Service
from aihub.psyche_v2_repository import get_recent_psyche_events, ensure_psyche_state
from aihub.db import init_db


@pytest.fixture
def setup_db():
    """Initialize DB for tests."""
    init_db()
    yield


def test_apply_outcome_event_success(setup_db):
    """Test applying success outcome event."""
    service = PsycheV2Service()
    user_id = "test-psyche-success"
    source_ref = "cycle-123"

    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="success",
        source_ref=source_ref,
        context={},
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["event_applied"] == "interaction_complete"

    # Verify event persisted
    events = get_recent_psyche_events(user_id, limit=5)
    assert len(events) > 0
    assert events[0].event_type == "interaction_complete"
    assert events[0].source_ref == source_ref


def test_apply_outcome_event_failure(setup_db):
    """Test applying failure outcome event."""
    service = PsycheV2Service()
    user_id = "test-psyche-failure"
    source_ref = "cycle-456"

    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="failure",
        source_ref=source_ref,
        context={},
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["event_applied"] == "tool_failure"

    # Verify state changed
    state = ensure_psyche_state(user_id)
    assert state.pressure > 0.0


def test_apply_outcome_event_timeout(setup_db):
    """Test applying timeout outcome event."""
    service = PsycheV2Service()
    user_id = "test-psyche-timeout"
    source_ref = "cycle-789"

    # Get baseline state
    baseline_state = ensure_psyche_state(user_id)
    baseline_pressure = baseline_state.pressure
    baseline_focus = baseline_state.focus

    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="timeout",
        source_ref=source_ref,
        context={},
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["event_applied"] == "mode_transition"

    # Verify pressure increased, focus decreased
    updated_state = ensure_psyche_state(user_id)
    assert updated_state.pressure > baseline_pressure
    assert updated_state.focus < baseline_focus


def test_apply_outcome_event_progress(setup_db):
    """Test applying progress outcome event."""
    service = PsycheV2Service()
    user_id = "test-psyche-progress"
    source_ref = "cycle-999"

    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="progress",
        source_ref=source_ref,
        context={"goal_progress_changed": True},
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["event_applied"] == "user_feedback_positive"

    # Verify event reason
    events = get_recent_psyche_events(user_id, limit=5)
    assert len(events) > 0
    assert "progress" in events[0].reason_text.lower()


def test_apply_outcome_event_failure_with_contradictions(setup_db):
    """Test failure with contradictions adjusts strength."""
    service = PsycheV2Service()
    user_id = "test-psyche-failure-contradictions"
    source_ref = "cycle-111"

    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="failure",
        source_ref=source_ref,
        context={"contradictions_present": 3},
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True

    # Verify reason mentions contradictions
    events = get_recent_psyche_events(user_id, limit=5)
    assert len(events) > 0
    assert "contradiction" in events[0].reason_text.lower()


def test_apply_outcome_event_degraded(setup_db):
    """Test degraded outcome event."""
    service = PsycheV2Service()
    user_id = "test-psyche-degraded"
    source_ref = "turn-222"

    result = service.apply_outcome_event(
        user_id=user_id,
        outcome_kind="degraded",
        source_ref=source_ref,
        context={},
    )

    assert result["attempted"] is True
    assert result["succeeded"] is True
    assert result["event_applied"] == "confidence_shift"

    # Verify event persisted
    events = get_recent_psyche_events(user_id, limit=5)
    assert len(events) > 0
    assert events[0].event_type == "confidence_shift"
