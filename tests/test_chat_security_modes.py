"""Security/policy tests for mode-based capability control."""

from __future__ import annotations


def test_debug_tools_hidden_without_debug_flag():
    from aihub.tools.registry import get_tool_registry

    reg = get_tool_registry()
    caps = reg.list_capabilities(mode="chat", include_debug=False, policy_overrides={})
    names = {c.name for c in caps}

    assert "debug.last_events" not in names
    assert "system.debug_info" not in names


def test_debug_tools_visible_in_debug_mode():
    from aihub.tools.registry import get_tool_registry

    reg = get_tool_registry()
    caps = reg.list_capabilities(mode="debug", include_debug=True, policy_overrides={})
    names = {c.name for c in caps}

    assert "debug.last_events" in names
    assert "system.debug_info" in names


def test_readonly_mode_excludes_mutating_tools():
    from aihub.tools.registry import get_tool_registry

    reg = get_tool_registry()
    caps = reg.list_capabilities(
        mode="readonly", include_debug=False, policy_overrides={}
    )
    names = {c.name for c in caps}

    assert "memory.add_fact" not in names
    assert "memory.add_episode" not in names
    assert "fs.write_file" not in names
    assert "snapshot.create" not in names
