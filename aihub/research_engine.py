#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import hashlib
import logging
import os
import re
import ssl
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Awaitable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx

from aihub.config import (
    BRAVE_API_KEY,
    HTTP_CA_BUNDLE,
    HTTP_MAX_BYTES,
    HTTP_TIMEOUT_S,
    HTTP_TRUST_ENV,
)
from aihub.db import append_event, now_ts
from aihub.memory_core import get_memory_core
from aihub.psyche_core import get_psyche_core

logger = logging.getLogger(__name__)

# Optional aggregation backends: failures are soft (Brave remains primary when configured).
_OPTIONAL_RESEARCH_BACKENDS = frozenset(
    {
        "Wikipedia",
        "DuckDuckGo",
        "GoogleNews",
        "Frankfurter",
        "OpenMeteo",
        "PyPI",
        "EndOfLife",
    }
)


def _optional_research_backends_enabled() -> bool:
    """Return whether public no-key research backends may be called.

    Default is off in CI/test/offline environments to prevent hidden network stalls.
    Enable explicitly with AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS=1.
    Brave remains available whenever BRAVE_API_KEY is configured.
    """
    value = os.getenv("AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _method_is_overridden(bound_method: Any, original_func: Any) -> bool:
    """True when tests/plugins replace a backend method with a mock/custom callable."""
    return getattr(bound_method, "__func__", None) is not original_func


_RESEARCH_USER_AGENT = "AIHub-Research/1.0 (+https://mordzixai.xyz)"


def _http_client_kwargs(timeout: float) -> dict[str, Any]:
    verify = (
        ssl.create_default_context(cafile=HTTP_CA_BUNDLE)
        if HTTP_CA_BUNDLE
        else ssl.create_default_context()
    )
    return {
        "timeout": timeout,
        "verify": verify,
        "trust_env": HTTP_TRUST_ENV,
        "headers": {"User-Agent": _RESEARCH_USER_AGENT},
    }


def _research_query_variants(query: str) -> List[str]:
    """Expand Polish/FX queries so optional backends return useful extracts."""
    variants = [query.strip()]
    ql = query.lower()
    if "python" in ql and ("wersj" in ql or "stable" in ql or "najnow" in ql):
        variants.extend(
            [
                "Python latest stable version",
                "Python (programming language)",
            ]
        )
    if ("eur" in ql and "pln" in ql) or ("euro" in ql and "złot" in ql):
        variants.extend(
            [
                "EUR PLN exchange rate",
                "Euro Polish zloty exchange rate today",
            ]
        )
    if "pogoda" in ql or "weather" in ql:
        if "warszaw" in ql or "warsaw" in ql:
            variants.extend(["Warsaw weather now", "pogoda Warszawa"])
        else:
            variants.append("current weather")
    if "fastapi" in ql and ("release" in ql or "wersj" in ql or "najnow" in ql or "stabil"):
        variants.extend(["FastAPI latest release PyPI", "fastapi pypi version"])

    news_markers = (
        "news",
        "wiadomo",
        "aktualno",
        "najnowsz",
        "ostatnich",
        "dzisiaj",
        "today",
        "latest",
    )
    ai_markers = (
        " ai ",
        "sztuczn",
        "artificial intelligence",
        "openai",
        "anthropic",
        "gemini",
        "chatgpt",
        "llm",
    )
    padded = f" {ql} "
    if any(marker in ql for marker in news_markers):
        if any(marker in padded for marker in ai_markers):
            variants.extend(
                [
                    "artificial intelligence latest news",
                    "AI industry latest news",
                    "OpenAI Anthropic Google AI latest news",
                ]
            )

    seen: set[str] = set()
    out: List[str] = []
    for v in variants:
        key = v.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(v.strip())
    return out


def _fx_pair_from_query(query: str) -> tuple[str, str] | None:
    ql = query.lower()
    if ("eur" in ql or "euro" in ql) and ("pln" in ql or "złot" in ql or "zlot" in ql):
        return "EUR", "PLN"
    return None


def _weather_location_from_query(query: str) -> tuple[float, float, str] | None:
    """Return (lat, lon, label) for known cities; default None."""
    ql = query.lower()
    if "pogoda" not in ql and "weather" not in ql:
        return None
    if "warszaw" in ql or "warsaw" in ql:
        return 52.2297, 21.0122, "Warsaw"
    return None


def _pypi_package_from_query(query: str) -> str | None:
    ql = query.lower()
    # FastAPI / package release lookups
    m = re.search(r"\b([a-z0-9][a-z0-9_.-]{1,40})\b", ql)
    if "fastapi" in ql and (
        "release" in ql or "wersj" in ql or "najnow" in ql or "stabil" in ql or "pypi" in ql
    ):
        return "fastapi"
    if m and "pypi" in ql:
        return m.group(1)
    return None


def _python_version_intent(query: str) -> bool:
    ql = query.lower()
    return "python" in ql and (
        "wersj" in ql or "stable" in ql or "najnow" in ql or "aktualn" in ql
    )


# ---- Hardening constants ----
RESEARCH_CACHE_TTL = 300  # seconds — skip same normalised query within this window
RESEARCH_MIN_FACT_LEN = 40
RESEARCH_MAX_FACT_LEN = 800
_BACKOFF_DELAYS = (0.2, 0.6, 1.5)
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "fbclid",
        "gclid",
        "ref",
        "source",
    }
)
_BOILERPLATE_RE = re.compile(
    r"(?i)\b(?:cookies?\s*(?:policy|settings)?|privacy\s*policy|javascript\s*required"
    r"|sign\s*in|log\s*in|accept\s*all|terms\s*of\s*(?:service|use)"
    r"|subscribe|newsletter|advertisement|click\s*here|cookie\s*settings)\b"
)


