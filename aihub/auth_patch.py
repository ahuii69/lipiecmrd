"""Authentication helper compatibility module.

Provides strict API key verification used by main HTTP middleware.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import FrozenSet

from fastapi import HTTPException, Request

_DEFAULT_HUB_KEYS = (
    "AIHUB_API_KEY",
    "HUB_API_KEY",
    "API_KEY",
    "AIHUB_PROXY_TOKEN",
)


def _load_hub_key_env_names() -> tuple[str, ...]:
    """Single source: repo ``config/hub_key_env_names.json`` (shared with Next.js)."""
    p = Path(__file__).resolve().parents[1] / "config" / "hub_key_env_names.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return _DEFAULT_HUB_KEYS
    if isinstance(data, list) and data and all(isinstance(x, str) for x in data):
        return tuple(data)
    return _DEFAULT_HUB_KEYS


HUB_KEY_ENV_NAMES: tuple[str, ...] = _load_hub_key_env_names()


def _codebase_dev_hub_spec() -> tuple[str, str] | None:
    """Opcjonalny jawny klucz dev z ``config/codebase_dev_hub.json`` (włączany flagą env)."""
    p = Path(__file__).resolve().parents[1] / "config" / "codebase_dev_hub.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    flag = (data.get("enable_env") or "").strip()
    key = (data.get("hub_key") or "").strip()
    if flag and key:
        return (flag, key)
    return None


def coalesce_hub_key() -> str:
    """First non-empty hub secret (same priority as Next.js BFF env fallbacks)."""
    for name in HUB_KEY_ENV_NAMES:
        v = (os.environ.get(name) or "").strip()
        if v:
            return v
    spec = _codebase_dev_hub_spec()
    if spec:
        flag, key = spec
        if os.getenv(flag) == "1":
            return key
    return ""


def collect_hub_auth_secrets() -> FrozenSet[str]:
    """Unique non-empty values from hub env aliases — any may authenticate."""
    vals = {
        (os.environ.get(name) or "").strip()
        for name in HUB_KEY_ENV_NAMES
        if (os.environ.get(name) or "").strip()
    }
    spec = _codebase_dev_hub_spec()
    if spec:
        flag, key = spec
        if os.getenv(flag) == "1" and key:
            vals.add(key)
    return frozenset(vals)


def hub_proxy_token_expected() -> str:
    """Value BFF must send as ``X-AIHub-Proxy-Token`` (explicit proxy secret or coalesced hub key)."""
    explicit = (os.environ.get("AIHUB_PROXY_TOKEN") or "").strip()
    if explicit:
        return explicit
    return coalesce_hub_key()


def safe_check_api_key(request: Request) -> None:
    """Validate API key from request headers/query params.

    Expected hub keys: any non-empty env name listed in ``config/hub_key_env_names.json``.

    Accepted request sources (in order):
    - header: ``x-api-key``
    - header: ``authorization: Bearer <token>``
    - query param: ``api_key``

    Raises HTTPException(401) when the key is missing or invalid.
    """
    expected = collect_hub_auth_secrets()
    if not expected:
        return

    header_key = (request.headers.get("x-api-key") or "").strip()

    auth = (request.headers.get("authorization") or "").strip()
    bearer_key = ""
    if auth.lower().startswith("bearer "):
        bearer_key = auth[7:].strip()

    query_key = (request.query_params.get("api_key") or "").strip()

    for candidate in (header_key, bearer_key, query_key):
        if candidate and candidate in expected:
            return

    raise HTTPException(status_code=401, detail="invalid api key")
