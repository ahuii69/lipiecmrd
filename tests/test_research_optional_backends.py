"""Soft-failure semantics for optional research backends (Wikipedia, DDG)."""

from __future__ import annotations

import uuid

import pytest


@pytest.mark.anyio
async def test_research_survives_wikipedia_backend_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from aihub import research_engine as re_mod

    eng = re_mod._research_engine
    eng._query_cache.clear()

    monkeypatch.setattr(re_mod, "BRAVE_API_KEY", "test-key-for-branch", raising=False)

    def _wiki_boom(_query: str) -> list:
        raise RuntimeError("simulated wikipedia thread failure")

    def _brave_ok(_query: str) -> list:
        filler = "x" * 80
        return [
            {
                "title": "Brave hit",
                "url": "https://example.com/brave",
                "content": filler,
                "source": "brave",
            }
        ]

    monkeypatch.setattr(eng, "_fetch_wikipedia", _wiki_boom)
    monkeypatch.setattr(eng, "_fetch_brave", _brave_ok)

    q = f"test-wiki-soft-{uuid.uuid4().hex}"
    out = await eng.research("user_soft_wiki", q, "general")
    assert out.get("ok") is True
    assert out.get("total_results", 0) >= 1
    assert any(
        (r.get("source") == "brave" or "example.com" in str(r.get("url", "")))
        for r in (out.get("results") or [])
    )
