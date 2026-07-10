#!/usr/bin/env python3
"""
Tests for Psyche V2 adaptation logic.
"""

import pytest

from aihub.psyche_v2_models import PsycheV2State, PsycheV2Event
from aihub.psyche_v2_adaptation import apply_event_to_state, detect_mode_transition


def test_apply_event_to_state():
    """Test event application to state."""
    state = PsycheV2State(
        user_id="user1",
        mood=0.5,
        energy=0.5,
        focus=0.5,
        pressure=0.0,
        certainty=0.5,
        current_mode="neutral",
        updated_ts=0.0,
    )

    event = PsycheV2Event(
        id="event-1",
        user_id="user1",
        event_type="tool_success",
        delta={"mood": 0.1, "certainty": 0.1},
        reason_text="Success event",
        created_ts=0.0,
    )

    updated_state = apply_event_to_state(state, event, signal_strength=0.5)

    assert updated_state.mood > 0.5
    assert updated_state.certainty > 0.5


def test_detect_mode_transition_exploratory():
    """Test mode transition detection to exploratory."""
    state = PsycheV2State(
        user_id="user1",
        mood=0.7,
        energy=0.7,
        focus=0.6,
        pressure=0.2,
        certainty=0.5,
        current_mode="neutral",
        updated_ts=0.0,
    )

    mode = detect_mode_transition(state)
    assert mode == "exploratory"


def test_detect_mode_transition_defensive():
    """Test mode transition detection to defensive."""
    state = PsycheV2State(
        user_id="user1",
        mood=0.3,
        energy=0.4,
        focus=0.5,
        pressure=0.8,
        certainty=0.3,
        current_mode="neutral",
        updated_ts=0.0,
    )

    mode = detect_mode_transition(state)
    assert mode == "cautious"
