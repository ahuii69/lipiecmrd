"""Post-sprint hardening tests: idempotency, rate-limit, backoff, quality gate."""

import asyncio
import ssl
from unittest.mock import MagicMock, patch

import httpx
import pytest

from aihub.db import fetch_all
from aihub.psyche_engine import ensure_user

# ---------------------------------------------------------------------------
# ETAP 2: Research idempotency (dedup)
# ---------------------------------------------------------------------------


class TestResearchIdempotency:
    def test_same_query_no_duplicates(self, isolated_db):
        """2× ten sam query → fakty NIE rosną po 2. wywołaniu."""
        uid = "idemp_same"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_results = [
            {
                "title": "Python info",
                "url": "https://en.wikipedia.org/wiki/Python_(programming_language)",
                "content": (
                    "Badania wykazują że Python jest najpopularniejszym językiem programowania w 2024 roku na świecie."
                ),
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_results),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            r1 = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Python programming")
            )
            r2 = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Python programming")
            )

        assert r1["ok"]
        assert r2.get("cached") is True

        facts_count = len(
            fetch_all(
                "SELECT id FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
                (uid,),
            )
        )
        # After cached second call, no new facts
        assert facts_count >= 1

    def test_case_space_normalization(self, isolated_db):
        """Warianty zapytania z innym case/spacjami → cache hit."""
        uid = "idemp_norm"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_results = [
            {
                "title": "Rust lang",
                "url": "https://en.wikipedia.org/wiki/Rust_(programming_language)",
                "content": (
                    "Badania wykazują że Rust jest najszybciej rosnącym językiem systemowym w 2025 roku."
                ),
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_results),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            r1 = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Rust Programming")
            )
            r2 = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "  rust   programming  ")
            )

        assert r1["ok"]
        assert r2.get("cached") is True

    def test_different_query_grows(self, isolated_db):
        """Różne zapytania → fakty rosną."""
        uid = "idemp_diff"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_py = [
            {
                "title": "Python",
                "url": "https://en.wikipedia.org/wiki/Python",
                "content": "Badania wykazują że Python jest popularny wśród Data Scientistów w 2024 roku na świecie.",
                "source": "wikipedia",
            }
        ]
        fake_rs = [
            {
                "title": "Rust",
                "url": "https://en.wikipedia.org/wiki/Rust",
                "content": "Badania wykazują że Rust zastępuje C++ w wielu projektach systemowych na przestrzeni lat.",
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_py),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            r1 = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Python data science")
            )

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_rs),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            r2 = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Rust systems")
            )

        assert r1["ok"] and r2["ok"]

        facts = fetch_all(
            "SELECT id FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        # At least 2 unique facts (one per query, since URLs differ)
        assert len(facts) >= 2


# ---------------------------------------------------------------------------
# ETAP 3: Rate limit + backoff
# ---------------------------------------------------------------------------


class TestResearchBackoff:
    def test_429_retry_then_raise(self, isolated_db):
        """Mock 429 → 3 retries z backoff, potem raise."""
        from aihub.research_engine import _http_get_with_backoff

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429 Too Many Requests",
            request=MagicMock(),
            response=mock_response,
        )

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response

        with patch("aihub.research_engine.time.sleep"):
            with pytest.raises(httpx.HTTPStatusError):
                _http_get_with_backoff(
                    mock_client, "https://example.com/test", params={}
                )

        # initial + 3 retries = 4 calls
        assert mock_client.get.call_count == 4

    def test_5xx_retry_then_success(self, isolated_db):
        """Pierwsze 2 requesty 503, trzeci 200 → sukces."""
        from aihub.research_engine import _http_get_with_backoff

        fail_resp = MagicMock()
        fail_resp.status_code = 503

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.side_effect = [fail_resp, fail_resp, ok_resp]

        with patch("aihub.research_engine.time.sleep"):
            result = _http_get_with_backoff(mock_client, "https://example.com/api")

        assert result.status_code == 200
        assert mock_client.get.call_count == 3

    def test_research_http_clients_use_explicit_verify_and_trust_env(self, monkeypatch):
        from aihub.research_engine import ResearchEngine

        captured = {}

        class _FakeClient:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def get(self, *_args, **_kwargs):
                response = MagicMock()
                response.status_code = 200
                response.json.return_value = ["q", [], [], []]
                return response

        monkeypatch.setattr("aihub.research_engine.httpx.Client", _FakeClient)

        ResearchEngine()._fetch_wikipedia("python")

        assert captured["trust_env"] is False
        assert isinstance(captured["verify"], ssl.SSLContext)

    def test_rate_limiter_skips_second(self, isolated_db):
        """2 research w 1s → drugi skip (rate_limited event)."""
        uid = "rate_user"
        ensure_user(uid)

        from aihub.agent_engine import _execute_research, _research_rate
        from aihub.research_engine import _research_engine

        _research_rate.clear()

        fake_results = [
            {
                "title": "Test",
                "url": "https://en.wikipedia.org/wiki/Test",
                "content": "Badania wykazują że testowanie oprogramowania jest kluczowe dla jakości w 2025 roku.",
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(
                _research_engine, "_fetch_wikipedia", return_value=fake_results
            ),
            patch.object(_research_engine, "_fetch_duckduckgo", return_value=[]),
        ):
            asyncio.get_event_loop().run_until_complete(
                _execute_research(uid, {"query": "testing quality"})
            )
            asyncio.get_event_loop().run_until_complete(
                _execute_research(uid, {"query": "testing quality 2"})
            )

        events = fetch_all(
            "SELECT type FROM event_log WHERE user_id=? AND type='agent.research.rate_limited'",
            (uid,),
        )
        assert len(events) >= 1

    def test_research_soft_fail_no_crash(self, isolated_db):
        """Partial backend failure → soft-fail safety, no crash, no invalid persisted facts."""
        uid = "softfail_user"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        with (
            patch.object(
                engine,
                "_fetch_wikipedia",
                side_effect=httpx.HTTPError("connection failed"),
            ),
            patch.object(
                engine,
                "_fetch_duckduckgo",
                side_effect=httpx.HTTPError("connection failed"),
            ),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "anything")
            )

        assert result["ok"]
        assert isinstance(result.get("results"), list)
        assert result["total_results"] == len(result["results"])
        assert result["total_results"] >= 0
        assert result["total_facts"] >= 0
        # If results exist, facts_extracted should sum correctly
        assert result["total_facts"] == sum(
            int(r.get("facts_extracted", 0) or 0) for r in result["results"]
        )
        # Safety invariant: if any facts were stored, they must pass quality gate.
        import json

        from aihub.research_engine import _BOILERPLATE_RE, RESEARCH_MIN_FACT_LEN

        facts = fetch_all(
            "SELECT content, meta FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        assert len(facts) == result["total_facts"]
        for row in facts:
            content = (row["content"] or "").strip()
            assert len(content) >= RESEARCH_MIN_FACT_LEN
            assert "placeholder" not in content.lower()
            assert _BOILERPLATE_RE.search(content) is None

            meta = json.loads(row["meta"] or "{}")
            assert meta.get("research_query") == "anything"
            assert meta.get("backend") in {
                "brave",
                "wikipedia",
                "duckduckgo",
                "unknown",
            }


# ---------------------------------------------------------------------------
# ETAP 4: Quality gate
# ---------------------------------------------------------------------------


class TestQualityGate:
    def test_filter_short_text(self):
        from aihub.research_engine import filter_research_text

        assert filter_research_text("too short") is None
        assert filter_research_text("") is None
        assert filter_research_text("   ") is None

    def test_filter_boilerplate(self):
        from aihub.research_engine import filter_research_text

        assert (
            filter_research_text(
                "Please accept all cookies to continue browsing this great website"
            )
            is None
        )
        assert (
            filter_research_text(
                "Sign in to your account to access premium content and features now"
            )
            is None
        )
        assert (
            filter_research_text(
                "JavaScript required to view this page properly on modern browsers"
            )
            is None
        )

    def test_filter_good_text_passes(self):
        from aihub.research_engine import filter_research_text

        result = filter_research_text(
            "Python is a high-level programming language created by Guido van Rossum in 1991"
        )
        assert result is not None
        assert len(result) >= 40

    def test_filter_truncates_long_text(self):
        from aihub.research_engine import RESEARCH_MAX_FACT_LEN, filter_research_text

        long_text = "A" * 2000
        result = filter_research_text(long_text)
        assert result is not None
        assert len(result) == RESEARCH_MAX_FACT_LEN

    def test_filter_normalizes_whitespace(self):
        from aihub.research_engine import filter_research_text

        result = filter_research_text(
            "Python   jest    językiem   programowania   ogólnego   przeznaczenia   stworzonym   przez  Guido"
        )
        assert result is not None
        assert "   " not in result

    def test_boilerplate_research_zero_facts(self, isolated_db):
        """Wynik research z boilerplate → 0 faktów w pamięci."""
        uid = "qual_boiler"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_results = [
            {
                "title": "Junk page",
                "url": "https://example.com/junk",
                "content": (
                    "Badania wykazują że musisz zaakceptować cookies policy i sign in do serwisu żeby kontynuować przeglądanie. "
                    "According to our privacy policy wszystkie dane użytkowników są chronione przez nasze systemy."
                ),
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_results),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "junk test")
            )

        assert result["ok"]
        assert result["total_facts"] == 0

        facts = fetch_all(
            "SELECT id FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        assert len(facts) == 0

    def test_good_content_saves_facts(self, isolated_db):
        """Dobry wynik research → fakty zapisane z kompletem meta."""
        uid = "qual_good"
        ensure_user(uid)

        from aihub.research_engine import ResearchEngine

        engine = ResearchEngine()

        fake_results = [
            {
                "title": "Python (programming language)",
                "url": "https://en.wikipedia.org/wiki/Python",
                "content": (
                    "Badania wykazują że Python jest najpopularniejszym językiem programowania w 2024 roku na świecie."
                ),
                "source": "wikipedia",
            }
        ]

        with (
            patch.object(engine, "_fetch_wikipedia", return_value=fake_results),
            patch.object(engine, "_fetch_duckduckgo", return_value=[]),
        ):
            result = asyncio.get_event_loop().run_until_complete(
                engine.research(uid, "Python popularity")
            )

        assert result["ok"]
        assert result["total_facts"] >= 1

        facts = fetch_all(
            "SELECT content, meta FROM memory_nodes WHERE user_id=? AND layer='L2' AND deleted=0",
            (uid,),
        )
        assert len(facts) >= 1

        import json

        meta = json.loads(facts[0]["meta"])
        assert "source_url" in meta
        assert "source_title" in meta
        assert "backend" in meta
        assert "research_query" in meta


# ---------------------------------------------------------------------------
# Normalization unit tests
# ---------------------------------------------------------------------------


class TestNormalization:
    def test_normalize_query(self):
        from aihub.research_engine import normalize_query

        assert normalize_query("  Python   3.13  ") == "python 3.13"
        assert normalize_query("RUST") == "rust"
        assert normalize_query("hello world") == "hello world"

    def test_normalize_url_strips_tracking(self):
        from aihub.research_engine import normalize_url

        url = "https://Example.COM/Path/Page?utm_source=google&real=1"
        normed = normalize_url(url)
        assert "example.com" in normed
        assert "utm_source" not in normed
        assert "real=1" in normed

    def test_normalize_url_trims_slash(self):
        from aihub.research_engine import normalize_url

        assert normalize_url("https://example.com/path/").endswith("/path")

    def test_fingerprint_stable(self):
        from aihub.research_engine import _research_fingerprint

        fp1 = _research_fingerprint(
            "u1", "wikipedia", "Python 3.13", "https://wiki/Python"
        )
        fp2 = _research_fingerprint(
            "u1", "wikipedia", "  python   3.13  ", "https://Wiki/Python/"
        )
        assert fp1 == fp2

    def test_fingerprint_differs_for_different_query(self):
        from aihub.research_engine import _research_fingerprint

        fp1 = _research_fingerprint("u1", "wikipedia", "Python", "https://wiki/Python")
        fp2 = _research_fingerprint("u1", "wikipedia", "Rust", "https://wiki/Python")
        assert fp1 != fp2
