"""Regresja: komentarze ``--`` przed DDL nie mogą usuwać całego polecenia (compat_router)."""

from aihub.pg_bootstrap import _strip_leading_line_comments


def test_strip_leading_comments_keeps_create_after_blank_comment_lines():
    stmt = """-- Legacy note
-- second line
CREATE SCHEMA IF NOT EXISTS compat_router;
"""
    s = _strip_leading_line_comments(stmt.strip())
    assert "CREATE SCHEMA" in s
    assert s.startswith("CREATE")


def test_strip_empty_only_returns_empty():
    assert _strip_leading_line_comments("-- only\n-- comments\n") == ""
