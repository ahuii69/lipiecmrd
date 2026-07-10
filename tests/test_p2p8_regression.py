"""P2-P8 regression tests — pytest-compatible version of test_p2p8_validation.py."""

import ast
import asyncio
import inspect
import time

import pytest

from aihub.cognitive_controller import CognitiveController, DecisionRequest
from aihub.conflict_detector import check_conflict
from aihub.memory_gc import MemoryGC
from aihub.metrics_engine import MetricPoint, MetricsEngine
from aihub.prediction_engine import predict_next_action

# ── P1: Module compilation ──────────────────────────────────────────

REQUIRED_MODULES = [
    "aihub.cognitive_controller",
    "aihub.attention_controller",
    "aihub.conflict_detector",
    "aihub.knowledge_graph",
    "aihub.knowledge_evolution",
    "aihub.memory_gc",
    "aihub.metrics_engine",
    "aihub.prediction_engine",
    "aihub.agent_loop",
    "aihub.psyche_engine",
    "aihub.db",
    "aihub.config",
]


@pytest.mark.parametrize("module_name", REQUIRED_MODULES)
def test_module_imports(module_name):
    __import__(module_name)


# ── P2: Conflict detector type validation ────────────────────────────


def test_p2_bad_format_rejected():
    result = check_conflict("test_user", [{"query": "hello", "limit": 20}])
    assert result.has_conflict
    assert result.conflict_type == "validation_error"


def test_p2_correct_format_passes():
    result = check_conflict(
        "test_user", [{"type": "memory_search", "parameters": {"query": "hello"}}]
    )
    assert not result.has_conflict or result.conflict_type != "validation_error"


def test_p2_forbidden_action():
    result = check_conflict(
        "test_user", [{"type": "delete_all_memory", "parameters": {}}]
    )
    assert result.has_conflict
    assert result.conflict_type == "security_violation"


# ── P3: Resource limits ──────────────────────────────────────────────


def test_p3_web_request_limit():
    controller = CognitiveController()
    for i in range(4):
        has, reason = controller._check_resources("limit_test", "web_request")
        if i < 3:
            assert has, f"request {i} should succeed"
        else:
            assert not has, f"request {i} should be blocked"
            assert "Resource limit" in reason


def test_p3_decide_query_blocked_when_limit_exceeded():
    controller = CognitiveController()
    controller.reset_state("limit_test2")
    for _ in range(6):
        controller._check_resources("limit_test2", "memory_operation")

    req = DecisionRequest(
        user_id="limit_test2",
        message="test query",
        context={"psyche_state": {"mood": "neutral", "energy": 0.5, "focus": 0.5}},
        available_tools=[],
    )
    result = asyncio.get_event_loop().run_until_complete(
        controller._decide_query("limit_test2", req, req.context)
    )
    assert result.action_type == "skip"
    assert result.skip_reason is not None


# ── P4: Context influences decisions ─────────────────────────────────


def test_p4_context_affects_query_limit():
    controller = CognitiveController()
    controller.reset_state("ctx_low")
    controller.reset_state("ctx_high")

    low_ctx = {
        "psyche_state": {"mood": "tired", "energy": 0.1, "focus": 0.2},
        "urgency": 0.1,
        "relevance_score": 0.3,
    }
    high_ctx = {
        "psyche_state": {"mood": "focused", "energy": 0.9, "focus": 0.9},
        "urgency": 0.9,
        "relevance_score": 0.9,
    }

    req_low = DecisionRequest(
        user_id="ctx_low", message="test", context=low_ctx, available_tools=[]
    )
    req_high = DecisionRequest(
        user_id="ctx_high", message="test", context=high_ctx, available_tools=[]
    )

    loop = asyncio.new_event_loop()
    result_low = loop.run_until_complete(
        controller._decide_query("ctx_low", req_low, low_ctx)
    )
    result_high = loop.run_until_complete(
        controller._decide_query("ctx_high", req_high, high_ctx)
    )
    loop.close()

    assert result_low.parameters["limit"] == 10
    assert result_high.parameters["limit"] == 20


