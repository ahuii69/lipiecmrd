"""Runtime confidence tests for background agent_worker lifecycle."""

from __future__ import annotations


class _FakeThread:
    def __init__(self, alive: bool = False):
        self._alive = alive
        self.started = False

    def start(self):
        self.started = True
        self._alive = True

    def is_alive(self):
        return self._alive

    def join(self, _timeout=None):
        return None


def test_start_worker_once_respects_autostart_false(monkeypatch):
    import aihub.agent_worker as aw

    monkeypatch.setattr(aw, "AGENT_AUTOSTART", False)
    monkeypatch.setattr(aw, "_worker_started", False)
    monkeypatch.setattr(aw, "_worker_thread", None)

    aw.start_worker_once()

    assert aw._worker_started is False
    assert aw._worker_thread is None


def test_start_worker_once_idempotent_when_alive(monkeypatch):
    import aihub.agent_worker as aw

    alive_thread = _FakeThread(alive=True)
    monkeypatch.setattr(aw, "AGENT_AUTOSTART", True)
    monkeypatch.setattr(aw, "_worker_started", True)
    monkeypatch.setattr(aw, "_worker_thread", alive_thread)

    aw.start_worker_once()

    assert aw._worker_thread is alive_thread
    assert alive_thread.started is False


def test_start_worker_once_restarts_dead_thread(monkeypatch):
    import aihub.agent_worker as aw

    dead_thread = _FakeThread(alive=False)
    new_thread = _FakeThread(alive=False)

    monkeypatch.setattr(aw, "AGENT_AUTOSTART", True)
    monkeypatch.setattr(aw, "_worker_started", True)
    monkeypatch.setattr(aw, "_worker_thread", dead_thread)
    monkeypatch.setattr(
        aw.threading,
        "Thread",
        lambda **_kwargs: new_thread,
    )

    aw.start_worker_once()

    assert aw._worker_started is True
    assert aw._worker_thread is new_thread
    assert new_thread.started is True


