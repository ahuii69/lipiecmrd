"""Cockpit proxy allowlist: subset of canonical HTTP surface + matcher sanity."""

from __future__ import annotations

import pytest

from aihub.canonical_http_surface import CANONICAL_HTTP_ROUTES
from aihub.cockpit_proxy_allowlist import (
    concrete_path_allowed,
    load_cockpit_proxy_allowlist_routes,
    path_matches_template,
)


def test_allowlist_every_route_exists_on_canonical_surface():
    allow = load_cockpit_proxy_allowlist_routes()
    canonical = frozenset((m, p) for m, p, *_ in CANONICAL_HTTP_ROUTES)
    missing = []
    for r in allow:
        key = (r["method"], r["path"])
        if key not in canonical:
            missing.append(key)
    assert not missing, f"Allowlist entries not on canonical surface: {missing}"


@pytest.mark.parametrize(
    "template,actual,expected",
    [
        ("/system/ping", "/system/ping", True),
        ("/agent/status/{user_id}", "/agent/status/u1", True),
        ("/agent/status/{user_id}", "/agent/status/u1/extra", False),
        ("/agent/goals/{user_id}/{goal_id}/trace", "/agent/goals/a/g/trace", True),
        ("/chat/turn", "/chat/turn", True),
        ("/admin/ping", "/system/ping", False),
    ],
)
def test_path_matches_template(template, actual, expected):
    assert path_matches_template(template, actual) is expected


def test_concrete_paths_used_by_api_client_style_calls():
    routes = load_cockpit_proxy_allowlist_routes()
    samples = [
        ("GET", "/system/ping"),
        ("POST", "/chat/turn"),
        ("GET", "/chat/capabilities"),
        ("POST", "/chat/capabilities/execute"),
        ("POST", "/agent/run"),
        ("GET", "/cockpit/schema-health"),
        ("GET", "/cockpit/overview/demo-user"),
        ("GET", "/memory/v2/procedures/demo-user"),
        ("GET", "/chat/session/demo-sid/history"),
    ]
    for method, path in samples:
        assert concrete_path_allowed(routes, method, path), (method, path)


def test_disallowed_paths_blocked_by_matcher():
    routes = load_cockpit_proxy_allowlist_routes()
    assert not concrete_path_allowed(routes, "GET", "/admin/ping")
    assert not concrete_path_allowed(routes, "GET", "/openapi.json")
    assert not concrete_path_allowed(routes, "POST", "/system/ping")
    assert not concrete_path_allowed(routes, "DELETE", "/chat/turn")
