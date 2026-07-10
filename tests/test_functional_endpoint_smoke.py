from __future__ import annotations

from pathlib import Path


def test_functional_endpoint_smoke(tmp_path):
    from scripts.functional_endpoint_smoke import run

    db_path = tmp_path / "functional.sqlite3"
    assert run(Path.cwd(), db_path=str(db_path), json_output=True) == 0
