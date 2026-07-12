"""Regression: temp SQLite DB cycles must not leak FDs or background threads."""

from __future__ import annotations

import importlib
import shutil

import pytest

from aihub.testing.runtime_reset import (
    assert_runtime_quiescent,
    collect_runtime_diagnostics,
    reset_runtime_for_tests,
)


@pytest.mark.no_isolated_db
def test_runtime_reset_survives_one_hundred_db_cycles(tmp_path, monkeypatch):
    baseline = collect_runtime_diagnostics()
    baseline_threads = int(baseline["thread_count"])
    baseline_fds = baseline.get("fd_count")
    baseline_fds_int = int(baseline_fds) if isinstance(baseline_fds, int) else None

    for cycle in range(100):
        db_dir = tmp_path / f"cycle_{cycle}"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_file = db_dir / "test.sqlite3"

        reset_runtime_for_tests()
        monkeypatch.setenv("DB_BACKEND", "sqlite")
        monkeypatch.setenv("DB_PATH", str(db_file.resolve()))
        monkeypatch.setenv("DATA_DIR", str(db_dir))

        import aihub.config as cfg
        import aihub.db as db_pkg

        importlib.reload(cfg)
        db_pkg.init_db()
        db_pkg.exec_one(
            "CREATE TABLE IF NOT EXISTS leak_probe(id INTEGER PRIMARY KEY, note TEXT)"
        )
        db_pkg.exec_one("INSERT INTO leak_probe(note) VALUES (?)", (f"cycle-{cycle}",))
        row = db_pkg.fetch_one(
            "SELECT note FROM leak_probe WHERE note=?", (f"cycle-{cycle}",)
        )
        assert row is not None

        reset_runtime_for_tests()
        shutil.rmtree(db_dir, ignore_errors=False)

    assert_runtime_quiescent(
        baseline_threads=baseline_threads,
        baseline_fds=baseline_fds_int,
    )
