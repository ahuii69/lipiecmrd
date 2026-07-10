#!/usr/bin/env python3

"""
Tests for EMBEDDING_PROVIDER routing, healthcheck honesty vs embed, and fallback flags.
"""

from __future__ import annotations

import os
from unittest import mock

import pytest

from aihub.embedding_engine import reset_providers


class TestEmbeddingAutoMode:
    """EMBEDDING_PROVIDER=auto / voyage / sentence-transformers."""

    @pytest.fixture(autouse=True)
    def reset_embedding_state(self):
        reset_providers()
        yield
        reset_providers()

    def test_auto_mode_with_voyage_key_will_attempt_voyage(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "test-key-present",
                "EMBEDDING_MODEL": "voyage-4-large",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            health = ee.healthcheck()
            assert health["provider"] == "auto"
            assert health["voyage_api_key_present"] is True
            assert health["will_attempt_voyage_first"] is True
            assert health["will_use_voyage"] is True
            assert health["st_only_expected"] is False
            assert health["runtime_st_produces_embedding"] is False
            assert health["output_dimension"] == ee.EMBEDDING_OUTPUT_DIM
            assert health["st_fallback_output_dimension_if_voyage_fails"] == 384

    def test_auto_mode_without_voyage_key_st_only_expected(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "",
                "EMBEDDING_MODEL": "all-MiniLM-L6-v2",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            health = ee.healthcheck()
            assert health["provider"] == "auto"
            assert health["voyage_api_key_present"] is False
            assert health["will_attempt_voyage_first"] is False
            assert health["st_only_expected"] is True
            assert health["runtime_st_produces_embedding"] is True
            assert health["output_dimension"] == 384

    def test_explicit_voyage_provider_always_attempts_voyage(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "voyage",
                "VOYAGE_API_KEY": "",
                "EMBEDDING_MODEL": "voyage-4-large",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            health = ee.healthcheck()
            assert health["provider"] == "voyage"
            assert health["will_attempt_voyage_first"] is True
            assert health["runtime_st_produces_embedding"] is True
            assert health["output_dimension"] == 384

    def test_sentence_transformers_failure_cached(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "",
                "EMBEDDING_MODEL": "all-MiniLM-L6-v2",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            with mock.patch(
                "aihub.embedding_engine._init_sentence_transformer",
                return_value=None,
            ):
                with mock.patch(
                    "aihub.embedding_engine._sentence_transformers_init_failed",
                    True,
                ):
                    with pytest.raises(ee.EmbeddingError):
                        ee.embed_query("test query 1")

                    health1 = ee.healthcheck()
                    assert health1["sentence_transformers_init_failed"] is True

                    with pytest.raises(ee.EmbeddingError):
                        ee.embed_query("test query 2")

                    health2 = ee.healthcheck()
                    assert health2["sentence_transformers_init_failed"] is True
                    assert health2["sentence_transformers_loaded"] is False

    def test_voyage_fallback_to_st_on_api_error_sets_flag(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "test-key-invalid",
                "EMBEDDING_MODEL": "voyage-4-large",
                "AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK": "1",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            with (
                mock.patch(
                    "aihub.embedding_engine._call_voyage_api",
                    side_effect=ee.EmbeddingProviderError("Voyage API: 401"),
                ),
                mock.patch(
                    "aihub.embedding_engine._call_sentence_transformer",
                    return_value=[0.1] * 384,
                ),
            ):
                response = ee.embed_query("test fallback query")
                assert response.provider == "sentence-transformers"
                assert response.embedding_fallback_used is True
                assert response.primary_provider_attempted == "voyage"
                assert len(response.vector) == 384

    def test_auto_with_key_voyage_success_no_fallback(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "k",
                "EMBEDDING_MODEL": "voyage-4-large",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            with (
                mock.patch(
                    "aihub.embedding_engine._call_voyage_api",
                    return_value=[0.02] * 1024,
                ),
                mock.patch(
                    "aihub.embedding_engine._call_sentence_transformer",
                ) as st_mock,
            ):
                r = ee.embed_query("no st")
                assert r.provider == "voyage"
                assert r.embedding_fallback_used is False
                assert r.primary_provider_attempted == "voyage"
                st_mock.assert_not_called()

    def test_auto_without_key_never_calls_voyage(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "",
                "EMBEDDING_MODEL": "voyage-4-large",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            with (
                mock.patch(
                    "aihub.embedding_engine._call_voyage_api",
                ) as v_mock,
                mock.patch(
                    "aihub.embedding_engine._call_sentence_transformer",
                    return_value=[0.1] * 384,
                ),
            ):
                r = ee.embed_query("st only")
                v_mock.assert_not_called()
                assert r.provider == "sentence-transformers"
                assert r.embedding_fallback_used is False
                assert r.primary_provider_attempted == "none"

    def test_healthcheck_consistent_with_auto_no_key_embed(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "auto",
                "VOYAGE_API_KEY": "",
                "EMBEDDING_MODEL": "voyage-4-large",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            h = ee.healthcheck()
            assert h["st_only_expected"] is True
            assert h["will_attempt_voyage_first"] is False
            assert h["output_dimension"] == 384
            assert h["output_dimension_semantics"] == "sentence_transformers_expected"

            with mock.patch(
                "aihub.embedding_engine._call_sentence_transformer",
                return_value=[0.1] * 384,
            ):
                r = ee.embed_query("consistency")
            assert r.primary_provider_attempted == "none"
            assert r.embedding_fallback_used is False

    def test_sentence_transformers_provider_skips_voyage(self):
        with mock.patch.dict(
            os.environ,
            {
                "EMBEDDING_HEALTHCHECK_LIVE_PROBE": "0",
                "AIHUB_DISABLE_REMOTE_EMBEDDINGS": "0",
                "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK": "0",
                "EMBEDDING_PROVIDER": "sentence-transformers",
                "VOYAGE_API_KEY": "should-not-matter",
                "EMBEDDING_MODEL": "voyage-4-large",
            },
        ):
            import importlib

            import aihub.config
            import aihub.embedding_engine as ee

            importlib.reload(aihub.config)
            importlib.reload(ee)

            with (
                mock.patch(
                    "aihub.embedding_engine._call_voyage_api",
                ) as v_mock,
                mock.patch(
                    "aihub.embedding_engine._call_sentence_transformer",
                    return_value=[0.1] * 384,
                ),
            ):
                r = ee.embed_query("st mode")
                v_mock.assert_not_called()
                assert r.primary_provider_attempted == "sentence-transformers"
