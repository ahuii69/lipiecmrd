#!/usr/bin/env python3
"""Tests for Memory V2 decay/lifecycle logic (``aihub.memory_v2_decay``).

06.07 repair sprint (P2): this module implements real, non-trivial decay-bucket and freshness
lifecycle logic, but nothing in the active runtime calls ``run_decay_pass`` (no scheduled
worker/cron wires it up yet — see ``aihub/memory_v2_decay.py`` module docstring and
``06.07naprawa.md``). Rather than leaving it completely untested dead code, or building a new
scheduled-worker feature (out of scope for this security-focused repair sprint), this test
exercises the decay logic directly against the real repository so the logic itself is covered
and regressions are caught if/when it is wired into a scheduler.
"""

from __future__ import annotations

import time

from aihub.memory_v2_decay import apply_decay_update, assign_decay_bucket, run_decay_pass
from aihub.memory_v2_models import MemoryV2Item
from aihub.memory_v2_repository import get_memory_item, insert_memory_item


def test_assign_decay_bucket_pinned_always_active():
    assert assign_decay_bucket(0.0, 0.0, is_pinned=True, recurrence_score=0.0) == "active"


def test_assign_decay_bucket_thresholds():
    assert assign_decay_bucket(0.9, 0.0, is_pinned=False, recurrence_score=0.0) == "active"
    assert assign_decay_bucket(0.5, 0.0, is_pinned=False, recurrence_score=0.0) == "warm"
    assert assign_decay_bucket(0.3, 0.0, is_pinned=False, recurrence_score=0.0) == "cooling"
    assert assign_decay_bucket(0.0, 0.0, is_pinned=False, recurrence_score=0.0) == "archive_candidate"


def test_apply_decay_update_recalculates_freshness_and_bucket():
    now = time.time()
    old_item = MemoryV2Item(
        id="mem-decay-1",
        user_id="user_decay",
        memory_type="fact",
        scope="user",
        title="Old fact",
        content="Something said a long time ago",
        summary="Old fact",
        source_kind="chat_turn",
        salience_score=0.0,
        recurrence_score=0.0,
        is_pinned=False,
        created_ts=now - (60 * 60 * 24 * 400),  # ~400 days old
        updated_ts=now - (60 * 60 * 24 * 400),
    )

    updated = apply_decay_update(old_item)

    assert updated.freshness_score < 0.2
    assert updated.decay_bucket == "archive_candidate"
    assert updated.updated_ts >= old_item.created_ts


def test_run_decay_pass_persists_updates_via_repository():
    now = time.time()
    item = MemoryV2Item(
        id="mem-decay-2",
        user_id="user_decay2",
        memory_type="fact",
        scope="user",
        title="Aging fact",
        content="This fact is aging out",
        summary="Aging fact",
        source_kind="chat_turn",
        salience_score=0.1,
        recurrence_score=0.0,
        is_pinned=False,
        created_ts=now - (60 * 60 * 24 * 400),
        updated_ts=now - (60 * 60 * 24 * 400),
    )
    assert insert_memory_item(item)

    updated_count = run_decay_pass("user_decay2", batch_size=10)
    assert updated_count == 1

    persisted = get_memory_item("mem-decay-2", "user_decay2")
    assert persisted is not None
    assert persisted.decay_bucket == "archive_candidate"
    assert persisted.freshness_score < 0.2
