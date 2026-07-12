#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical web runtime: safe URL fetch, HTML extraction, research ingestion hooks.

This module is the one production implementation used by HTTP, tool registry,
agent/chat controlled web prefetch and the ``aihub.web`` package adapters.
It keeps SSRF checks, redirect validation, content limits and audit events in
one place.
"""

from __future__ import annotations

import html
import ipaddress
import logging
import re
import socket
import ssl
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, Iterable
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from aihub.config import (
    HTTP_CA_BUNDLE,
    HTTP_MAX_BYTES,
    HTTP_MAX_REDIRECTS,
    HTTP_TIMEOUT_S,
    HTTP_TRUST_ENV,
)
from aihub.db import append_event

logger = logging.getLogger(__name__)

_TEXT_CONTENT_TYPES = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml+xml",
    "application/rss+xml",
    "application/atom+xml",
)

_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


class WebResponseTooLarge(ValueError):
    """Raised as soon as the decoded response exceeds the configured hard cap."""


async def _read_response_body_limited(
    response: httpx.Response, *, max_bytes: int
) -> bytes:
    if max_bytes <= 0:
        raise ValueError("HTTP_MAX_BYTES must be positive")
    declared = response.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > max_bytes:
        raise WebResponseTooLarge(f"response exceeds {max_bytes} bytes")

    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > max_bytes:
            raise WebResponseTooLarge(f"decoded response exceeds {max_bytes} bytes")
        body.extend(chunk)
    return bytes(body)


class _HTMLTextExtractor(HTMLParser):
    """Small stdlib HTML extractor: title, visible text and hrefs."""

    def __init__(self, *, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._skip_stack: list[str] = []
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.links: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_link_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attrs_dict = {k.lower(): v or "" for k, v in attrs}
        if t in {"script", "style", "noscript", "svg", "canvas"}:
            self._skip_stack.append(t)
            return
        if t == "title":
            self._in_title = True
        if t == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                self._current_href = urljoin(self.base_url, href)
                self._current_link_text = []
        if t in {"p", "div", "section", "article", "header", "footer", "br", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if self._skip_stack and self._skip_stack[-1] == t:
            self._skip_stack.pop()
            return
        if t == "title":
            self._in_title = False
        if t == "a" and self._current_href:
            label = _clean_text(" ".join(self._current_link_text))[:160]
            self.links.append({"url": self._current_href, "text": label})
            self._current_href = None
            self._current_link_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        value = html.unescape(data or "")
        if not value.strip():
            return
        if self._in_title:
            self.title_parts.append(value)
        self.text_parts.append(value)
        if self._current_href is not None:
            self._current_link_text.append(value)


def _clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def _build_ssl_context() -> ssl.SSLContext:
    if HTTP_CA_BUNDLE:
        return ssl.create_default_context(cafile=HTTP_CA_BUNDLE)
    return ssl.create_default_context()


def _parse_url(url: str) -> Any:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("unsupported url scheme")
    if not parsed.hostname:
        raise ValueError("url has no hostname")
    if parsed.username or parsed.password:
        raise ValueError("url credentials are not allowed")
    return parsed


def _ip_is_private_or_reserved(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> Iterable[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return []
    out: list[str] = []
    for info in infos:
        try:
            out.append(str(info[4][0]))
        except Exception:  # noqa: BLE001
            continue
    return sorted(set(out))


def validate_url_safe(url: str, *, resolve_dns: bool = True) -> str:
    """Validate URL against scheme, credentials and local/private targets."""
    parsed = _parse_url(url)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        raise ValueError(f"SSRF protection: blocked hostname: {host}")
    if _ip_is_private_or_reserved(host):
        raise ValueError(f"SSRF protection: blocked private/reserved ip: {host}")
    if resolve_dns:
        resolved = list(_resolve_host_ips(host))
        if not resolved:
            raise ValueError(f"SSRF protection: hostname did not resolve: {host}")
        for ip in resolved:
            if _ip_is_private_or_reserved(ip):
                raise ValueError(
                    f"SSRF protection: hostname resolves to private/reserved ip: {host} -> {ip}"
                )
    return parsed.geturl()


@dataclass(frozen=True)
class _PinnedTarget:
    logical_url: str
    connect_url: str
    host_header: str
    sni_hostname: str


def _resolve_and_pin_url(url: str) -> _PinnedTarget:
    logical_url = validate_url_safe(url, resolve_dns=False)
    parsed = _parse_url(logical_url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    addresses = list(_resolve_host_ips(hostname))
    if not addresses:
        raise ValueError(f"SSRF protection: hostname did not resolve: {hostname}")
    for address in addresses:
        if _ip_is_private_or_reserved(address):
            raise ValueError(
                f"SSRF protection: hostname resolves to private/reserved ip: {hostname}"
            )

    pinned_ip = sorted(set(addresses))[0]
    ip_literal = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    connect_netloc = (
        f"{ip_literal}:{parsed.port}" if parsed.port is not None else ip_literal
    )
    default_port = 443 if parsed.scheme == "https" else 80
    host_header = (
        f"{hostname}:{parsed.port}"
        if parsed.port is not None and parsed.port != default_port
        else hostname
    )
    connect_url = urlunparse(parsed._replace(netloc=connect_netloc))
    return _PinnedTarget(
        logical_url=logical_url,
        connect_url=connect_url,
        host_header=host_header,
        sni_hostname=hostname,
    )


def extract_page_text(
    content: bytes,
    *,
    url: str,
    content_type: str = "",
    max_chars: int = 20_000,
) -> dict[str, Any]:
    """Extract title/text/links using stdlib only; works for HTML and plain text."""
    raw = content[:HTTP_MAX_BYTES]
    ctype = (content_type or "").lower()
    looks_html = b"<html" in raw[:1000].lower() or b"<!doctype html" in raw[:1000].lower()
    is_html = "html" in ctype or looks_html
    decoded = raw.decode("utf-8", errors="replace")
    if not is_html:
        return {
            "title": "",
            "text": _clean_text(decoded)[:max_chars],
            "links": [],
            "extraction": "plain_text",
        }

    parser = _HTMLTextExtractor(base_url=url)
    parser.feed(decoded)
    parser.close()
    title = _clean_text(" ".join(parser.title_parts))[:300]
    text = _clean_text(" ".join(parser.text_parts))[:max_chars]
    links = []
    seen: set[str] = set()
    for link in parser.links:
        u = str(link.get("url") or "").strip()
        if not u or u in seen or not u.startswith(("http://", "https://")):
            continue
        seen.add(u)
        links.append({"url": u, "text": str(link.get("text") or "")[:160]})
        if len(links) >= 50:
            break
    return {"title": title, "text": text, "links": links, "extraction": "html_stdlib"}


def _is_text_content_type(content_type: str) -> bool:
    ctype = (content_type or "").lower().split(";", 1)[0].strip()
    if not ctype:
        return True
    return any(ctype.startswith(prefix) for prefix in _TEXT_CONTENT_TYPES)


async def fetch_url(user_id: str, url: str) -> Dict[str, Any]:
    """Fetch a public HTTP(S) URL with redirect SSRF validation and text extraction."""
    safe_url = validate_url_safe(str(url or "").strip(), resolve_dns=False)
    timeout = httpx.Timeout(HTTP_TIMEOUT_S)
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            headers={"User-Agent": "AIHub/1.0"},
            verify=_build_ssl_context(),
            trust_env=HTTP_TRUST_ENV,
        ) as client:
            current_url = safe_url
            redirect_count = 0
            while True:
                target = _resolve_and_pin_url(current_url)
                async with client.stream(
                    "GET",
                    target.connect_url,
                    headers={"Host": target.host_header},
                    extensions={"sni_hostname": target.sni_hostname},
                ) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        redirect_count += 1
                        if redirect_count > HTTP_MAX_REDIRECTS:
                            raise ValueError(
                                f"too many redirects (max: {HTTP_MAX_REDIRECTS})"
                            )
                        location = response.headers.get("location", "").strip()
                        if not location:
                            raise ValueError("redirect without Location header")
                        next_url = urljoin(target.logical_url, location)
                        current_url = validate_url_safe(next_url, resolve_dns=False)
                        continue

                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "")
                    if not _is_text_content_type(content_type):
                        raise ValueError(
                            f"unsupported content-type for text fetch: {content_type}"
                        )
                    content = await _read_response_body_limited(
                        response, max_bytes=HTTP_MAX_BYTES
                    )
                    extracted = extract_page_text(
                        content,
                        url=target.logical_url,
                        content_type=content_type,
                        max_chars=min(HTTP_MAX_BYTES, 20_000),
                    )
                    out = {
                        "ok": True,
                        "url": target.logical_url,
                        "status": response.status_code,
                        "headers": dict(response.headers),
                        "content_type": content_type,
                        "bytes": len(content),
                        "title": extracted.get("title", ""),
                        "text": extracted.get("text", ""),
                        "links": extracted.get("links", []),
                        "extraction": extracted.get("extraction", ""),
                        "redirects_followed": redirect_count,
                    }
                    append_event(
                        user_id,
                        "web.fetch",
                        {
                            "url": out["url"],
                            "status": out["status"],
                            "bytes": out["bytes"],
                            "title": out.get("title", ""),
                            "links": len(out.get("links") or []),
                        },
                    )
                    return out
    except Exception as err:
        append_event(user_id, "web.fetch.error", {"url": safe_url, "error": str(err)})
        raise


async def ingest_url(
    user_id: str,
    url: str,
    *,
    importance: float = 0.6,
    confidence: float = 0.72,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Fetch URL and store extracted page text into canonical memory pipelines."""
    fetched = await fetch_url(user_id, url)
    text = str(fetched.get("text") or "").strip()
    title = str(fetched.get("title") or fetched.get("url") or url).strip()[:300]
    if not text:
        raise ValueError("web page produced empty extracted text")

    memory_ids: dict[str, Any] = {"v1_fact": None, "v2_item": None}
    try:
        from aihub.memory_core import get_memory_core

        core = get_memory_core()
        memory_ids["v1_fact"] = core.ingest_fact(
            user_id,
            f"WEB[{fetched.get('url')}]: {text[:3000]}",
            tags=["web", "ingest", "url"],
            meta={
                "source_url": fetched.get("url"),
                "source_title": title,
                "content_type": fetched.get("content_type"),
                "bytes": fetched.get("bytes"),
            },
        )
        v2 = core.v2_create_item(
            user_id=user_id,
            memory_type="fact",
            scope="domain",
            title=f"WEB: {title}",
            content=f"URL: {fetched.get('url')}\nTITLE: {title}\n\n{text[:8000]}",
            source_kind="explicit_learning",
            source_ref=str(fetched.get("url") or url),
            session_id=session_id,
            importance_score=float(importance),
            emotional_weight=0.0,
            confidence_score=float(confidence),
        )
        memory_ids["v2_item"] = getattr(v2, "id", None)
    except Exception as exc:
        append_event(user_id, "web.ingest.memory_error", {"url": url, "error": str(exc)[:500]})
        raise

    append_event(
        user_id,
        "web.ingest",
        {
            "url": fetched.get("url"),
            "title": title,
            "memory_ids": memory_ids,
            "bytes": fetched.get("bytes"),
        },
    )
    return {"ok": True, "fetch": fetched, "memory_ids": memory_ids}


