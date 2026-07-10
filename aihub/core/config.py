"""SPLIT_BRAIN / ACTIVE_PARTIAL: secondary :class:`Settings` for legacy modules only.

**Canonical** process config is :mod:`aihub.config` (loaded first, single ``.env`` semantics
for ``HOST``/``PORT``/``DATA_DIR``/``DB_PATH``/``FS_ROOT``).

This module **adapts** :class:`Settings` from ``os.environ`` + values imported from
:mod:`aihub.config` — it does **not** load a second ``.env`` file.

Use :mod:`aihub.config` for new code. Import ``settings`` from here only where listed
consumers already do (e.g. ``aihub.memory.embedder``, ``aihub.workers.consolidation``,
``aihub.db.database`` — see grep for ``aihub.core.config``).

Optional overrides (backward compatibility only): ``AIHUB_HOST``, ``AIHUB_PORT``,
``AIHUB_DATA_DIR``, ``AIHUB_DB_PATH``, ``AIHUB_FS_ROOT``, ``AIHUB_PUBLIC_BASE``, and the
remaining ``AIHUB_*`` keys below — if unset, canon from :mod:`aihub.config` wins.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import List

from pydantic import BaseModel, Field, ValidationError


def _split_csv(v: str) -> List[str]:
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]


class Settings(BaseModel):
    """Legacy-shaped settings object; values derive from :mod:`aihub.config` + env."""

    api_key: str = Field(default="")
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8080)
    public_base: str = Field(default="")

    data_dir: str = Field(default="")
    db_path: str = Field(default="")

    fs_root: str = Field(default="")
    fs_max_write_bytes: int = Field(default=10_000_000)

    token_secret: str = Field(default="")
    token_ttl_sec: int = Field(default=900)

    web_max_bytes: int = Field(default=2_000_000)
    web_timeout_sec: int = Field(default=15)
    web_allow_domains: list[str] = Field(default_factory=list)
    web_deny_domains: list[str] = Field(default_factory=list)

    embed_model: str = Field(default="BAAI/bge-small-en-v1.5")
    consolidate_every_sec: int = Field(default=60)
    decay_half_life_hours: int = Field(default=72)

    recorder_max_body_bytes: int = Field(default=400_000)

    def ensure_dirs(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)


def load_settings() -> Settings:
    """Build settings from canonical :mod:`aihub.config` plus explicit env overrides."""
    import aihub.config as canon

    host = (os.getenv("AIHUB_HOST") or "").strip() or canon.HOST
    port_raw = (os.getenv("AIHUB_PORT") or "").strip()
    port = int(port_raw) if port_raw else canon.PORT

    data_dir = (os.getenv("AIHUB_DATA_DIR") or "").strip() or str(canon.DATA_DIR)
    db_path = (os.getenv("AIHUB_DB_PATH") or "").strip() or str(canon.DB_PATH)
    fs_root = (os.getenv("AIHUB_FS_ROOT") or "").strip() or str(canon.FS_ROOT)

    public_base = (os.getenv("AIHUB_PUBLIC_BASE") or "").strip() or (
        canon.PUBLIC_URL or canon.DOMAIN or ""
    )

    raw = {
        "api_key": os.getenv("API_KEY", ""),
        "host": host,
        "port": port,
        "public_base": public_base,
        "data_dir": data_dir,
        "db_path": db_path,
        "fs_root": fs_root,
        "fs_max_write_bytes": int(os.getenv("AIHUB_FS_MAX_WRITE_BYTES", "10000000")),
        "token_secret": os.getenv("AIHUB_TOKEN_SECRET", ""),
        "token_ttl_sec": int(os.getenv("AIHUB_TOKEN_TTL_SEC", "900")),
        "web_max_bytes": int(
            os.getenv("AIHUB_WEB_MAX_BYTES", str(canon.HTTP_MAX_BYTES))
        ),
        "web_timeout_sec": int(
            os.getenv("AIHUB_WEB_TIMEOUT_SEC", str(int(canon.HTTP_TIMEOUT_S)))
        ),
        "web_allow_domains": _split_csv(os.getenv("AIHUB_WEB_ALLOW_DOMAINS", "")),
        "web_deny_domains": _split_csv(
            os.getenv(
                "AIHUB_WEB_DENY_DOMAINS",
                "localhost,127.0.0.1,0.0.0.0,169.254.169.254",
            )
        ),
        # LEGACY / ADAPTER — NIE UŻYWAĆ W NOWYM KODZIE (fastembed; memory/embedder + memory.service).
        # Nie jest to EMBEDDING_MODEL / Voyage z aihub.config — kanon: aihub.embedding_engine.
        "embed_model": os.getenv("AIHUB_EMBED_MODEL", "BAAI/bge-small-en-v1.5"),
        "consolidate_every_sec": int(os.getenv("AIHUB_CONSOLIDATE_EVERY_SEC", "60")),
        "decay_half_life_hours": int(os.getenv("AIHUB_DECAY_HALF_LIFE_HOURS", "72")),
        "recorder_max_body_bytes": int(
            os.getenv("AIHUB_RECORDER_MAX_BODY_BYTES", "400000")
        ),
    }

    try:
        return Settings(**raw)
    except ValidationError as e:
        raise RuntimeError(f"Invalid legacy settings: {e}") from e


@lru_cache(maxsize=1)
def _cached_settings() -> Settings:
    return load_settings()


def get_settings() -> Settings:
    """Return process-cached adapted settings (invalidate in tests via ``reload_settings``)."""
    return _cached_settings()


def reload_settings() -> Settings:
    """Clear cache and reload (tests / rare hot-reload)."""
    _cached_settings.cache_clear()
    return get_settings()


class _SettingsProxy:
    __slots__ = ()

    def __getattr__(self, name: str):
        return getattr(get_settings(), name)


settings = _SettingsProxy()
