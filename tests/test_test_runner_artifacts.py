"""Regression tests for pytest failure-evidence configuration."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyproject_pytest_addopts_exposes_failure_summary_and_traceback():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.pytest.ini_options]" in pyproject
    assert "-ra" in pyproject
    assert "--tb=short" in pyproject


def test_artifact_runner_script_persists_log_and_junit_report():
    script_path = ROOT / "scripts" / "run_pytest_with_artifacts.sh"
    script = script_path.read_text(encoding="utf-8")

    assert script_path.exists()
    assert "--junitxml" in script
    assert "tee" in script
    assert "pytest_" in script
