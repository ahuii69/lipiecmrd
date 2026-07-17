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


def test_frankfurter_fx_pair_parsing() -> None:
    from aihub.research_engine import _fx_pair_from_query

    assert _fx_pair_from_query("Sprawdź dzisiejszy kurs EUR do PLN") == ("EUR", "PLN")
    assert _fx_pair_from_query("Python version") is None


def test_frankfurter_fetch_returns_rate(monkeypatch: pytest.MonkeyPatch) -> None:
    from aihub import research_engine as re_mod

    eng = re_mod._research_engine

    class _Resp:
        def json(self):
            return {"date": "2026-07-14", "rates": {"PLN": 4.33}}

    monkeypatch.setattr(
        re_mod,
        "_http_get_with_backoff",
        lambda _client, _url, **kwargs: _Resp(),
    )
    out = eng._fetch_frankfurter_fx("EUR PLN")
    assert len(out) == 1
    assert "4.33" in out[0]["content"]
    assert out[0]["source"] == "frankfurter"


def test_open_meteo_and_pypi_helpers() -> None:
    from aihub.research_engine import (
        _pypi_package_from_query,
        _python_version_intent,
        _weather_location_from_query,
    )

    assert _weather_location_from_query("pogoda w Warszawie") is not None
    assert _pypi_package_from_query("najnowszy stabilny release FastAPI") == "fastapi"
    assert _python_version_intent("aktualna stabilna wersja Pythona")


def test_open_meteo_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from aihub import research_engine as re_mod

    eng = re_mod._research_engine

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "current": {
                    "time": "2026-07-17T10:00",
                    "temperature_2m": 21.5,
                    "weather_code": 1,
                    "wind_speed_10m": 5.0,
                }
            }

    monkeypatch.setattr(
        re_mod,
        "_http_get_with_backoff",
        lambda _client, _url, **kwargs: _Resp(),
    )
    out = eng._fetch_open_meteo("Jaka jest teraz pogoda w Warszawie?")
    assert len(out) == 1
    assert out[0]["source"] == "open_meteo"
    assert "21.5" in out[0]["content"]


def test_pypi_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    from aihub import research_engine as re_mod

    eng = re_mod._research_engine

    class _Resp:
        status_code = 200

        def json(self):
            return {
                "info": {
                    "version": "0.139.2",
                    "release_url": "https://pypi.org/project/fastapi/0.139.2/",
                }
            }

    monkeypatch.setattr(
        re_mod,
        "_http_get_with_backoff",
        lambda _client, _url, **kwargs: _Resp(),
    )
    out = eng._fetch_pypi("Jaki jest najnowszy stabilny release FastAPI?")
    assert len(out) == 1
    assert out[0]["source"] == "pypi"
    assert "0.139.2" in out[0]["content"]
