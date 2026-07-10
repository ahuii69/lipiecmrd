"""Compatibility adapter over the canonical web runtime.

The production implementation lives in :mod:`aihub.web_tools`.  This module is
kept for older imports and manual callers, but every operation delegates to the
same safe fetch/extract/ingest stack used by HTTP routes and tools.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Tuple

from aihub.web_tools import extract_page_text, fetch_url, ingest_url as ingest_url_async, validate_url_safe


def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError("aihub.web.service sync adapter cannot run inside an active event loop; use aihub.web_tools async functions")


def fetch(url: str) -> Tuple[int, Dict[str, str], bytes]:
    """Synchronous compatibility fetch returning status, headers, bytes.

    This intentionally delegates to the canonical async fetch implementation so
    legacy imports get the same SSRF checks, redirect validation, content limits
    and audit behavior as HTTP/tools.  The old adapter used ``follow_redirects``
    directly, which bypassed redirect re-validation.
    """
    result = _run(fetch_url("default", url))
    headers = {str(k): str(v) for k, v in dict(result.get("headers") or {}).items()}
    body = str(result.get("text") or "").encode("utf-8")
    return int(result.get("status") or 200), headers, body


def extract_text(html_bytes: bytes) -> str:
    return str(extract_page_text(html_bytes, url="https://local.invalid/", content_type="text/html").get("text") or "")


def ingest_url(url: str, importance: float = 0.6, kind: str = "fact") -> Dict[str, Any]:
    """Compatibility ingest using canonical Memory V2/V1 pipelines."""
    del kind
    return _run(ingest_url_async("default", url, importance=importance))


async def ingest_url_for_user(user_id: str, url: str, importance: float = 0.6) -> Dict[str, Any]:
    return await ingest_url_async(user_id, url, importance=importance)