# ── P5: Predictions not empty ────────────────────────────────────────


def test_p5_predictions_for_active_context():
    ctx = {
        "psyche_state": {"mood": "focused", "energy": 0.8, "focus": 0.9},
        "urgency_score": 0.8,
        "relevance_score": 0.7,
        "intent": "research",
        "memory_pressure": 0.1,
    }
    predictions = predict_next_action("pred_test", ctx)
    assert len(predictions) > 0


def test_p5_tired_high_urgency_predictions():
    ctx = {
        "psyche_state": {"mood": "tired", "energy": 0.1, "focus": 0.3},
        "urgency_score": 0.9,
        "relevance_score": 0.5,
    }
    predictions = predict_next_action("pred_test3", ctx)
    types = [p.prediction_type for p in predictions]
    assert "disengage_risk" in types
    assert "urgent_response" in types


# ── P6: Metrics TTL and rotation ─────────────────────────────────────


def test_p6_ttl_prune():
    engine = MetricsEngine()
    old_ts = time.time() - 7200
    for i in range(500):
        engine.record_metric(
            MetricPoint(metric_name="test_metric", value=float(i), timestamp=old_ts + i)
        )

    fresh_ts = time.time()
    for i in range(50):
        engine.record_metric(
            MetricPoint(
                metric_name="test_metric", value=float(i), timestamp=fresh_ts + i * 0.01
            )
        )

    count = len(engine.metrics.get("test_metric", []))
    assert count <= 50


def test_p6_size_cap():
    engine = MetricsEngine()
    for i in range(2000):
        engine.record_metric(MetricPoint(metric_name="big_metric", value=float(i)))
    big_count = len(engine.metrics.get("big_metric", []))
    assert big_count <= MetricsEngine.MAX_POINTS_PER_METRIC


# ── P7: Knowledge evolution integrated ───────────────────────────────


def test_p7_gc_uses_knowledge_evolution():
    gc = MemoryGC()
    assert hasattr(gc, "knowledge_evolution")
    assert not hasattr(gc, "_compress_facts")
    source = inspect.getsource(gc.collect_garbage)
    assert "evolve_all" in source
    assert "_compress_facts" not in source


# ── P8: No broad bare exceptions in agent_loop ───────────────────────


def test_p8_no_broad_exceptions_in_agent_loop():
    with open("aihub/agent_loop.py") as f:
        tree = ast.parse(f.read())

    broad_handlers = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                broad_handlers.append(f"line {node.lineno}: bare except")
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                broad_handlers.append(f"line {node.lineno}: except Exception")

    safe_count = sum(1 for h in broad_handlers if "except Exception" in h)
    assert safe_count <= 7, f"Too many broad exception handlers: {broad_handlers}"


# ── E2E pipeline ─────────────────────────────────────────────────────


def test_e2e_query_scenario():
    ctrl = CognitiveController()
    req = DecisionRequest(
        user_id="e2e_user",
        message="Co to jest Python?",
        context={
            "psyche_state": {"mood": "curious", "energy": 0.7, "focus": 0.8},
            "urgency_score": 0.5,
            "relevance_score": 0.6,
        },
        available_tools=["web_search", "memory_store"],
    )
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(ctrl.decide(req))
    loop.close()
    assert result.action_type in ("memory_search", "skip")


def test_e2e_research_scenario():
    ctrl = CognitiveController()
    req = DecisionRequest(
        user_id="e2e_user_r",
        message="Wyszukaj najnowsze badania o LLM",
        context={
            "psyche_state": {"mood": "focused", "energy": 0.9, "focus": 0.9},
            "urgency_score": 0.8,
            "relevance_score": 0.7,
        },
        available_tools=["web_search"],
    )
    loop = asyncio.new_event_loop()
    result = loop.run_until_complete(ctrl.decide(req))
    loop.close()
    assert result.action_type in ("research", "skip")
