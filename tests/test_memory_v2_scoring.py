#!/usr/bin/env python3
"""
Tests for Memory V2 scoring logic.
"""

import pytest

from aihub.memory_v2_scoring import (
    calculate_salience,
    calculate_freshness,
    calculate_identity_relevance,
)
from aihub.memory_psyche_contracts import MemoryV2ScoringWeights


def test_calculate_salience_basic():
    """Test salience calculation with default weights."""
    salience = calculate_salience(
        importance_score=0.8,
        recurrence_score=0.5,
        emotional_weight=0.6,
        identity_relevance_score=0.7,
        confidence_score=0.9,
        freshness_score=1.0,
    )

    assert 0.0 <= salience <= 1.0
    assert salience > 0.5


def test_calculate_salience_clamps():
    """Test salience is clamped to 0-1 range."""
    high_salience = calculate_salience(
        importance_score=1.5,
        recurrence_score=1.5,
        emotional_weight=1.5,
        identity_relevance_score=1.5,
        confidence_score=1.5,
        freshness_score=1.5,
    )
    assert high_salience == 1.0

    low_salience = calculate_salience(
        importance_score=-0.5,
        recurrence_score=-0.5,
        emotional_weight=-0.5,
        identity_relevance_score=-0.5,
        confidence_score=-0.5,
        freshness_score=-0.5,
    )
    assert low_salience == 0.0


def test_calculate_freshness_recent():
    """Test freshness calculation for recent memory."""
    import time

    now = time.time()
    recent_ts = now - 3600

    freshness = calculate_freshness(recent_ts, is_pinned=False)
    assert 0.9 <= freshness <= 1.0


def test_calculate_freshness_old():
    """Test freshness calculation for old memory."""
    import time

    now = time.time()
    old_ts = now - (30 * 86400)

    freshness = calculate_freshness(old_ts, is_pinned=False)
    assert freshness < 0.5


def test_calculate_freshness_pinned():
    """Test pinned memories decay slower."""
    import time

    now = time.time()
    old_ts = now - (30 * 86400)

    freshness_pinned = calculate_freshness(old_ts, is_pinned=True)
    freshness_normal = calculate_freshness(old_ts, is_pinned=False)

    assert freshness_pinned > freshness_normal


def test_calculate_identity_relevance():
    """Test identity relevance scoring."""
    pref_rel = calculate_identity_relevance("preference", "user", 0.8)
    assert pref_rel > 0.7

    fact_rel = calculate_identity_relevance("fact", "session", 0.5)
    assert fact_rel < 0.7

    lesson_rel = calculate_identity_relevance("lesson", "global", 0.9)
    assert lesson_rel > 0.6
