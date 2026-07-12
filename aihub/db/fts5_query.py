"""Safe FTS5 MATCH expression builder for user-supplied search text."""

from __future__ import annotations

import re
from dataclasses import dataclass

# FTS5 prefix/query operators we never pass through unquoted.
_FTS5_OPERATORS = frozenset({"AND", "OR", "NOT", "NEAR"})


@dataclass(frozen=True)
class Fts5MatchQuery:
    """Prepared MATCH clause fragment (without the ``MATCH`` keyword)."""

    expression: str
    used_lexical_fallback: bool = False


def _tokenize(raw: str) -> list[str]:
    """Split user text into non-empty tokens; preserve meaningful punctuation chunks."""
    text = (raw or "").strip()
    if not text:
        return []
    # Unicode-aware word chunks; keep isolated punctuation as its own token when meaningful.
    parts = re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE)
    tokens: list[str] = []
    for part in parts:
        piece = part.strip()
        if not piece:
            continue
        upper = piece.upper()
        if upper in _FTS5_OPERATORS:
            continue
        tokens.append(piece)
    return tokens


def _quote_literal(token: str) -> str:
    escaped = token.replace('"', '""')
    return f'"{escaped}"'


def build_fts5_match_query(user_query: str, *, column: str = "content") -> Fts5MatchQuery:
    """Build a safe FTS5 MATCH expression for ``column:<tokens>``.

    User input containing ``?``, ``"``, ``:``, ``-``, parentheses, boolean operators
    and other FTS5 syntax characters is tokenized and each token is quoted literally.
    An empty query yields an expression that matches nothing without parser errors.
    """
    col = (column or "content").strip() or "content"
    tokens = _tokenize(user_query)
    if not tokens:
        return Fts5MatchQuery(f'{col}:"__no_match__"', used_lexical_fallback=False)
    quoted = " ".join(_quote_literal(tok) for tok in tokens)
    return Fts5MatchQuery(f"{col}:({quoted})", used_lexical_fallback=False)


def build_lexical_like_pattern(user_query: str) -> str:
    """Escape ``%``/``_`` for SQL LIKE fallback search."""
    text = (user_query or "").strip()
    if not text:
        return "%"
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"
