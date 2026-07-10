"""Smoke: selected legacy ``aihub.api`` routers import without mounting on ``app``.

Policy (explicit subset, not accidental omission):
- Gate checks only modules importable with **canonical** ``requirements.txt`` (same third-party
  surface as the active stack). This does **not** assert functional value of legacy code.
- ``aihub.api.web_router`` is **out of scope** for this gate: it imports ``beautifulsoup4``
  (``bs4``), which is **not** a declared dependency of the canonical runtime. That router is
  LEGACY / UNMOUNTED, a documented NEAR_DUPLICATE of ``POST /web/fetch`` on ``aihub.main``;
  extending production deps solely to satisfy its import would inflate the install footprint
  for unused code. See ``aihub/api/_LEGACY.md`` (P3B) and
  ``_LEGACY_API_ROUTER_IMPORT_SMOKE_EXCLUDED`` below.
- ``aihub.api.ai_compat_router`` (arbitrary code execution via ``POST /python/run``) was **removed
  from this gate and from ``aihub/api/`` entirely** in the 06.07 repair sprint (P0 security fix):
  see ``archive/legacy_routers/README.md``. "Importable but unmounted" was previously used as an
  argument that the RCE surface was safe; it is not — a single accidental
  ``app.include_router(...)`` would have exposed it. It is now physically outside the ``aihub``
  package and this gate asserts it stays that way.
- ``aihub.api.ops_router`` (hardcoded ``/root/ai-hub`` path + ``systemctl restart`` via shell) was
  **also removed** in the same sprint (P1 security fix) for the same reason — see
  ``archive/legacy_routers/README.md``.
- ``aihub.api.admin_router`` (``/admin`` prefix collision with the canonical, mounted
  ``aihub.admin_api``, plus an unredacted request/response body leak endpoint) was **also
  removed** in the same sprint (P1 fix) — see ``archive/legacy_routers/README.md``.

Canonical HTTP surface remains ``aihub.main:app``; nothing here mounts ``aihub/api/*``.
"""

from __future__ import annotations

import importlib

import pytest

# Modules required for this gate to stay aligned with ``requirements.txt`` (no extra wheels).
_LEGACY_API_ROUTER_IMPORT_SMOKE_EXCLUDED: tuple[tuple[str, str], ...] = (
    (
        "aihub.api.web_router",
        "Requires beautifulsoup4 (bs4), not in canonical requirements.txt; "
        "LEGACY NEAR_DUPLICATE of main POST /web/fetch; UNMOUNTED — see aihub/api/_LEGACY.md P3B.",
    ),
)

_LEGACY_API_ROUTER_MODULES = (
    "aihub.api.anomaly_router",
    "aihub.api.fs_router",
    "aihub.api.memory_router",
    "aihub.api.memory_stats_router",
    "aihub.api.memory_train_router",
    "aihub.api.psyche_brain_router",
    "aihub.api.psyche_brain_live_router",
    "aihub.api.psyche_predict_router",
    "aihub.api.psyche_router",
    "aihub.api.security_router",
    "aihub.api.self_heal_status_router",
    "aihub.api.sse_router",
)


def test_legacy_import_smoke_exclusions_documented() -> None:
    excluded_mods = {m for m, _ in _LEGACY_API_ROUTER_IMPORT_SMOKE_EXCLUDED}
    assert "aihub.api.web_router" in excluded_mods
    for mod in excluded_mods:
        assert mod not in _LEGACY_API_ROUTER_MODULES


@pytest.mark.parametrize("mod_name", _LEGACY_API_ROUTER_MODULES)
def test_legacy_api_router_imports(mod_name: str) -> None:
    importlib.import_module(mod_name)


def test_ai_compat_router_removed_from_aihub_package() -> None:
    """06.07 P0: RCE router must not exist inside the importable ``aihub`` package anymore."""
    import pathlib

    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "ai_compat_router.py").exists()

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.ai_compat_router")


def test_ai_compat_router_archived_outside_runtime() -> None:
    """The archived copy must live outside aihub/ and not be a Python package member."""
    import pathlib

    import aihub

    repo_root = pathlib.Path(aihub.__file__).resolve().parents[1]
    archived = repo_root / "archive" / "legacy_routers" / "ai_compat_router.py"
    assert archived.is_file()
    assert not (repo_root / "archive" / "legacy_routers" / "__init__.py").exists()
    assert not (repo_root / "archive" / "__init__.py").exists()


def test_ops_router_removed_from_aihub_package() -> None:
    """06.07 P1: ops router with hardcoded host path + systemctl restart must be archived."""
    import pathlib

    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "ops_router.py").exists()

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.ops_router")

    repo_root = aihub_root.parent
    archived = repo_root / "archive" / "legacy_routers" / "ops_router.py"
    assert archived.is_file()


def test_admin_router_removed_from_aihub_package() -> None:
    """06.07 P1: colliding /admin router (unredacted body leak) must be archived."""
    import pathlib

    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "admin_router.py").exists()

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.admin_router")

    repo_root = aihub_root.parent
    archived = repo_root / "archive" / "legacy_routers" / "admin_router.py"
    assert archived.is_file()