def _brave_token_live_status(api_key: str) -> dict[str, Any]:
    """Best-effort live validation of the Brave subscription token.

    Only called when the operator opts into live provider probing. A present but
    invalid/expired token returns HTTP 422 (SUBSCRIPTION_TOKEN_INVALID) or 401/403,
    which must NOT be reported as a working web backend.
    """
    try:
        with httpx.Client(
            timeout=min(HTTP_TIMEOUT_S, 8.0),
            trust_env=HTTP_TRUST_ENV,
            follow_redirects=False,
        ) as client:
            resp = client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": "ping", "count": "1"},
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": api_key,
                },
            )
        if resp.status_code == 200:
            return {"probed": True, "valid": True, "http_status": 200}
        if resp.status_code in (401, 403, 422):
            return {
                "probed": True,
                "valid": False,
                "http_status": resp.status_code,
                "reason": "brave_token_invalid",
            }
        return {
            "probed": True,
            "valid": None,
            "http_status": resp.status_code,
            "reason": "brave_probe_inconclusive",
        }
    except Exception as exc:  # noqa: BLE001
        return {"probed": True, "valid": None, "reason": f"brave_probe_error:{type(exc).__name__}"}


def web_health() -> dict[str, Any]:
    """Non-secret web capability health."""
    import os

    from aihub.config import BRAVE_API_KEY

    brave_key = (BRAVE_API_KEY or "").strip()
    brave_configured = bool(brave_key)
    optional_public_backends = (
        os.getenv("AIHUB_ENABLE_OPTIONAL_RESEARCH_BACKENDS", "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )

    research: dict[str, Any] = {
        "brave_configured": brave_configured,
        "optional_public_backends": optional_public_backends,
    }

    live_probe = (
        os.getenv("AIHUB_HEALTH_LIVE_PROVIDER_PROBE", "0").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    if live_probe and brave_configured:
        research["brave_live"] = _brave_token_live_status(brave_key)

    # A backend is usable when Brave has a valid token (or was not live-probed away)
    # or when keyless public backends are enabled. A present-but-invalid Brave token
    # with no public backends means web research cannot actually run.
    brave_usable = brave_configured and research.get("brave_live", {}).get("valid") is not False
    ok = bool(brave_usable or optional_public_backends)

    return {
        "ok": ok,
        "fetch": {
            "enabled": True,
            "timeout_s": HTTP_TIMEOUT_S,
            "max_bytes": HTTP_MAX_BYTES,
            "max_redirects": HTTP_MAX_REDIRECTS,
            "ssrf_dns_resolution": True,
        },
        "research": research,
    }