def test_run_loop_runs_tick_once_and_exits_on_keyboard_interrupt(monkeypatch):
    import aihub.agent_worker as aw

    class _Controller:
        def __init__(self):
            self.calls = 0

        async def run_cycle(self, *_args, **_kwargs):
            self.calls += 1
            return {
                "legacy_response": {"ok": True, "processed": 1, "enqueued": 0},
            }

    controller = _Controller()

    # 06.07 repair sprint: tests/conftest.py sets AIHUB_BACKGROUND_AGENT_LOOP_ENABLED=0 (as a
    # process env var, before any module import) so unrelated tests never spin up a real
    # background loop; aihub.agent_worker reads that into the module-level constant
    # BACKGROUND_AGENT_LOOP_ENABLED once at import time. This test specifically exercises the
    # enabled path, so it must override the already-imported module's constant directly.
    monkeypatch.setattr(aw, "BACKGROUND_AGENT_LOOP_ENABLED", True)
    monkeypatch.setattr(aw, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        aw,
        "get_agent_state",
        lambda _uid: {"enabled": True},
    )
    monkeypatch.setattr(aw, "get_executive_controller", lambda: controller)

    def _sleep(_seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(aw.time, "sleep", _sleep)

    aw._run_loop()

    assert controller.calls == 1


def test_run_loop_disabled_state_skips_controller(monkeypatch):
    import aihub.agent_worker as aw

    class _Controller:
        def __init__(self):
            self.calls = 0

        async def run_cycle(self, *_args, **_kwargs):
            self.calls += 1
            return {"legacy_response": {"ok": True}}

    controller = _Controller()

    monkeypatch.setattr(aw, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        aw,
        "get_agent_state",
        lambda _uid: {"enabled": False},
    )
    monkeypatch.setattr(aw, "get_executive_controller", lambda: controller)

    def _sleep(_seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(aw.time, "sleep", _sleep)

    aw._run_loop()

    assert controller.calls == 0


def test_run_loop_emits_background_loop_event_when_cycle_traced(monkeypatch):
    import aihub.agent_worker as aw

    events: list[tuple] = []

    class _Controller:
        async def run_cycle(self, *_a, **_k):
            return {
                "cycle_id": "bg-cycle-1",
                "execution_origin": "background_agent_loop",
                "proactive_noop": True,
                "proactive_trigger_type": "none",
                "background_result_type": "noop",
                "bias_updated": False,
                "legacy_response": {"ok": True, "processed": 0, "enqueued": 0},
            }

    monkeypatch.setattr(aw, "BACKGROUND_AGENT_LOOP_ENABLED", True)
    monkeypatch.setattr(aw, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        aw,
        "get_agent_state",
        lambda _uid: {"enabled": True},
    )
    monkeypatch.setattr(aw, "get_executive_controller", lambda: _Controller())
    monkeypatch.setattr(
        aw,
        "append_event",
        lambda user_id, typ, data: events.append((user_id, typ, data)),
    )

    def _sleep(_seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(aw.time, "sleep", _sleep)

    aw._run_loop()

    assert any(t == "agent.background_loop.tick" for _u, t, _d in events)


def test_run_loop_emits_background_tick_when_trace_present_but_legacy_not_ok(
    monkeypatch,
):
    import aihub.agent_worker as aw

    events: list[tuple] = []

    class _Controller:
        async def run_cycle(self, *_a, **_k):
            return {
                "cycle_id": "bg-cycle-fail",
                "execution_origin": "background_agent_loop",
                "proactive_noop": False,
                "proactive_trigger_present": True,
                "proactive_trigger_type": "task_execution",
                "background_result_type": "failure",
                "bias_updated": False,
                "legacy_response": {"ok": False, "error": "tick_failed"},
            }

    monkeypatch.setattr(aw, "BACKGROUND_AGENT_LOOP_ENABLED", True)
    monkeypatch.setattr(aw, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        aw,
        "get_agent_state",
        lambda _uid: {"enabled": True},
    )
    monkeypatch.setattr(aw, "get_executive_controller", lambda: _Controller())
    monkeypatch.setattr(
        aw,
        "append_event",
        lambda user_id, typ, data: events.append((user_id, typ, data)),
    )

    def _sleep(_seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(aw.time, "sleep", _sleep)

    aw._run_loop()

    bg = [d for _u, t, d in events if t == "agent.background_loop.tick"]
    assert len(bg) == 1
    assert bg[0].get("tick_ok") is False
    assert bg[0].get("background_result_type") == "failure"
    assert any(t == "agent.worker.error" for _u, t, _d in events)


def test_run_loop_records_error_when_all_retries_fail(monkeypatch):
    import aihub.agent_worker as aw

    class _Controller:
        async def run_cycle(self, *_args, **_kwargs):
            return {
                "legacy_response": {"ok": False, "error": "boom"},
            }

    events = []

    monkeypatch.setattr(aw, "BACKGROUND_AGENT_LOOP_ENABLED", True)
    monkeypatch.setattr(aw, "ensure_schema", lambda: None)
    monkeypatch.setattr(
        aw,
        "get_agent_state",
        lambda _uid: {"enabled": True},
    )

    def _controller_provider():
        return _Controller()

    monkeypatch.setattr(aw, "get_executive_controller", _controller_provider)
    monkeypatch.setattr(aw, "AGENT_MAX_RETRIES", 1)
    monkeypatch.setattr(
        aw,
        "append_event",
        lambda user_id, typ, data: events.append((user_id, typ, data)),
    )

    def _sleep(_seconds):
        raise KeyboardInterrupt()

    monkeypatch.setattr(aw.time, "sleep", _sleep)

    aw._run_loop()

    assert any(t == "agent.worker.error" for _u, t, _d in events)


def test_is_running_reflects_thread_state(monkeypatch):
    import aihub.agent_worker as aw

    running_thread = _FakeThread(alive=True)
    monkeypatch.setattr(aw, "_worker_started", True)
    monkeypatch.setattr(aw, "_worker_thread", running_thread)
    assert aw.is_running() is True

    stopped_thread = _FakeThread(alive=False)
    monkeypatch.setattr(aw, "_worker_started", True)
    monkeypatch.setattr(aw, "_worker_thread", stopped_thread)
    assert aw.is_running() is False
