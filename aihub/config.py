#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ACTIVE_CONFIRMED: primary runtime configuration for the AI-Hub process.

- Loaded first; optional ``.env`` in non-``production`` (see ``_load_local_env_file``).
- **Canonical** env keys for LLM, embeddings (Voyage / ``EMBEDDING_*``), paths (``DATA_DIR``,
  ``DB_PATH``, ``FS_ROOT``), etc.
- **Adapter:** :mod:`aihub.core.config` exposes a legacy-shaped ``Settings`` object built
  **from these values** — use this module for new code; use ``core.config`` only where
  legacy modules already depend on ``settings`` (see docstring there).

Embeddings: **canonical** env names for the main stack are documented in the
``# CANONICAL EMBEDDING CONFIG`` block below (``EMBEDDING_MODEL``, ``VOYAGE_API_KEY``, plus
optional ``EMBEDDING_*`` read by :mod:`aihub.embedding_engine`). Legacy fastembed uses only
``AIHUB_EMBED_MODEL`` via :mod:`aihub.core.config` / ``aihub.memory.embedder`` — not Voyage.
"""

import os
import re
from pathlib import Path

from aihub.secret_resolver import resolve_llm_api_key, validate_vault_secret_material


def _env_bool(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env_refs(value: str, scope: dict[str, str]) -> str:
    resolved = str(value or "")

    for _ in range(8):
        next_value = _ENV_REF_RE.sub(lambda m: scope.get(m.group(1), ""), resolved)
        if next_value == resolved:
            break
        resolved = next_value

    return resolved


def _load_local_env_file() -> None:
    """Load from .env file ONLY in development mode.
    In production, env vars MUST be set via environment.
    """
    # Check environment mode
    env_mode = os.getenv("ENV", "development").lower().strip()

    # In production, skip .env loading (enforce environment variables)
    if env_mode == "production":
        return

    # Development: load .env for convenience
    env_file = Path(
        os.getenv(
            "AIHUB_ENV_FILE",
            str(Path(__file__).resolve().parents[1] / ".env"),
        )
    )

    if not env_file.exists():
        return

    raw: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()

        if value and value[0] in {'"', "'"} and value[-1] == value[0]:
            value = value[1:-1]

        if key:
            raw[key] = value

    if not raw:
        return

    resolved: dict[str, str] = {}
    for _ in range(8):
        changed = False
        scope = {**raw, **os.environ, **resolved}
        for key, value in raw.items():
            new_value = _resolve_env_refs(value, scope)
            if resolved.get(key) != new_value:
                changed = True
            resolved[key] = new_value
        if not changed:
            break

    for key, value in resolved.items():
        os.environ.setdefault(key, value)


_load_local_env_file()


def _validate_production_secrets() -> None:
    """Validate that all required API keys and security settings are set in production.

    Also enforces two fail-fast invariants that previously failed OPEN (silently disabled)
    when unset:
    - ``AIHUB_USER_VAULT_KEY`` (see ``aihub.user_vault._fernet``): without it, production would
      silently fall back to a deterministic, derivable-from-source encryption key.
    - At least one HTTP hub auth secret (see ``aihub.auth_patch.collect_hub_auth_secrets``):
      without it, ``aihub.main._auth_middleware`` skips authentication entirely for every
      non-public path.
    """
    env_mode = os.getenv("ENV", "development").lower().strip()
    if env_mode != "production":
        return

    required_secrets = {
        "BRAVE_API_KEY": "Search API key (Brave Search)",
        "VOYAGE_API_KEY": "Embeddings API key (Voyage)",
        "AIHUB_USER_VAULT_KEY": "User vault encryption key (aihub.user_vault) - no dev fallback in production",
    }

    missing = []
    if not resolve_llm_api_key():
        missing.append(
            "LLM_API_KEY / DEEPINFRA_API_KEY: one LLM provider credential is required"
        )
    for key, description in required_secrets.items():
        value = os.getenv(key, "").strip()
        if not value:
            missing.append(f"{key}: {description}")
        elif key == "AIHUB_USER_VAULT_KEY":
            validate_vault_secret_material(value)

    # Import kept local: aihub.auth_patch has no dependency back on aihub.config, but this
    # keeps the dependency direction explicit and avoids import-order surprises at module load.
    from aihub.auth_patch import collect_hub_auth_secrets

    if not collect_hub_auth_secrets():
        missing.append(
            "AIHUB_API_KEY / HUB_API_KEY / API_KEY / AIHUB_PROXY_TOKEN: at least one HTTP hub "
            "auth secret is required - without one, the auth middleware fails OPEN and every "
            "non-public endpoint becomes unauthenticated"
        )

    if missing:
        raise RuntimeError(
            "Production mode: missing required env vars:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\nSet ENV=production only when all API keys and auth secrets are available in environment."
        )


APP_NAME = os.getenv("APP_NAME", "AIHub")
HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8080"))

DOMAIN = os.getenv("DOMAIN", "")
PUBLIC_URL = os.getenv("PUBLIC_URL", "")

BASE_DIR = Path(os.getenv("BASE_DIR", str(Path.cwd()))).resolve()
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "aihub.sqlite3"))).resolve()

# Memory sizing
STM_MAX_MESSAGES = int(os.getenv("STM_MAX_MESSAGES", "200"))
LTM_MAX_FACTS_PER_USER = int(os.getenv("LTM_MAX_FACTS_PER_USER", "20000"))
EPISODES_MAX_PER_USER = int(os.getenv("EPISODES_MAX_PER_USER", "20000"))

# Vector settings
VEC_MAX_VOCAB = int(os.getenv("VEC_MAX_VOCAB", "60000"))
VEC_MAX_DF = float(os.getenv("VEC_MAX_DF", "0.90"))
VEC_MIN_DF = int(os.getenv("VEC_MIN_DF", "2"))
VEC_MAX_TOKENS_PER_DOC = int(os.getenv("VEC_MAX_TOKENS_PER_DOC", "6000"))

# CANONICAL EMBEDDING CONFIG
# Używaj tylko tych zmiennych:
# - EMBEDDING_MODEL
# - VOYAGE_API_KEY
# (Pełny zestaw sterujący silnikiem — także EMBEDDING_PROVIDER, EMBEDDING_OUTPUT_DIM, … —
#  jest wczytywany poniżej; domyślne wartości w kodzie. Legacy: AIHUB_EMBED_MODEL → wyłącznie
#  stos fastembed w aihub/memory/embedder.py, nie ten blok.)
#
# Embedding settings (Voyage API + fallback)
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "")
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "voyage").lower().strip()
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "voyage-4-large")
EMBEDDING_OUTPUT_DIM = int(os.getenv("EMBEDDING_OUTPUT_DIM", "1024"))
EMBEDDING_OUTPUT_DTYPE = os.getenv("EMBEDDING_OUTPUT_DTYPE", "float")
EMBEDDING_TIMEOUT_SECONDS = float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "30.0"))
EMBEDDING_MAX_RETRIES = int(os.getenv("EMBEDDING_MAX_RETRIES", "2"))
EMBEDDING_HEALTHCHECK_LIVE_PROBE = _env_bool("EMBEDDING_HEALTHCHECK_LIVE_PROBE", "1")

# Search API
BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")

# Web fetch
HTTP_TIMEOUT_S = float(os.getenv("HTTP_TIMEOUT_S", "12"))
HTTP_MAX_BYTES = int(os.getenv("HTTP_MAX_BYTES", str(2 * 1024 * 1024)))
HTTP_MAX_REDIRECTS = int(os.getenv("HTTP_MAX_REDIRECTS", "5"))
HTTP_CA_BUNDLE = os.getenv(
    "HTTP_CA_BUNDLE",
    os.getenv(
        "SSL_CERT_FILE",
        os.getenv("REQUESTS_CA_BUNDLE", os.getenv("CURL_CA_BUNDLE", "")),
    ),
).strip()
HTTP_TRUST_ENV = _env_bool("HTTP_TRUST_ENV", "0")

# LLM provider/runtime
LLM_PROVIDER_NAME = os.getenv("LLM_PROVIDER_NAME", "deepinfra")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "openai/gpt-oss-120b")
LLM_API_KEY = resolve_llm_api_key()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.deepinfra.com/v1/openai")
LLM_TIMEOUT_S = float(os.getenv("LLM_TIMEOUT_S", "45"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_DEFAULT_TEMPERATURE = float(os.getenv("LLM_DEFAULT_TEMPERATURE", "0.35"))
LLM_TOOL_CALLING_ENABLED = _env_bool("LLM_TOOL_CALLING_ENABLED", "1")
LLM_STREAMING_ENABLED = _env_bool("LLM_STREAMING_ENABLED", "0")

# Chat runtime
CHAT_DEFAULT_MODE = os.getenv("CHAT_DEFAULT_MODE", "chat")
CHAT_MAX_TOOL_ITERATIONS = int(os.getenv("CHAT_MAX_TOOL_ITERATIONS", "4"))
CHAT_DEBUG_TOOLS_ENABLED = _env_bool("CHAT_DEBUG_TOOLS_ENABLED", "0")

# Historia czatu: rollup + „ogon” surowych tur (chat_context_compose.smart_clip_chat_history)
CHAT_HISTORY_RAW_TAIL = int(os.getenv("CHAT_HISTORY_RAW_TAIL", "28"))
CHAT_HISTORY_ROLLUP_MAX_CHARS = int(os.getenv("CHAT_HISTORY_ROLLUP_MAX_CHARS", "12000"))
CHAT_HISTORY_ROLLUP_SNIP = int(os.getenv("CHAT_HISTORY_ROLLUP_SNIP", "4000"))
CHAT_HISTORY_SMART_TRIM_TRIGGER = int(os.getenv("CHAT_HISTORY_SMART_TRIM_TRIGGER", "140"))

# STT — ``aihub.chat_stt_service`` (faster-whisper lokalnie lub endpoint kompatybilny z OpenAI)
CHAT_STT_ENABLED = _env_bool("CHAT_STT_ENABLED", "0")
CHAT_STT_BACKEND = os.getenv("CHAT_STT_BACKEND", "self_hosted_whisper")
CHAT_STT_MODEL = os.getenv("CHAT_STT_MODEL", "base")
CHAT_STT_DEVICE = os.getenv("CHAT_STT_DEVICE", "cpu")
CHAT_STT_COMPUTE_TYPE = os.getenv("CHAT_STT_COMPUTE_TYPE", "int8")
CHAT_STT_API_URL = os.getenv("CHAT_STT_API_URL", "http://127.0.0.1:8081/v1")
CHAT_STT_API_KEY = os.getenv("CHAT_STT_API_KEY", "")
CHAT_STT_OPENAI_MODEL = os.getenv("CHAT_STT_OPENAI_MODEL", "whisper-1")
CHAT_STT_TIMEOUT_S = float(os.getenv("CHAT_STT_TIMEOUT_S", "120"))

# Vision — opisy załączników graficznych (``aihub.chat_attachment_vision``)
CHAT_VISION_ENABLED = _env_bool("CHAT_VISION_ENABLED", "0")
CHAT_VISION_BACKEND = os.getenv("CHAT_VISION_BACKEND", "ollama")
CHAT_VISION_OLLAMA_URL = os.getenv(
    "CHAT_VISION_OLLAMA_URL", "http://127.0.0.1:11434"
)
CHAT_VISION_MODEL = os.getenv("CHAT_VISION_MODEL", "")
CHAT_VISION_FALLBACK_MODEL = os.getenv("CHAT_VISION_FALLBACK_MODEL", "")
CHAT_VISION_API_URL = os.getenv("CHAT_VISION_API_URL", "")
CHAT_VISION_API_KEY = os.getenv("CHAT_VISION_API_KEY", "")
CHAT_VISION_TIMEOUT_S = float(os.getenv("CHAT_VISION_TIMEOUT_S", "120"))

# FS sandbox root
FS_ROOT = Path(os.getenv("FS_ROOT", str(DATA_DIR / "fs"))).resolve()
FS_ROOT.mkdir(parents=True, exist_ok=True)

# SSE
SSE_KEEPALIVE_S = int(os.getenv("SSE_KEEPALIVE_S", "15"))
SSE_MAX_EVENT_LOG = int(os.getenv("SSE_MAX_EVENT_LOG", "50000"))

# Snapshots
SNAPSHOT_DIR = Path(os.getenv("SNAPSHOT_DIR", str(DATA_DIR / "snapshots"))).resolve()
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

# Status self-heal (``GET /system/self-heal-db/status``) — SQLite z historią „uzdrowionych” ścieżek
_SELF_HEAL_RAW = Path(
    os.getenv("AIHUB_SELF_HEAL_DB", str(DATA_DIR / "self_heal.db"))
).expanduser()
SELF_HEAL_DB_PATH = (
    _SELF_HEAL_RAW.resolve()
    if _SELF_HEAL_RAW.is_absolute()
    else (BASE_DIR / _SELF_HEAL_RAW).resolve()
)


def gpt_openapi_spec_path() -> Path:
    """Path to optional GPT-oriented OpenAPI JSON served at ``GET /gpt-openapi.json``.

    Resolution:
    1. ``AIHUB_GPT_OPENAPI_PATH`` — absolute or relative to :data:`BASE_DIR`.
    2. Default: ``<repo root>/openapi-gpt.json`` next to the ``aihub`` package.
    """
    raw = (os.getenv("AIHUB_GPT_OPENAPI_PATH") or "").strip()
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_absolute() else (BASE_DIR / p).resolve()
    repo_root = Path(__file__).resolve().parents[1]
    return (repo_root / "openapi-gpt.json").resolve()


def safe_join(root: Path, relative: str) -> Path:
    rel = Path(relative)
    if rel.is_absolute():
        raise ValueError("absolute paths are not allowed")
    p = (root / rel).resolve()
    if not str(p).startswith(str(root.resolve())):
        raise ValueError("path escapes sandbox")
    return p


# Validate production secrets on startup
_validate_production_secrets()


def _validate_llm_api_key_on_startup() -> None:
    """Validate LLM API key at startup and log warning if missing."""
    import logging

    logger = logging.getLogger(__name__)

    if not LLM_API_KEY or not LLM_API_KEY.strip():
        logger.warning(
            f"LLM provider: {LLM_PROVIDER_NAME} - LLM_API_KEY is missing. "
            f"Checked sources: LLM_API_KEY, DEEPINFRA_API_KEY env vars. "
            f"Set one to enable LLM functionality."
        )
