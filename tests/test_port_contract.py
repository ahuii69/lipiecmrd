"""Verify backend port contract consistency.

Tests that all config sources agree on the canonical backend port.
"""

import importlib
import os
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CANONICAL_HOST = "127.0.0.1"
CANONICAL_PORT = 8080


# ── 1. Python config defaults ──────────────────────────────────


class TestConfigDefaults:
    """aihub.config and aihub.core.config must default to canonical port."""

    def test_config_py_host(self):
        # Reload with clean env (no HOST set → default kicks in)
        env_backup = os.environ.pop("HOST", None)
        try:
            import aihub.config as cfg

            importlib.reload(cfg)
            assert cfg.HOST == CANONICAL_HOST, (
                f"aihub.config.HOST default is {cfg.HOST!r}, expected {CANONICAL_HOST!r}"
            )
        finally:
            if env_backup is not None:
                os.environ["HOST"] = env_backup

    def test_config_py_port(self):
        env_backup = os.environ.pop("PORT", None)
        try:
            import aihub.config as cfg

            importlib.reload(cfg)
            assert cfg.PORT == CANONICAL_PORT, (
                f"aihub.config.PORT default is {cfg.PORT}, expected {CANONICAL_PORT}"
            )
        finally:
            if env_backup is not None:
                os.environ["PORT"] = env_backup

    def test_core_config_host(self):
        env_backup = os.environ.pop("HOST", None)
        try:
            importlib.reload(sys.modules["aihub.core.config"])
            from aihub.core.config import load_settings as ls

            s = ls.__wrapped__() if hasattr(ls, "__wrapped__") else ls()
            assert s.host == CANONICAL_HOST
        except Exception:
            # load_settings may require API_KEY etc — check raw default
            from aihub.core.config import Settings

            assert Settings().host == CANONICAL_HOST
        finally:
            if env_backup is not None:
                os.environ["HOST"] = env_backup

    def test_core_config_port(self):
        env_backup = os.environ.pop("PORT", None)
        try:
            from aihub.core.config import Settings

            assert Settings().port == CANONICAL_PORT
        finally:
            if env_backup is not None:
                os.environ["PORT"] = env_backup


# ── 2. start.sh contract ───────────────────────────────────────


class TestStartSh:
    """start.sh must launch uvicorn on the canonical port."""

    @pytest.fixture()
    def start_sh(self):
        return (REPO / "start.sh").read_text(encoding="utf-8")

    def test_start_sh_default_port_fallback(self, start_sh):
        """The PORT fallback in start_backend() must be canonical."""
        # Matches: local port="${PORT:-8080}"
        m = re.search(r'local\s+port="\$\{PORT:-(\d+)\}"', start_sh)
        assert m, "Cannot find 'local port' declaration in start.sh"
        assert int(m.group(1)) == CANONICAL_PORT, (
            f"start.sh default port is {m.group(1)}, expected {CANONICAL_PORT}"
        )

    def test_start_sh_exports_actual_port(self, start_sh):
        """start.sh must export PORT=actual_port before launching uvicorn."""
        assert 'export PORT="$actual_port"' in start_sh, (
            "start.sh does not export actual PORT before uvicorn launch"
        )

    def test_start_sh_uses_host_variable(self, start_sh):
        """uvicorn in start.sh must use $HOST, not hardcoded value."""
        assert '--host "$HOST"' in start_sh

    def test_start_sh_uses_actual_port_variable(self, start_sh):
        """uvicorn in start.sh must use $actual_port, not $PORT."""
        assert '--port "$actual_port"' in start_sh


# ── 3. .env.example contract ───────────────────────────────────


class TestEnvExample:
    def test_env_example_port(self):
        env_example = (REPO / ".env.example").read_text(encoding="utf-8")
        assert f"PORT={CANONICAL_PORT}" in env_example

    def test_env_example_host(self):
        env_example = (REPO / ".env.example").read_text(encoding="utf-8")
        assert f"HOST={CANONICAL_HOST}" in env_example


# ── 4. Startup log does not lie ─────────────────────────────────


class TestStartupLogHonesty:
    """The lifespan log must read live env, not stale import-time constants."""

    def test_lifespan_reads_env_at_runtime(self):
        src = (REPO / "aihub" / "main.py").read_text(encoding="utf-8")
        # Must NOT have the pattern: logger.info(..., HOST, PORT)
        # where HOST/PORT are bare config constants.
        # Instead should use os.environ.get or re-read.
        assert 'os.environ.get("HOST"' in src or 'os.environ.get("PORT"' in src, (
            "lifespan startup log should re-read HOST/PORT from os.environ, "
            "not use stale import-time config constants"
        )


# ── 5. No port 8000 in active code ─────────────────────────────


class TestNoStalePort:
    """No active code file should default to port 8000."""

    ACTIVE_FILES = [
        "aihub/config.py",
        "aihub/core/config.py",
        "aihub/main.py",
        "start.sh",
        "smoke_test_tools.sh",
        "e2e-sanity.sh",
        "sanity.sh",
        "ENV_STATUS_CHECK.sh",
    ]

    @pytest.mark.parametrize("relpath", ACTIVE_FILES)
    def test_no_port_8000_default(self, relpath):
        fpath = REPO / relpath
        if not fpath.exists():
            pytest.skip(f"{relpath} not found")
        content = fpath.read_text(encoding="utf-8")
        # Match port-like usage of 8000 (not string truncation like [:8000])
        hits = re.findall(r"(?:port|PORT)[^a-zA-Z0-9]*8000", content, re.IGNORECASE)
        assert not hits, f"{relpath} still references port 8000: {hits}"
