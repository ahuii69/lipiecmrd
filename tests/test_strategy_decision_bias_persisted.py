#!/usr/bin/env python3
"""Per-user persisted strategy_decision_bias: isolation and reload semantics."""

from __future__ import annotations

import importlib
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("isolated_db")

from aihub.db import (
    get_strategy_decision_bias,
    reset_strategy_decision_bias,
    save_strategy_decision_bias,
)
from aihub.strategy_selector import select_strategy


def test_users_do_not_share_strategy_bias() -> None:
    save_strategy_decision_bias(
        "user_a",
        {"instant": -0.06, "contextual": 0.0, "research": 0.0, "agentic": 0.05},
        metrics_snapshot={"note": "a"},
    )
    save_strategy_decision_bias(
        "user_b",
        {"instant": 0.03, "contextual": 0.04, "research": 0.0, "agentic": 0.0},
        metrics_snapshot={"note": "b"},
    )
    ba = get_strategy_decision_bias("user_a")
    bb = get_strategy_decision_bias("user_b")
    assert ba["instant"] == -0.06
    assert ba["agentic"] == 0.05
    assert bb["instant"] == 0.03
    assert bb["contextual"] == 0.04


def test_reload_db_module_keeps_bias_same_file() -> None:
    """Simulate process restart: reload db + strategy_selector; SQLite file unchanged."""
    import aihub.db as db_mod
    import aihub.strategy_selector as ss_mod

    uid = f"reload_{uuid.uuid4().hex[:8]}"
    db_mod.save_strategy_decision_bias(
        uid,
        {"instant": -0.06, "contextual": 0.0, "research": 0.0, "agentic": 0.0},
        metrics_snapshot={},
    )

    importlib.reload(db_mod)
    importlib.reload(ss_mod)

    assert db_mod.get_strategy_decision_bias(uid)["instant"] == -0.06
    sel = ss_mod.select_strategy(
        user_id=uid,
        user_text="ile to 2+2",
        mode="chat",
        active_goals_summary=None,
        history=[],
    )
    assert sel.selected_strategy == "instant"
    assert sel.confidence == 0.76


def test_reset_strategy_decision_bias_clears_user_row() -> None:
    uid = "reset_me"
    save_strategy_decision_bias(
        uid,
        {"instant": -0.06, "contextual": 0.0, "research": 0.0, "agentic": 0.0},
        metrics_snapshot={},
    )
    reset_strategy_decision_bias(uid)
    assert get_strategy_decision_bias(uid) == {
        "instant": 0.0,
        "contextual": 0.0,
        "research": 0.0,
        "agentic": 0.0,
    }


def test_select_strategy_trace_shows_persisted_load_source() -> None:
    uid = "trace_load"
    save_strategy_decision_bias(
        uid,
        {"instant": -0.06, "contextual": 0.0, "research": 0.0, "agentic": 0.0},
        metrics_snapshot={},
    )
    sel = select_strategy(
        user_id=uid,
        user_text="ile to 2+2",
        mode="chat",
        active_goals_summary=None,
        history=[],
    )
    assert sel.trace_payload.get("strategy_bias_load_source") == "persisted"
    assert sel.trace_payload.get("strategy_confidence_bias", {}).get("instant") == -0.06
