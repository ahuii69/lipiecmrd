"""Tests for AgentExecutor action routing and subsystem integration."""

import asyncio
from unittest.mock import AsyncMock

from aihub.db import fetch_one
from aihub.memory_engine import add_fact
from aihub.psyche_engine import ensure_user


def test_executor_query():
    uid = "executor_query_user"
    ensure_user(uid)
    add_fact(uid, "Python to język programowania", tags=["tech"], meta={})

    from aihub.agent_executor import AgentExecutor

    ex = AgentExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute("query", {"query": "Python", "limit": 5}, uid)
    )

    assert result["ok"] is True
    assert result["action"] == "query"
    assert "context" in result


def test_executor_research(monkeypatch):
    uid = "executor_research_user"
    ensure_user(uid)

    import aihub.research_engine as re_mod
    from aihub.agent_executor import AgentExecutor

    async def _fake_research(user_id: str, query: str, research_type: str = "general"):
        return {
            "ok": True,
            "user_id": user_id,
            "query": query,
            "type": research_type,
            "total_results": 1,
            "total_facts": 1,
        }

    monkeypatch.setattr(re_mod, "research", _fake_research)

    ex = AgentExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute("research", {"query": "LLM"}, uid)
    )

    assert result["ok"] is True
    assert result["action"] == "research"
    assert result["query"] == "LLM"


def test_executor_learn():
    uid = "executor_learn_user"
    ensure_user(uid)

    from aihub.agent_executor import AgentExecutor

    ex = AgentExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute("learn", {"fact": "Użytkownik lubi ciemny motyw"}, uid)
    )

    assert result["ok"] is True
    assert result["action"] == "learn"
    assert result.get("node_id")

    row = fetch_one(
        "SELECT id, content FROM memory_nodes WHERE id=?",
        (result["node_id"],),
    )
    assert row is not None
    assert "ciemny motyw" in row["content"]


def test_executor_action(monkeypatch):
    uid = "executor_action_user"
    ensure_user(uid)

    import aihub.web_tools as wt
    from aihub.agent_executor import AgentExecutor

    fetch_mock = AsyncMock(return_value={"ok": True, "status": 200, "text": "ok"})
    monkeypatch.setattr(wt, "fetch_url", fetch_mock)

    ex = AgentExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute(
            "action",
            {"tool": "web_fetch", "params": {"url": "https://example.com"}},
            uid,
        )
    )

    assert result["ok"] is True
    assert result["action"] == "tool"
    assert result["tool"] == "web.fetch_url"
    fetch_mock.assert_awaited_once()


def test_executor_reason():
    uid = "executor_reason_user"
    ensure_user(uid)

    from aihub.agent_executor import AgentExecutor

    ex = AgentExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute(
            "reason",
            {
                "message": "co wiemy o Pythonie?",
                "memory_total": 0,
                "knowledge_hits": 0,
            },
            uid,
        )
    )

    assert result["ok"] is True
    assert result["action"] == "reason"
    assert result["analysis"]["recommendation"] in {"research", "query", "action"}


def test_executor_alias_memory_query():
    uid = "executor_alias_user"
    ensure_user(uid)
    add_fact(uid, "Alias test fact", tags=["test"], meta={})

    from aihub.agent_executor import AgentExecutor

    ex = AgentExecutor()
    result = asyncio.get_event_loop().run_until_complete(
        ex.execute("memory_query", {"query": "Alias", "limit": 5}, uid)
    )

    assert result["ok"] is True
    assert result["action"] == "query"
    assert "context" in result
