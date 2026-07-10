#!/usr/bin/env python3
"""Jednorazowy bootstrap schematu PostgreSQL z pliku wygenerowanego z kanonicznego SQLite."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _split_sql_statements(sql: str) -> list[str]:
    """Dzieli plik DDL na pojedyncze polecenia (średnik poza nawiasami / stringami uproszczony)."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    in_squote = False
    i = 0
    while i < len(sql):
        c = sql[i]
        if c == "'" and (i == 0 or sql[i - 1] != "\\"):
            in_squote = not in_squote
            buf.append(c)
            i += 1
            continue
        if not in_squote:
            if c == "(":
                depth += 1
            elif c == ")":
                depth = max(0, depth - 1)
            elif c == ";" and depth == 0:
                stmt = "".join(buf).strip()
                buf = []
                if stmt:
                    out.append(stmt)
                i += 1
                continue
        buf.append(c)
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


def _strip_leading_line_comments(stmt: str) -> str:
    """Usuwa wiodące linie będące wyłącznie komentarzem ``--`` (wielolinijkowe bloki DDL)."""
    lines: list[str] = []
    started = False
    for line in stmt.splitlines():
        t = line.strip()
        if not started:
            if not t or t.startswith("--"):
                continue
            started = True
        lines.append(line)
    return "\n".join(lines).strip()


def run_postgres_bootstrap(raw_conn: Any) -> None:
    """Wykonuje ``postgres_bootstrap.sql`` na surowym połączeniu psycopg2."""
    path = Path(__file__).resolve().parent / "sql" / "postgres_bootstrap.sql"
    sql = path.read_text(encoding="utf-8")
    cur = raw_conn.cursor()
    n = 0
    for stmt in _split_sql_statements(sql):
        s = _strip_leading_line_comments(stmt.strip())
        if not s or s.startswith("--"):
            continue
        try:
            cur.execute(s)
            n += 1
        except Exception as e:
            logger.error("postgres_bootstrap failed on stmt #%d: %s\n%s", n + 1, e, s[:500])
            cur.close()
            raise
    cur.close()
    raw_conn.commit()
    logger.info("postgres_bootstrap: wykonano %d poleceń SQL", n)
