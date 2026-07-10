#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Metrics Engine - System monitorowania i obserwowatelności.

Odpowiada za:
- Zbieranie metryk
- Performance tracking
- Error rate monitoring
- Resource usage
- System health
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

from aihub.db import append_event, now_ts

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """Punkt danych metryki."""

    metric_name: str
    value: float
    timestamp: float = field(default_factory=now_ts)
    tags: Dict[str, str] = field(default_factory=dict)
    unit: str = ""


@dataclass
class SystemMetrics:
    """Systemowe metryki."""

    latency_ms: float = 0.0
    memory_usage_mb: float = 0.0
    error_rate: float = 0.0
    requests_per_second: float = 0.0
    average_response_time_ms: float = 0.0
    cache_hit_rate: float = 0.0
    timestamp: float = field(default_factory=now_ts)


class MetricsEngine:
    """Engine do zbierania i analizy metryk systemowych."""

    MAX_POINTS_PER_METRIC = 1000
    TTL_SECONDS = 3600  # 1 hour

    def __init__(self):
        self.metrics: Dict[str, List[MetricPoint]] = {}

    def _prune(self, metric_name: str) -> None:
        """Remove expired points and enforce size limit."""
        points = self.metrics.get(metric_name)
        if not points:
            return
        cutoff = now_ts() - self.TTL_SECONDS
        # Remove old points by TTL
        pruned = [p for p in points if p.timestamp >= cutoff]
        # Enforce size cap
        if len(pruned) > self.MAX_POINTS_PER_METRIC:
            pruned = pruned[-self.MAX_POINTS_PER_METRIC :]
        self.metrics[metric_name] = pruned

    def record_metric(self, point: MetricPoint) -> None:
        """Record single metric point."""
        try:
            if point.metric_name not in self.metrics:
                self.metrics[point.metric_name] = []

            self.metrics[point.metric_name].append(point)
            self._prune(point.metric_name)

            logger.debug("Metric: %s=%s %s", point.metric_name, point.value, point.unit)
        except Exception as e:
            logger.warning("Error recording metric: %s", e)

    def record_latency(
        self, operation: str, duration_ms: float, success: bool = True
    ) -> None:
        """Record operation latency."""
        try:
            point = MetricPoint(
                metric_name=f"latency.{operation}",
                value=duration_ms,
                unit="ms",
                tags={"status": "success" if success else "error"},
            )
            self.record_metric(point)
        except Exception as e:
            logger.warning(f"Error recording latency: {e}")

    def record_error(self, error_type: str, user_id: str = "unknown") -> None:
        """Record error occurrence."""
        try:
            point = MetricPoint(
                metric_name="error",
                value=1.0,
                tags={"error_type": error_type, "user_id": user_id},
            )
            self.record_metric(point)
            append_event(user_id, "metrics.error", {"error_type": error_type})
        except Exception as e:
            logger.warning(f"Error recording error metric: {e}")

    def record_resource_usage(self, memory_mb: float, cpu_percent: float) -> None:
        """Record system resource usage."""
        try:
            self.record_metric(
                MetricPoint(metric_name="system.memory", value=memory_mb, unit="MB")
            )
            self.record_metric(
                MetricPoint(metric_name="system.cpu", value=cpu_percent, unit="%")
            )
        except Exception as e:
            logger.warning(f"Error recording resource usage: {e}")

    def get_metrics_summary(
        self, metric_name: str, window_seconds: int = 300
    ) -> Dict[str, Any]:
        """Get statistical summary of metric over time window."""
        try:
            if metric_name not in self.metrics:
                return {"error": f"No data for metric {metric_name}"}

            now = now_ts()
            window_start = now - window_seconds

            points = [
                p for p in self.metrics[metric_name] if p.timestamp >= window_start
            ]

            if not points:
                return {"error": "No recent data"}

            values = [p.value for p in points]

            return {
                "metric": metric_name,
                "window_seconds": window_seconds,
                "count": len(points),
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "latest": values[-1] if values else None,
                "timestamp": now,
            }
        except Exception as e:
            logger.error(f"Error getting metrics summary: {e}")
            return {"error": str(e)}

    def get_system_health(self) -> SystemMetrics:
        """Get overall system health metrics."""
        try:
            latency = self.get_metrics_summary("latency.api", window_seconds=60).get(
                "avg", 0.0
            )
            error_metrics = self.metrics.get("error", [])
            error_count = len([p for p in error_metrics if p.timestamp > now_ts() - 60])
            total_ops = sum(
                1
                for m in self.metrics.values()
                for p in m
                if p.timestamp > now_ts() - 60
            )

            error_rate = error_count / max(1, total_ops)

            return SystemMetrics(
                latency_ms=latency,
                error_rate=min(1.0, error_rate),
                requests_per_second=total_ops / 60.0,
                timestamp=now_ts(),
            )
        except Exception as e:
            logger.error(f"Error getting system health: {e}")
            return SystemMetrics()

    def get_alert_status(self) -> Dict[str, Any]:
        """Check if any metrics exceed thresholds."""
        try:
            alerts = []
            health = self.get_system_health()

            if health.latency_ms > 1000:
                alerts.append(
                    {
                        "type": "HIGH_LATENCY",
                        "value": health.latency_ms,
                        "threshold": 1000,
                    }
                )

            if health.error_rate > 0.05:
                alerts.append(
                    {
                        "type": "HIGH_ERROR_RATE",
                        "value": health.error_rate,
                        "threshold": 0.05,
                    }
                )

            return {
                "alert_count": len(alerts),
                "alerts": alerts,
                "health": {
                    "latency_ms": health.latency_ms,
                    "error_rate": health.error_rate,
                    "rps": health.requests_per_second,
                },
            }
        except Exception as e:
            logger.error(f"Error checking alert status: {e}")
            return {"error": str(e)}


# Singleton
_metrics = MetricsEngine()


def record_metric(point: MetricPoint) -> None:
    """Public API."""
    return _metrics.record_metric(point)


def record_latency(operation: str, duration_ms: float, success: bool = True) -> None:
    """Public API."""
    return _metrics.record_latency(operation, duration_ms, success)


def record_error(error_type: str, user_id: str = "unknown") -> None:
    """Public API."""
    return _metrics.record_error(error_type, user_id)


def get_system_health() -> SystemMetrics:
    """Public API."""
    return _metrics.get_system_health()


def get_alert_status() -> Dict[str, Any]:
    """Public API."""
    return _metrics.get_alert_status()
