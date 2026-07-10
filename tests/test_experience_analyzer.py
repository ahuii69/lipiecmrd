from __future__ import annotations

from pathlib import Path

from aihub import db as db_module
from aihub.db import init_db, write_experience
from aihub.experience_analyzer import ExperienceAnalyzer


def _set_test_db(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "test_experience_analyzer.sqlite3"
    monkeypatch.setattr("aihub.config.DB_PATH", db_path, raising=False)
    db_module._ADAPTER_HOLDER.clear()
    return db_path


def test_experience_analyzer_empty_result_for_unknown_user(tmp_path: Path, monkeypatch) -> None:
    _set_test_db(tmp_path, monkeypatch)
    init_db()

    analyzer = ExperienceAnalyzer()
    result = analyzer.analyze_recent_experiences("unknown-user", 10)

    assert result == {
        "instant": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "contextual": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "research": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "agentic": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
    }


def test_experience_analyzer_aggregates_per_strategy_metrics(tmp_path: Path, monkeypatch) -> None:
    _set_test_db(tmp_path, monkeypatch)
    init_db()

    write_experience(
        experience_id="exp-instant-1",
        user_id="mordo",
        user_input_summary="instant one",
        selected_strategy="instant",
        reason_codes=["fast_path"],
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
        fallback_flag=False,
        degraded_flag=False,
        latency_ms=100.0,
        content_hash="hash-instant-1",
        metadata={"case": 1},
    )

    write_experience(
        experience_id="exp-instant-2",
        user_id="mordo",
        user_input_summary="instant two",
        selected_strategy="instant",
        reason_codes=["fast_path"],
        tools_needed=True,
        tools_executed=True,
        research_needed=False,
        research_executed=False,
        planner_recommended=True,
        planner_executed=True,
        agentic_recommended=False,
        agentic_executed=False,
        outcome_type="failure",
        success=False,
        fallback_flag=True,
        degraded_flag=False,
        latency_ms=300.0,
        content_hash="hash-instant-2",
        metadata={"case": 2},
    )

    write_experience(
        experience_id="exp-research-1",
        user_id="mordo",
        user_input_summary="research one",
        selected_strategy="research",
        reason_codes=["need_web"],
        tools_needed=True,
        tools_executed=True,
        research_needed=True,
        research_executed=True,
        planner_recommended=False,
        planner_executed=False,
        agentic_recommended=True,
        agentic_executed=True,
        outcome_type="success",
        success=True,
        fallback_flag=False,
        degraded_flag=False,
        latency_ms=50.0,
        content_hash="hash-research-1",
        metadata={"case": 3},
    )

    analyzer = ExperienceAnalyzer()
    result = analyzer.analyze_recent_experiences("mordo", 10)

    assert result["instant"]["sample_count"] == 2
    assert result["instant"]["success_rate"] == 0.5
    assert result["instant"]["avg_latency_ms"] == 200.0
    assert result["instant"]["fallback_rate"] == 0.5
    assert result["instant"]["tool_usage_rate"] == 0.5
    assert result["instant"]["reasoning_usage_rate"] == 0.5

    assert result["research"]["sample_count"] == 1
    assert result["research"]["success_rate"] == 1.0
    assert result["research"]["avg_latency_ms"] == 50.0
    assert result["research"]["fallback_rate"] == 0.0
    assert result["research"]["tool_usage_rate"] == 1.0
    assert result["research"]["reasoning_usage_rate"] == 1.0

    assert result["contextual"] == {
        "sample_count": 0,
        "success_rate": 0.0,
        "avg_latency_ms": 0.0,
        "fallback_rate": 0.0,
        "tool_usage_rate": 0.0,
        "reasoning_usage_rate": 0.0,
    }
    assert result["agentic"] == {
        "sample_count": 0,
        "success_rate": 0.0,
        "avg_latency_ms": 0.0,
        "fallback_rate": 0.0,
        "tool_usage_rate": 0.0,
        "reasoning_usage_rate": 0.0,
    }


def test_experience_analyzer_ignores_unknown_strategy(tmp_path: Path, monkeypatch) -> None:
    _set_test_db(tmp_path, monkeypatch)
    init_db()

    write_experience(
        experience_id="exp-unknown-1",
        user_id="mordo",
        user_input_summary="unknown strategy",
        selected_strategy="weird_mode",
        reason_codes=["custom"],
        tools_needed=True,
        tools_executed=True,
        research_needed=False,
        research_executed=False,
        planner_recommended=False,
        planner_executed=False,
        agentic_recommended=False,
        agentic_executed=False,
        outcome_type="success",
        success=True,
        fallback_flag=False,
        degraded_flag=False,
        latency_ms=123.0,
        content_hash="hash-unknown-1",
        metadata={"case": "unknown"},
    )

    analyzer = ExperienceAnalyzer()
    result = analyzer.analyze_recent_experiences("mordo", 10)

    assert result == {
        "instant": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "contextual": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "research": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
        "agentic": {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        },
    }


def test_experience_analyzer_skips_none_latency_in_average(tmp_path: Path, monkeypatch) -> None:
    _set_test_db(tmp_path, monkeypatch)
    init_db()

    write_experience(
        experience_id="exp-contextual-1",
        user_id="mordo",
        user_input_summary="ctx one",
        selected_strategy="contextual",
        reason_codes=["memory"],
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
        fallback_flag=False,
        degraded_flag=False,
        latency_ms=None,
        content_hash="hash-contextual-1",
        metadata={"case": 1},
    )

    write_experience(
        experience_id="exp-contextual-2",
        user_id="mordo",
        user_input_summary="ctx two",
        selected_strategy="contextual",
        reason_codes=["memory"],
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
        fallback_flag=False,
        degraded_flag=False,
        latency_ms=80.0,
        content_hash="hash-contextual-2",
        metadata={"case": 2},
    )

    analyzer = ExperienceAnalyzer()
    result = analyzer.analyze_recent_experiences("mordo", 10)

    assert result["contextual"]["sample_count"] == 2
    assert result["contextual"]["success_rate"] == 1.0
    assert result["contextual"]["avg_latency_ms"] == 80.0
    assert result["contextual"]["fallback_rate"] == 0.0
    assert result["contextual"]["tool_usage_rate"] == 0.0
    assert result["contextual"]["reasoning_usage_rate"] == 0.0


def test_experience_analyzer_normalizes_limit_and_user_id(tmp_path: Path, monkeypatch) -> None:
    _set_test_db(tmp_path, monkeypatch)
    init_db()

    write_experience(
        experience_id="exp-agentic-1",
        user_id="mordo",
        user_input_summary="agentic one",
        selected_strategy="agentic",
        reason_codes=["complex"],
        tools_needed=True,
        tools_executed=True,
        research_needed=True,
        research_executed=True,
        planner_recommended=True,
        planner_executed=True,
        agentic_recommended=True,
        agentic_executed=True,
        outcome_type="success",
        success=True,
        fallback_flag=False,
        degraded_flag=False,
        latency_ms=250.0,
        content_hash="hash-agentic-1",
        metadata={"case": 1},
    )

    analyzer = ExperienceAnalyzer()
    result = analyzer.analyze_recent_experiences("  mordo  ", limit="bad-limit")

    assert result["agentic"]["sample_count"] == 1
    assert result["agentic"]["success_rate"] == 1.0
    assert result["agentic"]["avg_latency_ms"] == 250.0
    assert result["agentic"]["fallback_rate"] == 0.0
    assert result["agentic"]["tool_usage_rate"] == 1.0
    assert result["agentic"]["reasoning_usage_rate"] == 1.0
