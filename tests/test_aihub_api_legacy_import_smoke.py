"""19.07: unmounted legacy ``aihub.api`` routers are archived outside the package.

Only ``security_router`` and ``self_heal_status_router`` remain under ``aihub.api``
and are mounted on ``aihub.main:app``. Dangerous / unused routers live in
``archive/legacy_routers/`` (and ``api_unmounted/``).
"""

from __future__ import annotations

import importlib
import pathlib

import pytest

_ARCHIVED_API_ROUTERS = (
    "anomaly_router",
    "fs_router",
    "memory_router",
    "memory_stats_router",
    "memory_train_router",
    "psyche_brain_router",
    "psyche_brain_live_router",
    "psyche_predict_router",
    "psyche_router",
    "sse_router",
)

_MOUNTED_API_MODULES = (
    "aihub.api.security_router",
    "aihub.api.self_heal_status_router",
)


@pytest.mark.parametrize("mod_name", _MOUNTED_API_MODULES)
def test_mounted_api_router_imports(mod_name: str) -> None:
    importlib.import_module(mod_name)


@pytest.mark.parametrize("leaf", _ARCHIVED_API_ROUTERS)
def test_unmounted_api_routers_removed_from_aihub_package(leaf: str) -> None:
    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / f"{leaf}.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"aihub.api.{leaf}")


@pytest.mark.parametrize("leaf", _ARCHIVED_API_ROUTERS)
def test_unmounted_api_routers_archived(leaf: str) -> None:
    import aihub

    repo_root = pathlib.Path(aihub.__file__).resolve().parents[1]
    archived = repo_root / "archive" / "legacy_routers" / "api_unmounted" / f"{leaf}.py"
    assert archived.is_file()


def test_ai_compat_router_removed_from_aihub_package() -> None:
    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "ai_compat_router.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.ai_compat_router")


def test_ai_compat_router_archived_outside_runtime() -> None:
    import aihub

    repo_root = pathlib.Path(aihub.__file__).resolve().parents[1]
    archived = repo_root / "archive" / "legacy_routers" / "ai_compat_router.py"
    assert archived.is_file()
    assert not (repo_root / "archive" / "legacy_routers" / "__init__.py").exists()
    assert not (repo_root / "archive" / "__init__.py").exists()


def test_ops_router_removed_from_aihub_package() -> None:
    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "ops_router.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.ops_router")
    assert (aihub_root.parent / "archive" / "legacy_routers" / "ops_router.py").is_file()


def test_admin_router_removed_from_aihub_package() -> None:
    import aihub

    aihub_root = pathlib.Path(aihub.__file__).resolve().parent
    assert not (aihub_root / "api" / "admin_router.py").exists()
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("aihub.api.admin_router")
    assert (aihub_root.parent / "archive" / "legacy_routers" / "admin_router.py").is_file()
