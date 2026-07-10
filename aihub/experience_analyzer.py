"""Aggregate experience_memory rows into deterministic per-strategy metrics for runtime feedback."""

from __future__ import annotations

import logging
from typing import Any, Final, TypedDict, cast

from aihub.db import get_experiences_by_user

logger = logging.getLogger(__name__)

_STRATEGIES: Final[tuple[str, str, str, str]] = (
    "instant",
    "contextual",
    "research",
    "agentic",
)

_DEFAULT_LIMIT: Final[int] = 100
_MAX_LIMIT: Final[int] = 1000


class StrategyMetrics(TypedDict):
    sample_count: int
    success_rate: float
    avg_latency_ms: float
    fallback_rate: float
    tool_usage_rate: float
    reasoning_usage_rate: float


class ExperienceAnalyzerResult(TypedDict):
    instant: StrategyMetrics
    contextual: StrategyMetrics
    research: StrategyMetrics
    agentic: StrategyMetrics


class ExperienceAnalyzer:
    """Read-only analysis of recent experiences for a user."""

    def analyze_recent_experiences(
        self,
        user_id: str,
        limit: int = _DEFAULT_LIMIT,
    ) -> ExperienceAnalyzerResult:
        uid = self._normalize_user_id(user_id)
        if not uid:
            return self._empty_result()

        safe_limit = self._normalize_limit(limit)

        try:
            rows = get_experiences_by_user(uid, limit=safe_limit)
        except Exception:
            logger.exception(
                "ExperienceAnalyzer: failed to fetch experiences for user_id=%r limit=%r",
                uid,
                safe_limit,
            )
            return self._empty_result()

        buckets: dict[str, list[dict[str, Any]]] = {strategy: [] for strategy in _STRATEGIES}

        for row in rows:
            strategy = self._normalize_strategy(row.get("selected_strategy"))
            if strategy is None:
                continue
            buckets[strategy].append(row)

        result: dict[str, StrategyMetrics] = {}
        for strategy in _STRATEGIES:
            result[strategy] = self._compute_metrics(buckets[strategy])

        return cast(ExperienceAnalyzerResult, result)

    @staticmethod
    def _normalize_user_id(user_id: Any) -> str:
        if user_id is None:
            return ""
        if isinstance(user_id, str):
            return user_id.strip()
        return str(user_id).strip()

    @staticmethod
    def _normalize_limit(limit: Any) -> int:
        try:
            value = int(limit)
        except (TypeError, ValueError):
            return _DEFAULT_LIMIT

        if value <= 0:
            return _DEFAULT_LIMIT
        if value > _MAX_LIMIT:
            return _MAX_LIMIT
        return value

    @staticmethod
    def _normalize_strategy(value: Any) -> str | None:
        strategy = str(value or "").strip().lower()
        if strategy in _STRATEGIES:
            return strategy
        return None

    @staticmethod
    def _empty_metrics() -> StrategyMetrics:
        return {
            "sample_count": 0,
            "success_rate": 0.0,
            "avg_latency_ms": 0.0,
            "fallback_rate": 0.0,
            "tool_usage_rate": 0.0,
            "reasoning_usage_rate": 0.0,
        }

    @classmethod
    def _empty_result(cls) -> ExperienceAnalyzerResult:
        return {
            "instant": cls._empty_metrics(),
            "contextual": cls._empty_metrics(),
            "research": cls._empty_metrics(),
            "agentic": cls._empty_metrics(),
        }

    @classmethod
    def _compute_metrics(cls, items: list[dict[str, Any]]) -> StrategyMetrics:
        sample_count = len(items)
        if sample_count == 0:
            return cls._empty_metrics()

        success_count = 0
        fallback_count = 0
        tool_usage_count = 0
        reasoning_usage_count = 0
        latency_values: list[float] = []

        for item in items:
            if cls._as_bool(item.get("success")):
                success_count += 1

            if cls._as_bool(item.get("fallback_flag")):
                fallback_count += 1

            if cls._as_bool(item.get("tools_executed")):
                tool_usage_count += 1

            if cls._as_bool(item.get("planner_executed")) or cls._as_bool(
                item.get("agentic_executed")
            ):
                reasoning_usage_count += 1

            latency = cls._as_latency_ms(item.get("latency_ms"))
            if latency is not None:
                latency_values.append(latency)

        avg_latency_ms = (
            round(sum(latency_values) / len(latency_values), 2) if latency_values else 0.0
        )

        return {
            "sample_count": sample_count,
            "success_rate": round(success_count / sample_count, 4),
            "avg_latency_ms": avg_latency_ms,
            "fallback_rate": round(fallback_count / sample_count, 4),
            "tool_usage_rate": round(tool_usage_count / sample_count, 4),
            "reasoning_usage_rate": round(reasoning_usage_count / sample_count, 4),
        }

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, float):
            return value != 0.0
        return False

    @staticmethod
    def _as_latency_ms(value: Any) -> float | None:
        if value is None:
            return None

        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None

        if parsed < 0.0:
            return None

        return parsed