def normalize_query(q: str) -> str:
    """Lowercase, collapse whitespace, strip."""
    return re.sub(r"\s+", " ", q.strip().lower())


def normalize_url(url: str) -> str:
    """Lowercase host, strip tracking params, trim trailing slash."""
    try:
        p = urlparse(url.strip())
        if p.query:
            qs = parse_qs(p.query, keep_blank_values=False)
            clean_qs = {
                k: v for k, v in qs.items() if k.lower() not in _TRACKING_PARAMS
            }
            new_query = urlencode(clean_qs, doseq=True)
        else:
            new_query = ""
        return urlunparse(
            (p.scheme, p.netloc.lower(), p.path.rstrip("/"), p.params, new_query, "")
        )
    except Exception:
        return url.strip().lower()


def _research_fingerprint(user_id: str, backend: str, query: str, url: str) -> str:
    """Stable hash for (user, backend, query, url) dedup."""
    nq = normalize_query(query)
    nu = normalize_url(url)
    return hashlib.sha256(
        f"{user_id}\0research\0{backend}\0{nq}\0{nu}".encode()
    ).hexdigest()[:32]


def filter_research_text(text: str) -> Optional[str]:
    """Quality gate: normalise whitespace, reject short/boilerplate, truncate."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip())
    if len(t) < RESEARCH_MIN_FACT_LEN:
        return None
    if _BOILERPLATE_RE.search(t):
        return None
    return t[:RESEARCH_MAX_FACT_LEN]


def _http_get_with_backoff(client: httpx.Client, url: str, **kwargs) -> httpx.Response:
    """HTTP GET with retry on 429/5xx (3 retries, exponential backoff)."""
    for attempt in range(len(_BACKOFF_DELAYS) + 1):
        try:
            resp = client.get(url, **kwargs)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < len(_BACKOFF_DELAYS):
                    logger.warning(
                        "research.backoff: HTTP %d from %s, retry %d/%d in %.1fs",
                        resp.status_code,
                        url,
                        attempt + 1,
                        len(_BACKOFF_DELAYS),
                        _BACKOFF_DELAYS[attempt],
                    )
                    time.sleep(_BACKOFF_DELAYS[attempt])
                    continue
                resp.raise_for_status()
            return resp
        except httpx.HTTPError:
            if attempt < len(_BACKOFF_DELAYS):
                logger.warning(
                    "research.backoff: error from %s, retry %d/%d",
                    url,
                    attempt + 1,
                    len(_BACKOFF_DELAYS),
                )
                time.sleep(_BACKOFF_DELAYS[attempt])
                continue
            raise
    raise httpx.HTTPError(f"max retries exceeded for {url}")


@dataclass
class ResearchResult:
    """Wynik researchu."""

    title: str
    url: str
    content: str
    source: str
    relevance_score: float
    extraction_ts: float


class ResearchEngine:
    """
    Research Engine - system wyszukiwania i ekstrakcji wiedzy.

    Features:
    - Structured research queries
    - Result parsing and extraction
    - Knowledge integration
    - Source tracking
    - Relevance scoring
    """

    def __init__(self, max_results: int = 10):
        self.max_results = max_results
        self.extraction_patterns = self._init_patterns()
        self._query_cache: Dict[str, tuple[float, Dict[str, Any]]] = {}

    def _init_patterns(self) -> Dict[str, List[str]]:
        """Regex patterns dla ekstrakcji faktów."""
        return {
            "definition": [
                r"(?:is|jest|to)\s+(?:a|an)?\s*([^.!?]{20,200})[.!?]",
                r"(?:defined as|definiuje się jako)\s+([^.!?]{20,200})[.!?]",
            ],
            "statistics": [
                r"(\d+)\s*(?:%|\d+\s*(?:thousands|millions|billions))",
                r"(?:about|około|roughly)\s+(\d+)\s*(?:percent|%)",
            ],
            "date": [
                r"(?:in|w)\s+(19|20)\d{2}",
                r"(\d{1,2})\s+(?:of\s+)?(?:January|February|March|April|May|June|July|August|September|October|November|December|stycznia|lutego|marca|kwietnia|maja|czerwca|lipca|sierpnia|września|października|listopada|grudnia)",
            ],
            "claim": [
                r"(?:according to|wg\.?|według)\s+([^.!?]{20,150})",
                r"(?:research shows|badania wykazują)\s+([^.!?]{20,150})",
            ],
        }

    def _extract_facts_from_text(
        self,
        text: str,
        source_url: str,
        *,
        allow_fallback: bool = False,
        fallback_type: str = "source_statement",
    ) -> List[Dict[str, Any]]:
        """Extract structured facts, with an optional conservative source fallback."""
        facts: List[Dict[str, Any]] = []
        fallback_candidates: List[str] = []

        try:
            normalized_text = re.sub(r"&nbsp;?", " ", str(text or ""))
            normalized_text = re.sub(r"\s+", " ", normalized_text).strip()

            # Keep the publication date intact instead of splitting after weekday abbreviations.
            sentences = re.split(
                r"(?<=[.!?])\s+(?=[A-ZÀ-Ż0-9])",
                normalized_text,
            )

            if len(sentences) == 1:
                sentences = re.split(r"[.!?]+", normalized_text)

            seen_sentences: set[str] = set()

            for raw_sentence in sentences[:20]:
                sentence = re.sub(r"\s+", " ", raw_sentence).strip(" -\t\r\n")
                if len(sentence) < 20:
                    continue

                sentence_key = sentence.casefold()
                if sentence_key in seen_sentences:
                    continue
                seen_sentences.add(sentence_key)

                matched = False
                for pattern_type, patterns in self.extraction_patterns.items():
                    for pattern in patterns:
                        if re.search(pattern, sentence, re.IGNORECASE):
                            facts.append(
                                {
                                    "type": pattern_type,
                                    "extracted": sentence,
                                    "source_url": source_url,
                                    "extraction_ts": now_ts(),
                                    "confidence": 0.6,
                                }
                            )
                            matched = True
                            break
                    if matched:
                        break

                if not matched:
                    fallback_candidates.append(sentence)

            if allow_fallback and not facts:
                metadata_prefixes = (
                    "publisher:",
                    "published:",
                    "source:",
                    "author:",
                )

                for sentence in fallback_candidates:
                    lowered = sentence.casefold()

                    if lowered.startswith(metadata_prefixes):
                        continue
                    if len(sentence) < 35:
                        continue
                    if not re.search(r"[A-Za-zÀ-Żà-ż]", sentence):
                        continue

                    facts.append(
                        {
                            "type": fallback_type,
                            "extracted": sentence,
                            "source_url": source_url,
                            "extraction_ts": now_ts(),
                            "confidence": 0.4,
                        }
                    )

                    # RSS entries usually contain one useful headline/summary statement.
                    if len(facts) >= 2:
                        break

        except Exception as e:
            logger.debug("Error extracting facts: %s", e)

        return facts[:10]

    def _calculate_relevance(self, query: str, text: str) -> float:
        """Oblicz relevance score tekstu do query."""
        if not text or not query:
            return 0.0

        query_words = set(query.lower().split())
        text_words = text.lower().split()

        matches = sum(1 for w in text_words if w in query_words)
        max_matches = len(query_words)

        if max_matches == 0:
            return 0.0

        return min(1.0, matches / max_matches)

    async def research(
        self, user_id: str, query: str, research_type: str = "general"
    ) -> Dict[str, Any]:
        """
        Przeprowadź research na zadany temat.

        Args:
            user_id: ID użytkownika
            query: Zapytanie research
            research_type: Typ researchu (general, factual, technical)

        Returns:
            Dict z wynikami researchu
        """
        try:
            get_psyche_core().ensure_user(user_id)

            # Query-level dedup cache
            cache_key = f"{user_id}\0{normalize_query(query)}"
            cached = self._query_cache.get(cache_key)
            if cached:
                cached_ts, cached_payload = cached
                age = now_ts() - cached_ts
                if age < RESEARCH_CACHE_TTL and isinstance(cached_payload, dict):
                    logger.info(
                        "research.cached: user=%s query=%s (%.0fs ago)",
                        user_id,
                        query,
                        age,
                    )
                    return {**cached_payload, "cached": True}

            logger.info("Starting research for user %s: %s", user_id, query)
            try:
                get_psyche_core().v2_service.apply_event(
                    user_id=user_id,
                    event_type="web_research_triggered",
                    reason_text=f"research.query {query}",
                    source_ref=query,
                    signal_strength=0.55,
                    metadata={"research_type": research_type},
                )
            except Exception:
                logger.debug("psyche v2 research trigger event skipped", exc_info=True)

            results: List[Dict[str, Any]] = []

            search_results = await self._fetch_search_results(query)

            for result in search_results[: self.max_results]:
                try:
                    # Extract facts from result
                    source_name = str(result.get("source") or "unknown")
                    is_news_result = source_name in {
                        "google_news",
                        "bing_news",
                        "news_rss",
                    }

                    facts = self._extract_facts_from_text(
                        str(result.get("content") or ""),
                        str(result.get("url") or ""),
                        allow_fallback=is_news_result,
                        fallback_type="news_statement",
                    )

                    # Store facts in memory (quality gate + fingerprint dedup)
                    stored = 0
                    for fact in facts:
                        try:
                            cleaned = filter_research_text(fact["extracted"])
                            if cleaned is None:
                                logger.debug(
                                    "research.filtered: low-quality fact skipped"
                                )
                                continue
                            fp = _research_fingerprint(
                                user_id,
                                result.get("source", "unknown"),
                                query,
                                result["url"],
                            )
                            get_memory_core().ingest_fact(
                                user_id,
                                cleaned,
                                tags=[
                                    "research",
                                    fact["type"],
                                    normalize_query(query)[:30],
                                ],
                                meta={
                                    "source_url": fact["source_url"],
                                    "source_title": result["title"],
                                    "backend": result.get("source", "unknown"),
                                    "research_query": query,
                                    "research_type": research_type,
                                    "confidence": fact["confidence"],
                                    "research_fingerprint": fp,
                                },
                            )
                            stored += 1
                        except Exception as e:
                            logger.debug("Error storing fact: %s", e)

                    # Calculate relevance
                    relevance = self._calculate_relevance(query, result["content"])

                    results.append(
                        {
                            "title": result["title"],
                            "url": result["url"],
                            "content": str(result.get("content") or "")[:800],
                            "relevance": relevance,
                            "facts_extracted": stored,
                            "source": result.get("source", "unknown"),
                            "published_at": result.get("published_at"),
                            "publisher": result.get("publisher"),
                        }
                    )

                except Exception as e:
                    logger.warning(f"Error processing result: {e}")

            try:
                get_psyche_core().v2_service.apply_event(
                    user_id=user_id,
                    event_type="tool_success",
                    reason_text="research.query completed",
                    source_ref=query,
                    signal_strength=0.5 if results else 0.35,
                    metadata={
                        "research_type": research_type,
                        "results": len(results),
                        "facts": sum(r.get("facts_extracted", 0) for r in results),
                    },
                )
            except Exception:
                logger.debug("psyche v2 research completion event skipped", exc_info=True)

            # Log research completion
            append_event(
                user_id,
                "research.completed",
                {
                    "query": query,
                    "type": research_type,
                    "results_count": len(results),
                    "facts_extracted": sum(
                        r.get("facts_extracted", 0) for r in results
                    ),
                },
            )

            payload = {
                "ok": True,
                "user_id": user_id,
                "query": query,
                "type": research_type,
                "results": results,
                "total_results": len(results),
                "total_facts": sum(r.get("facts_extracted", 0) for r in results),
                "ts": now_ts(),
            }
            self._query_cache[cache_key] = (now_ts(), dict(payload))

            logger.info(
                "Research completed for user %s: %d results, %d facts",
                user_id,
                len(results),
                sum(r.get("facts_extracted", 0) for r in results),
            )

            return payload

        except Exception as e:
            logger.error(f"Error in research: {e}", exc_info=True)
            try:
                get_psyche_core().v2_service.apply_event(
                    user_id=user_id,
                    event_type="tool_failure",
                    reason_text=f"research.query failed: {str(e)[:240]}",
                    source_ref=query,
                    signal_strength=0.55,
                    metadata={"research_type": research_type},
                )
            except Exception:
                logger.debug("psyche v2 research failure event skipped", exc_info=True)
            append_event(user_id, "research.error", {"query": query, "error": str(e)})
            return {
                "ok": False,
                "user_id": user_id,
                "query": query,
                "error": str(e),
                "ts": now_ts(),
            }

    async def _fetch_search_results(self, query: str) -> List[Dict[str, Any]]:
        """Fetch results from Brave Search, Wikipedia, and DuckDuckGo in parallel."""
        results: List[Dict[str, Any]] = []

        tasks: List[Awaitable[Any]] = []
        names: List[str] = []

        if BRAVE_API_KEY:
            tasks.append(asyncio.to_thread(self._fetch_brave, query))
            names.append("Brave")

        optional_enabled = (
            _optional_research_backends_enabled()
            or _method_is_overridden(self._fetch_wikipedia, ResearchEngine._fetch_wikipedia)
            or _method_is_overridden(self._fetch_duckduckgo, ResearchEngine._fetch_duckduckgo)
        )
        if optional_enabled:
            tasks.append(asyncio.to_thread(self._fetch_wikipedia, query))
            names.append("Wikipedia")

            tasks.append(asyncio.to_thread(self._fetch_duckduckgo, query))
            names.append("DuckDuckGo")

            tasks.append(asyncio.to_thread(self._fetch_google_news, query))
            names.append("GoogleNews")

            if _fx_pair_from_query(query):
                tasks.append(asyncio.to_thread(self._fetch_frankfurter_fx, query))
                names.append("Frankfurter")
            if _weather_location_from_query(query):
                tasks.append(asyncio.to_thread(self._fetch_open_meteo, query))
                names.append("OpenMeteo")
            if _pypi_package_from_query(query):
                tasks.append(asyncio.to_thread(self._fetch_pypi, query))
                names.append("PyPI")
            if _python_version_intent(query):
                tasks.append(asyncio.to_thread(self._fetch_python_endoflife, query))
                names.append("EndOfLife")

        if not tasks:
            logger.info(
                "ResearchEngine: no configured online backend for query=%s (set BRAVE_API_KEY or AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS=1)",
                query,
            )
            return []

        gathered = await asyncio.gather(*tasks, return_exceptions=True)

        for backend_name, result in zip(names, gathered):
            if isinstance(result, Exception):
                if backend_name in _OPTIONAL_RESEARCH_BACKENDS:
                    logger.debug(
                        "Research optional backend %s skipped: %s query=%r",
                        backend_name,
                        result,
                        query[:400],
                    )
                else:
                    logger.warning(
                        "Research backend %s failed: %s query=%r",
                        backend_name,
                        result,
                        query[:400],
                    )
                continue
            if isinstance(result, list):
                results.extend(result)

        if not results:
            logger.info(
                "ResearchEngine: no results from any source for query=%s", query
            )
            return results
        # Prefer specialist backends (fresh structured facts) ahead of generic SERP noise.
        _prio = {
            "frankfurter": 0,
            "open_meteo": 0,
            "pypi": 0,
            "endoflife": 0,
            "google_news": 1,
            "brave": 1,
            "wikipedia": 2,
            "duckduckgo": 3,
            "duckduckgo_html": 3,
        }
        results.sort(key=lambda r: _prio.get(str((r or {}).get("source") or ""), 9))
        return results[:8]

    # ------ real search backends ------

    def _fetch_brave(self, query: str) -> List[Dict[str, Any]]:
        """Brave Web Search API — up to 5 organic results."""
        out: List[Dict[str, Any]] = []
        try:
            with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S)) as client:
                resp = _http_get_with_backoff(
                    client,
                    "https://api.search.brave.com/res/v1/web/search",
                    params={"q": query, "count": "5"},
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "gzip",
                        "X-Subscription-Token": BRAVE_API_KEY,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("web", {}).get("results", [])[:5]:
                    title = item.get("title", "")
                    url = item.get("url", "")
                    description = item.get("description", "")
                    extra = item.get("extra_snippets", [])
                    content = description
                    if extra:
                        content = description + " " + " ".join(extra)
                    if content and len(content) > 30:
                        out.append(
                            {
                                "title": title,
                                "url": url,
                                "content": content[:HTTP_MAX_BYTES],
                                "source": "brave",
                            }
                        )
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code or 0)
            if status in {401, 402, 403, 429} or status >= 500:
                logger.info(
                    "Research backend Brave soft-fail HTTP %s query=%r — trying optional backends",
                    status,
                    query[:120],
                )
                return []
            raise
        except httpx.HTTPError as exc:
            logger.warning("Brave search failed: %s query=%r", exc, query[:120])
        return out

    def _fetch_wikipedia(self, query: str) -> List[Dict[str, Any]]:
        """Search Wikipedia via REST API and return up to 3 article extracts."""
        out: List[Dict[str, Any]] = []
        for variant in _research_query_variants(query):
            if out:
                break
            try:
                with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S)) as client:
                    resp = _http_get_with_backoff(
                        client,
                        "https://en.wikipedia.org/w/api.php",
                        params={
                            "action": "opensearch",
                            "search": variant,
                            "limit": "3",
                            "format": "json",
                        },
                    )
                    data = resp.json()
                    titles = data[1] if len(data) > 1 else []
                    urls = data[3] if len(data) > 3 else []

                    for i, title in enumerate(titles[:3]):
                        try:
                            ext_resp = _http_get_with_backoff(
                                client,
                                "https://en.wikipedia.org/w/api.php",
                                params={
                                    "action": "query",
                                    "prop": "extracts",
                                    "exintro": "1",
                                    "explaintext": "1",
                                    "titles": title,
                                    "format": "json",
                                },
                            )
                            pages = ext_resp.json().get("query", {}).get("pages", {})
                            for page in pages.values():
                                extract = page.get("extract", "")
                                if len(extract) > 30:
                                    out.append(
                                        {
                                            "title": title,
                                            "url": urls[i] if i < len(urls) else "",
                                            "content": extract[:HTTP_MAX_BYTES],
                                            "source": "wikipedia",
                                        }
                                    )
                        except httpx.HTTPError as e:
                            logger.debug(
                                "Wikipedia extract fetch failed for %s: %s", title, e
                            )
            except httpx.HTTPError as e:
                logger.debug("Wikipedia opensearch failed for %r: %s", variant, e)
        return out

    def _fetch_frankfurter_fx(self, query: str) -> List[Dict[str, Any]]:
        """ECB-backed FX rates via Frankfurter (no API key)."""
        pair = _fx_pair_from_query(query)
        if pair is None:
            return []
        base, quote = pair
        out: List[Dict[str, Any]] = []
        try:
            with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S), follow_redirects=True) as client:
                resp = _http_get_with_backoff(
                    client,
                    "https://api.frankfurter.dev/v1/latest",
                    params={"base": base, "symbols": quote},
                )
                data = resp.json()
                rate = (data.get("rates") or {}).get(quote)
                if rate is None:
                    return []
                day = str(data.get("date") or "")
                content = (
                    f"Exchange rate {base}/{quote} on {day}: 1 {base} = {rate} {quote}. "
                    f"Source: Frankfurter API (ECB reference rates)."
                )
                out.append(
                    {
                        "title": f"{base}/{quote} exchange rate {day}".strip(),
                        "url": f"https://api.frankfurter.dev/v1/latest?base={base}&symbols={quote}",
                        "content": content,
                        "source": "frankfurter",
                    }
                )
        except httpx.HTTPError as exc:
            logger.debug("Frankfurter FX fetch failed: %s", exc)
        return out

    def _fetch_open_meteo(self, query: str) -> List[Dict[str, Any]]:
        """Current weather via Open-Meteo (no API key)."""
        loc = _weather_location_from_query(query)
        if loc is None:
            return []
        lat, lon, label = loc
        out: List[Dict[str, Any]] = []
        try:
            with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S), follow_redirects=True) as client:
                resp = _http_get_with_backoff(
                    client,
                    "https://api.open-meteo.com/v1/forecast",
                    params={
                        "latitude": str(lat),
                        "longitude": str(lon),
                        "current": "temperature_2m,weather_code,wind_speed_10m",
                        "timezone": "auto",
                    },
                )
                data = resp.json()
                cur = data.get("current") or {}
                ts = str(cur.get("time") or "")
                temp = cur.get("temperature_2m")
                code = cur.get("weather_code")
                wind = cur.get("wind_speed_10m")
                if temp is None:
                    return []
                content = (
                    f"Current weather in {label} at {ts}: {temp}°C, "
                    f"WMO weather_code={code}, wind={wind} km/h. "
                    f"Source: Open-Meteo."
                )
                out.append(
                    {
                        "title": f"{label} current weather {ts}".strip(),
                        "url": (
                            "https://api.open-meteo.com/v1/forecast"
                            f"?latitude={lat}&longitude={lon}&current=temperature_2m"
                        ),
                        "content": content,
                        "source": "open_meteo",
                    }
                )
        except httpx.HTTPError as exc:
            logger.debug("Open-Meteo fetch failed: %s", exc)
        return out

    def _fetch_pypi(self, query: str) -> List[Dict[str, Any]]:
        """Latest package version from PyPI JSON API."""
        pkg = _pypi_package_from_query(query)
        if not pkg:
            return []
        out: List[Dict[str, Any]] = []
        try:
            with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S), follow_redirects=True) as client:
                resp = _http_get_with_backoff(
                    client,
                    f"https://pypi.org/pypi/{pkg}/json",
                )
                if resp.status_code >= 400:
                    return []
                data = resp.json()
                info = data.get("info") or {}
                ver = str(info.get("version") or "").strip()
                if not ver:
                    return []
                url = str(info.get("release_url") or f"https://pypi.org/project/{pkg}/{ver}/")
                content = (
                    f"PyPI package {pkg} latest stable release: {ver}. "
                    f"Source: https://pypi.org/pypi/{pkg}/json"
                )
                out.append(
                    {
                        "title": f"{pkg} {ver} on PyPI",
                        "url": url,
                        "content": content,
                        "source": "pypi",
                    }
                )
        except httpx.HTTPError as exc:
            logger.debug("PyPI fetch failed: %s", exc)
        return out

    def _fetch_python_endoflife(self, query: str) -> List[Dict[str, Any]]:
        """Current Python releases via endoflife.date (no API key)."""
        if not _python_version_intent(query):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S), follow_redirects=True) as client:
                resp = _http_get_with_backoff(
                    client,
                    "https://endoflife.date/api/python.json",
                )
                if resp.status_code >= 400:
                    return []
                rows = resp.json()
                if not isinstance(rows, list) or not rows:
                    return []
                # Prefer the newest cycle that is not EOL.
                stable = None
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if row.get("eol") is False or (
                        isinstance(row.get("eol"), str) and row.get("eol") > "2026-01-01"
                    ):
                        stable = row
                        break
                if stable is None:
                    stable = rows[0] if isinstance(rows[0], dict) else None
                if not stable:
                    return []
                cycle = str(stable.get("cycle") or "")
                latest = str(stable.get("latest") or cycle)
                content = (
                    f"Python latest stable release per endoflife.date: {latest} "
                    f"(cycle {cycle}, releaseDate={stable.get('releaseDate')}, "
                    f"lts={stable.get('lts')}). Source: https://endoflife.date/api/python.json"
                )
                out.append(
                    {
                        "title": f"Python {latest} (endoflife.date)",
                        "url": "https://endoflife.date/python",
                        "content": content,
                        "source": "endoflife",
                    }
                )
        except httpx.HTTPError as exc:
            logger.debug("endoflife.date Python fetch failed: %s", exc)
        return out

    def _fetch_google_news(self, query: str) -> List[Dict[str, Any]]:
        """Search current news through Google News RSS without an API key."""
        out: List[Dict[str, Any]] = []
        seen_urls: set[str] = set()

        try:
            with httpx.Client(
                **_http_client_kwargs(HTTP_TIMEOUT_S),
                follow_redirects=True,
            ) as client:
                for variant in _research_query_variants(query):
                    params = {
                        "q": variant,
                        "hl": "en-US",
                        "gl": "US",
                        "ceid": "US:en",
                    }
                    resp = _http_get_with_backoff(
                        client,
                        "https://news.google.com/rss/search",
                        params=params,
                    )

                    if resp.status_code >= 400:
                        logger.debug(
                            "Google News RSS HTTP %s query=%r",
                            resp.status_code,
                            variant,
                        )
                        continue

                    try:
                        root = ET.fromstring(resp.content)
                    except ET.ParseError as exc:
                        logger.debug(
                            "Google News RSS XML parse failed query=%r: %s",
                            variant,
                            exc,
                        )
                        continue

                    for item in root.findall(".//item"):
                        title = str(item.findtext("title") or "").strip()
                        url = str(item.findtext("link") or "").strip()
                        description = str(
                            item.findtext("description") or ""
                        ).strip()
                        published = str(item.findtext("pubDate") or "").strip()
                        source_node = item.find("source")
                        publisher = (
                            str(source_node.text or "").strip()
                            if source_node is not None
                            else ""
                        )

                        description = re.sub(
                            r"<[^>]+>",
                            " ",
                            description,
                        )
                        description = re.sub(
                            r"\s+",
                            " ",
                            description,
                        ).strip()

                        if not title or not url or url in seen_urls:
                            continue

                        content_parts = [title]
                        if description and description.lower() != title.lower():
                            content_parts.append(description)
                        if publisher:
                            content_parts.append(f"Publisher: {publisher}.")
                        if published:
                            content_parts.append(f"Published: {published}.")

                        content = " ".join(content_parts).strip()
                        if len(content) <= 30:
                            continue

                        seen_urls.add(url)
                        out.append(
                            {
                                "title": title[:300],
                                "url": url,
                                "content": content[:HTTP_MAX_BYTES],
                                "source": "google_news",
                                "published_at": published,
                                "publisher": publisher,
                            }
                        )

                        if len(out) >= 8:
                            return out

                    if out:
                        break

        except httpx.HTTPError as exc:
            logger.debug(
                "Google News RSS fetch failed query=%r: %s",
                query[:200],
                exc,
            )

        return out

    def _fetch_duckduckgo_html(self, query: str) -> List[Dict[str, Any]]:
        """HTML search fallback when instant-answer API has no abstract."""
        out: List[Dict[str, Any]] = []
        try:
            with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S), follow_redirects=True) as client:
                for variant in _research_query_variants(query):
                    resp = client.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": variant, "b": "", "kl": "wt-wt"},
                    )
                    if resp.status_code >= 400:
                        continue
                    html = resp.text or ""
                    for block in re.findall(
                        r'class="result__body".*?(?=class="result__body"|$)',
                        html,
                        flags=re.DOTALL,
                    ):
                        link_m = re.search(
                            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                            block,
                            flags=re.DOTALL,
                        )
                        snip_m = re.search(
                            r'class="result__snippet"[^>]*>(.*?)</(?:a|td|span)',
                            block,
                            flags=re.DOTALL,
                        )
                        if not link_m:
                            continue
                        url = re.sub(r"\s+", " ", link_m.group(1).strip())
                        title = re.sub(r"<[^>]+>", " ", link_m.group(2))
                        title = re.sub(r"\s+", " ", title).strip()
                        snippet = ""
                        if snip_m:
                            snippet = re.sub(r"<[^>]+>", " ", snip_m.group(1))
                            snippet = re.sub(r"\s+", " ", snippet).strip()
                        content = snippet or title
                        if len(content) > 30:
                            out.append(
                                {
                                    "title": title[:200] or variant,
                                    "url": url,
                                    "content": content[:HTTP_MAX_BYTES],
                                    "source": "duckduckgo_html",
                                }
                            )
                        if len(out) >= 5:
                            break
                    if out:
                        break
        except httpx.HTTPError as e:
            logger.debug("DuckDuckGo HTML search failed: %s", e)
        return out

    def _fetch_duckduckgo(self, query: str) -> List[Dict[str, Any]]:
        """Fetch DuckDuckGo instant answer API (no API key needed)."""
        out: List[Dict[str, Any]] = []
        try:
            for variant in _research_query_variants(query):
                with httpx.Client(**_http_client_kwargs(HTTP_TIMEOUT_S)) as client:
                    resp = _http_get_with_backoff(
                        client,
                        "https://api.duckduckgo.com/",
                        params={"q": variant, "format": "json", "no_redirect": "1"},
                    )
                    data = resp.json()

                    abstract = data.get("AbstractText", "")
                    abstract_url = data.get("AbstractURL", "")
                    if abstract and len(abstract) > 30:
                        out.append(
                            {
                                "title": data.get("Heading", variant),
                                "url": abstract_url,
                                "content": abstract[:HTTP_MAX_BYTES],
                                "source": "duckduckgo",
                            }
                        )

                    for topic in data.get("RelatedTopics", [])[:5]:
                        text = topic.get("Text", "")
                        first_url = topic.get("FirstURL", "")
                        if text and len(text) > 30:
                            out.append(
                                {
                                    "title": text[:80],
                                    "url": first_url,
                                    "content": text[:HTTP_MAX_BYTES],
                                    "source": "duckduckgo",
                                }
                            )
                if out:
                    break
        except httpx.HTTPError as e:
            logger.warning("DuckDuckGo API failed: %s", e)
        if not out:
            out.extend(self._fetch_duckduckgo_html(query))
        return out

    def research_detailed(
        self, user_id: str, topic: str, subtopics: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Detailed research na multiple subtopics.

        Args:
            user_id: ID użytkownika
            topic: Główny temat
            subtopics: Dodatkowe subtopics do research

        Returns:
            Dict z detailed results
        """
        try:
            get_psyche_core().ensure_user(user_id)

            subtopics = subtopics or [topic]
            all_results = {
                "main_topic": topic,
                "subtopics": {},
                "total_facts": 0,
                "started_at": now_ts(),
            }

            for subtopic in subtopics[:5]:  # Limit to 5 subtopics
                try:
                    # Perform research on each subtopic
                    import asyncio

                    result = asyncio.run(self.research(user_id, subtopic, "detailed"))

                    if result.get("ok"):
                        all_results["subtopics"][subtopic] = result
                        all_results["total_facts"] += result.get("total_facts", 0)

                except Exception as e:
                    logger.warning(f"Error researching subtopic {subtopic}: {e}")

            all_results["completed_at"] = now_ts()
            all_results["ok"] = True

            return all_results

        except Exception as e:
            logger.error(f"Error in research_detailed: {e}", exc_info=True)
            return {"ok": False, "topic": topic, "error": str(e), "ts": now_ts()}


# Singleton
_research_engine = ResearchEngine()


async def research(
    user_id: str, query: str, research_type: str = "general"
) -> Dict[str, Any]:
    """Public API dla researchu."""
    return await _research_engine.research(user_id, query, research_type)


def research_detailed(
    user_id: str, topic: str, subtopics: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Public API dla detailed research."""
    return _research_engine.research_detailed(user_id, topic, subtopics)
