#!/usr/bin/env python3
"""Cockpit BFF proxy allowlist — single JSON manifest shared with Next.js (see cockpit/lib/api).

The file :file:`cockpit/lib/api/cockpit-proxy-allowlist.json` is the **authoritative list**
of backend paths the cockpit proxy may forward. It must stay aligned with
:file:`cockpit/lib/api/client.ts` and every entry must exist on ``aihub.main:app``
(verified against :data:`aihub.canonical_http_surface.CANONICAL_HTTP_ROUTES`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple, TypedDict


class _RouteRow(TypedDict):
    method: str
    path: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def cockpit_proxy_allowlist_json_path() -> Path:
    return _repo_root() / "cockpit" / "lib" / "api" / "cockpit-proxy-allowlist.json"


def load_cockpit_proxy_allowlist_routes() -> List[_RouteRow]:
    path = cockpit_proxy_allowlist_json_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    routes = data.get("routes")
    if not isinstance(routes, list):
        raise ValueError("cockpit-proxy-allowlist.json: missing routes array")
    out: List[_RouteRow] = []
    for row in routes:
        if not isinstance(row, dict):
            continue
        method = row.get("method")
        p = row.get("path")
        if isinstance(method, str) and isinstance(p, str):
            out.append({"method": method.upper(), "path": p})
    return out


def path_matches_template(template: str, actual_path: str) -> bool:
    """Match FastAPI-style template (``{user_id}``) to a concrete path."""

    def split_p(p: str) -> List[str]:
        p = p if p.startswith("/") else f"/{p}"
        return [x for x in p.split("/") if x]

    ta = split_p(template)
    tb = split_p(actual_path)
    if len(ta) != len(tb):
        return False
    for a, b in zip(ta, tb):
        if a.startswith("{") and a.endswith("}"):
            if not b:
                return False
            continue
        if a != b:
            return False
    return True


def concrete_path_allowed(routes: List[_RouteRow], method: str, pathname: str) -> bool:
    m = method.upper()
    path = pathname if pathname.startswith("/") else f"/{pathname}"
    for r in routes:
        if r["method"] != m:
            continue
        if path_matches_template(r["path"], path):
            return True
    return False


def allowlist_route_keys() -> frozenset[Tuple[str, str]]:
    """(METHOD, path_template) for set inclusion checks against canonical surface."""
    return frozenset(
        (r["method"], r["path"]) for r in load_cockpit_proxy_allowlist_routes()
    )
