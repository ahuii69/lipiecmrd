"""Regresja: krytyczne skrypty mają poprawny shebang (uniknięcie uszkodzenia pierwszej linii)."""

from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _must_start_with_shebang(rel: str) -> None:
    p = _root() / rel
    line = p.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    assert line.startswith("#!"), f"{rel}: pierwsza linia musi być shebang, jest: {line[:80]!r}"


def test_check_allowlist_script_shebang():
    _must_start_with_shebang("scripts/check_allowlist_canonical_sync.py")


def test_check_pg_ready_shebang():
    _must_start_with_shebang("scripts/check_pg_ready.py")


def test_dotenv_tool_shebang():
    _must_start_with_shebang("scripts/dotenv_tool.py")
