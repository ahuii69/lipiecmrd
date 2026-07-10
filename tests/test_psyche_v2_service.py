#!/usr/bin/env python3
"""
Tests for Psyche V2 Service.
"""

import time

import pytest

from aihub.psyche_v2_service import PsycheV2Service


@pytest.fixture
def service():
    """Return Psyche V2 Service instance."""
    return PsycheV2Service()


def test_ensure_user(service: PsycheV2Service):
    """Test user profile and state initialization."""
    profile, state = service.ensure_user("user1")

    assert profile is not None
    assert state is not None
    assert profile.user_id == "user1"
    assert state.user_id == "user1"
    assert profile.core_directness == 0.5
    assert state.current_mode == "neutral"


def test_get_snapshot(service: PsycheV2Service):
    """Test psyche snapshot retrieval."""
    service.ensure_user("user1")

    snapshot = service.get_snapshot("user1")
    assert snapshot.profile is not None
    assert snapshot.state is not None
    assert snapshot.profile.user_id == "user1"


def test_apply_event(service: PsycheV2Service):
    """Test event application."""
    service.ensure_user("user1")

    event = service.apply_event(
        user_id="user1",
        event_type="tool_success",
        reason_text="Task completed successfully",
        signal_strength=0.3,
        metadata={"task": "test"},
    )

    assert event is not None
    assert event.event_type == "tool_success"


def test_derive_policy(service: PsycheV2Service):
    """Test policy derivation."""
    service.ensure_user("user1")

    policy = service.derive_policy("user1")
    assert policy is not None
    assert isinstance(policy, dict)
    assert 0.0 <= policy["directness"] <= 1.0
    assert 0.0 <= policy["verbosity"] <= 1.0
    assert 0.0 <= policy["verbosity"] <= 1.0
