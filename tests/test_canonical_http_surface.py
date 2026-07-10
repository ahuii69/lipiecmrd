"""Gate: canonical HTTP manifest matches live ``aihub.main:app`` routes."""

from __future__ import annotations

from aihub.canonical_http_surface import (
    CANONICAL_HTTP_ROUTES,
    EXPECTED_ROUTE_KEYS,
    collect_route_keys,
    source_file_for_path,
)
from aihub.main import app


def test_canonical_routes_match_app_introspection() -> None:
    actual = collect_route_keys(app)
    msg = _diff_message(actual, EXPECTED_ROUTE_KEYS)
    assert actual == EXPECTED_ROUTE_KEYS, msg


def _diff_message(
    actual: frozenset[tuple[str, str]],
    expected: frozenset[tuple[str, str]],
) -> str:
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    parts = []
    if missing:
        parts.append("missing_on_app (update collect or fix app): " f"{missing[:20]!r}")
    if extra:
        parts.append("extra_on_app (update CANONICAL_HTTP_ROUTES): " f"{extra[:20]!r}")
    return "; ".join(parts) or "unknown diff"


def test_manifest_row_shape_and_source_heuristic() -> None:
    for method, path, src, cockpit, tests in CANONICAL_HTTP_ROUTES:
        assert method in ("GET", "POST", "PUT", "PATCH", "DELETE"), method
        assert path.startswith("/"), path
        assert cockpit in ("TAK", "NIE", "BRAK DANYCH"), cockpit
        assert tests, tests
        if "OpenAPI" not in src:
            assert src == source_file_for_path(path), (path, src)


def test_high_value_endpoints_have_tests_hint() -> None:
    """Curated pointers — expand as coverage mapping improves.

    Requires a non-``BRAK DANYCH`` ``tests_hint`` (committed path under ``tests/``)
    only for endpoints we can honestly tie to HTTP-level tests today. Everything
    else stays optional so the gate does not block the whole tree.
    """
    required = {
        ("GET", "/system/ping"),
        ("POST", "/chat/turn"),
        ("POST", "/web/fetch"),
        ("POST", "/agent/run"),
        ("GET", "/memory/v2/summary/{user_id}"),
        ("GET", "/psyche/v2/{user_id}"),
    }
    by_key = {(m, p): t for m, p, _, _, t in CANONICAL_HTTP_ROUTES}
    for key in required:
        hint = by_key[key]
        assert hint != "BRAK DANYCH", f"{key} should list a tests/ file"
