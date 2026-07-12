"""Regression tests for safe FTS5 query preparation."""

from __future__ import annotations

import sqlite3

import pytest

from aihub.db.fts5_query import build_fts5_match_query, build_lexical_like_pattern


@pytest.mark.parametrize(
    "query",
    [
        "?",
        '"quoted"',
        "foo:bar",
        "a -b",
        "(x AND y)",
        "AND OR NOT",
        "special !@#$%",
    ],
)
def test_build_fts5_match_query_never_empty(query: str):
    built = build_fts5_match_query(query)
    assert built.expression
    assert "?" not in built.expression or '"' in built.expression


def test_build_fts5_match_query_sqlite_parser_accepts_special_input():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE VIRTUAL TABLE memory_fts USING fts5(content, user_id UNINDEXED, layer UNINDEXED, node_id UNINDEXED)"
    )
    con.execute(
        "INSERT INTO memory_fts(node_id, content, user_id, layer) VALUES (?,?,?,?)",
        ("n1", "hello world", "u1", "L2"),
    )
    for query in ["?", "AND OR NOT", 'foo:"bar"', "a -b (c)"]:
        expr = build_fts5_match_query(query).expression
        try:
            con.execute(
                "SELECT node_id FROM memory_fts WHERE memory_fts MATCH ?",
                (expr,),
            ).fetchall()
        except sqlite3.OperationalError as exc:
            pytest.fail(f"FTS5 parser rejected {query!r} as {expr!r}: {exc}")


def test_build_lexical_like_pattern_escapes_wildcards():
    assert "\\%" in build_lexical_like_pattern("100%")
