"""Local account authentication, server-side sessions, and ownership policy."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from aihub.db import exec_one, exec_one_rowcount, fetch_one

SESSION_COOKIE_NAME = "aihub_session"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
ACCOUNT_STATUSES = frozenset({"active", "disabled", "locked"})
ACCOUNT_ROLES = frozenset({"admin", "user"})

_PASSWORD_HASHER = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16,
)
_DUMMY_PASSWORD_HASH = _PASSWORD_HASHER.hash("not-a-real-password")


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated local account attached to one request."""

    account_id: str
    username: str
    tenant_id: str
    role: str
    status: str
    session_id: str
    csrf_token: str
    expires_at: float

    @property
    def user_id(self) -> str:
        """Compatibility identity used by existing user-scoped tables."""
        return self.account_id

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.account_id,
            "user_id": self.user_id,
            "username": self.username,
            "tenant_id": self.tenant_id,
            "role": self.role,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class IssuedSession:
    token: str
    principal: Principal


def _normalize_username(username: str) -> str:
    value = (username or "").strip().casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.@+-]{2,127}", value):
        raise ValueError("username must contain 3-128 safe characters")
    return value


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _session_ttl_seconds() -> int:
    raw = (os.getenv("AIHUB_SESSION_TTL_SECONDS") or "43200").strip()
    try:
        return max(300, min(int(raw), 60 * 60 * 24 * 30))
    except ValueError:
        return 43200


def is_production() -> bool:
    return (os.getenv("ENV") or "development").strip().lower() == "production"


def cookie_secure() -> bool:
    configured = (os.getenv("AIHUB_SESSION_COOKIE_SECURE") or "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return is_production()


def ensure_auth_schema() -> None:
    """Create the portable auth schema used by SQLite and PostgreSQL."""
    exec_one(
        """
        CREATE TABLE IF NOT EXISTS auth_accounts (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL,
            updated_at DOUBLE PRECISION NOT NULL
        )
        """
    )
    exec_one(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            account_id TEXT NOT NULL,
            csrf_token TEXT NOT NULL,
            created_at DOUBLE PRECISION NOT NULL,
            expires_at DOUBLE PRECISION NOT NULL,
            last_seen_at DOUBLE PRECISION NOT NULL,
            revoked_at DOUBLE PRECISION,
            FOREIGN KEY(account_id) REFERENCES auth_accounts(id) ON DELETE CASCADE
        )
        """
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_account "
        "ON auth_sessions(account_id, expires_at)"
    )
    exec_one(
        "CREATE INDEX IF NOT EXISTS idx_auth_sessions_token "
        "ON auth_sessions(token_hash)"
    )


class RegistrationClosedError(RuntimeError):
    """Raised when bootstrap registration is no longer available."""


class UsernameTakenError(RuntimeError):
    """Raised when the requested username already exists."""


class WeakPasswordError(ValueError):
    """Raised when a password fails local policy checks."""


def account_count() -> int:
    ensure_auth_schema()
    row = fetch_one("SELECT COUNT(*) AS n FROM auth_accounts")
    return int(row["n"] if row else 0)


def registration_open() -> bool:
    """Bootstrap registration is open only while no local accounts exist."""
    return account_count() == 0


def auth_required() -> bool:
    configured = (os.getenv("AIHUB_AUTH_REQUIRED") or "").strip().lower()
    if configured:
        return configured in {"1", "true", "yes", "on"}
    return is_production() or account_count() > 0


def _validate_password(password: str) -> None:
    if len(password or "") < 12:
        raise WeakPasswordError("password must contain at least 12 characters")


def create_account(
    *,
    username: str,
    password: str,
    role: str = "user",
    status: str = "active",
    account_id: str | None = None,
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Create one local account; there is deliberately no implicit default user."""
    ensure_auth_schema()
    normalized = _normalize_username(username)
    _validate_password(password)
    if role not in ACCOUNT_ROLES:
        raise ValueError(f"unsupported role: {role}")
    if status not in ACCOUNT_STATUSES:
        raise ValueError(f"unsupported status: {status}")

    aid = (account_id or str(uuid.uuid4())).strip()
    tid = (tenant_id or aid).strip()
    if not aid or not tid:
        raise ValueError("account_id and tenant_id must not be empty")
    now = time.time()
    try:
        exec_one(
            """
            INSERT INTO auth_accounts(
                id, username, password_hash, tenant_id, role, status, created_at, updated_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                aid,
                normalized,
                _PASSWORD_HASHER.hash(password),
                tid,
                role,
                status,
                now,
                now,
            ),
        )
    except Exception as exc:
        message = str(exc).lower()
        if "unique" in message or "duplicate" in message:
            raise UsernameTakenError("username already exists") from exc
        raise
    return {
        "id": aid,
        "user_id": aid,
        "username": normalized,
        "tenant_id": tid,
        "role": role,
        "status": status,
    }


def create_bootstrap_admin(*, username: str, password: str) -> dict[str, Any]:
    """Create the first local admin account while the auth store is empty.

    Atomic under the DB lock: concurrent callers lose with RegistrationClosedError
    once any account exists. There is no ENV auto-seed; this is the intentional
    single-operator bootstrap path.
    """
    from aihub.db import _DB_LOCK, _conn

    ensure_auth_schema()
    normalized = _normalize_username(username)
    _validate_password(password)
    aid = str(uuid.uuid4())
    now = time.time()
    password_hash = _PASSWORD_HASHER.hash(password)

    with _DB_LOCK, _conn() as con:
        row = con.execute("SELECT COUNT(*) AS n FROM auth_accounts").fetchone()
        if row is None:
            count = 0
        elif isinstance(row, dict):
            count = int(row.get("n") or 0)
        else:
            try:
                count = int(row["n"])
            except (KeyError, TypeError, IndexError):
                count = int(row[0])
        if count > 0:
            raise RegistrationClosedError("registration closed")
        try:
            con.execute(
                """
                INSERT INTO auth_accounts(
                    id, username, password_hash, tenant_id, role, status, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    aid,
                    normalized,
                    password_hash,
                    aid,
                    "admin",
                    "active",
                    now,
                    now,
                ),
            )
            con.commit()
        except Exception as exc:
            with suppress(Exception):
                con.rollback()
            message = str(exc).lower()
            if "unique" in message or "duplicate" in message:
                raise UsernameTakenError("username already exists") from exc
            raise

    return {
        "id": aid,
        "user_id": aid,
        "username": normalized,
        "tenant_id": aid,
        "role": "admin",
        "status": "active",
    }


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    ensure_auth_schema()
    try:
        normalized = _normalize_username(username)
    except ValueError:
        normalized = ""
    row = fetch_one(
        "SELECT * FROM auth_accounts WHERE username=?",
        (normalized,),
    )
    encoded = str(row["password_hash"]) if row else _DUMMY_PASSWORD_HASH
    try:
        valid = _PASSWORD_HASHER.verify(encoded, password or "")
    except (VerifyMismatchError, InvalidHashError):
        valid = False
    if not row or not valid or str(row["status"]) != "active":
        return None
    if _PASSWORD_HASHER.check_needs_rehash(encoded):
        exec_one(
            "UPDATE auth_accounts SET password_hash=?, updated_at=? WHERE id=?",
            (_PASSWORD_HASHER.hash(password), time.time(), str(row["id"])),
        )
    return dict(row)


def issue_session(account: dict[str, Any], *, now: float | None = None) -> IssuedSession:
    ensure_auth_schema()
    created = time.time() if now is None else float(now)
    expires = created + _session_ttl_seconds()
    session_id = str(uuid.uuid4())
    token = secrets.token_urlsafe(48)
    csrf_token = secrets.token_urlsafe(32)
    exec_one(
        """
        INSERT INTO auth_sessions(
            id, token_hash, account_id, csrf_token, created_at,
            expires_at, last_seen_at, revoked_at
        ) VALUES(?,?,?,?,?,?,?,NULL)
        """,
        (
            session_id,
            _token_hash(token),
            str(account["id"]),
            csrf_token,
            created,
            expires,
            created,
        ),
    )
    return IssuedSession(
        token=token,
        principal=Principal(
            account_id=str(account["id"]),
            username=str(account["username"]),
            tenant_id=str(account["tenant_id"]),
            role=str(account["role"]),
            status=str(account["status"]),
            session_id=session_id,
            csrf_token=csrf_token,
            expires_at=expires,
        ),
    )


def resolve_session(token: str, *, now: float | None = None) -> Principal | None:
    if not token:
        return None
    ensure_auth_schema()
    current = time.time() if now is None else float(now)
    row = fetch_one(
        """
        SELECT
            s.id AS session_id, s.csrf_token, s.expires_at,
            a.id AS account_id, a.username, a.tenant_id, a.role, a.status
        FROM auth_sessions s
        INNER JOIN auth_accounts a ON a.id=s.account_id
        WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?
        """,
        (_token_hash(token), current),
    )
    if not row or str(row["status"]) != "active":
        return None
    exec_one(
        "UPDATE auth_sessions SET last_seen_at=? WHERE id=?",
        (current, str(row["session_id"])),
    )
    return Principal(
        account_id=str(row["account_id"]),
        username=str(row["username"]),
        tenant_id=str(row["tenant_id"]),
        role=str(row["role"]),
        status=str(row["status"]),
        session_id=str(row["session_id"]),
        csrf_token=str(row["csrf_token"]),
        expires_at=float(row["expires_at"]),
    )


def revoke_session(token: str, *, now: float | None = None) -> bool:
    if not token:
        return False
    return (
        exec_one_rowcount(
            """
            UPDATE auth_sessions SET revoked_at=?
            WHERE token_hash=? AND revoked_at IS NULL
            """,
            (time.time() if now is None else float(now), _token_hash(token)),
        )
        > 0
    )


def csrf_valid(principal: Principal, supplied: str) -> bool:
    return bool(supplied) and hmac.compare_digest(principal.csrf_token, supplied)


_RESERVED_PATH_USER_SEGMENTS = frozenset(
    {
        "run",
        "loop",
        "enable",
        "enqueue",
        "tick",
        "status",
        "tasks",
        "goals",
        "turn",
        "session",
        "sessions",
        "capabilities",
        "upload",
        "stt",
        "update",
        "reflect",
        "write",
        "get",
        "search",
        "delete",
        "export",
        "ingest",
        "event",
        "health",
        "ping",
        "ready",
        "login",
        "logout",
        "register",
        "registration-status",
        "me",
        "item",
        "index-jobs",
        "context-pack",
        "train-from-events",
        "stats",
        "fetch",
        "research",
    }
)

_USER_PATH_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"^/system/health/([^/]+)$",
        r"^/sse/([^/]+)$",
        r"^/psyche/([^/]+)$",
        r"^/psyche/runtime/([^/]+)$",
        r"^/psyche/v2/(?:runtime|reflect|policy|history|habits|relations)/([^/]+)$",
        r"^/psyche/v2/([^/]+)$",
        r"^/memory/v2/(?:summary|consolidate|procedures|contradictions|autobio|forgetting|retrieval-explain)/([^/]+)$",
        r"^/memory/v2/autobio/compact/([^/]+)$",
        r"^/cockpit/(?:consistency|reflections|policy|simulations|overview|memory-v2|psyche-v2|identity)/([^/]+)$",
        r"^/goals/([^/]+)$",
        r"^/agent/status/([^/]+)$",
        r"^/agent/tasks/([^/]+)$",
        r"^/agent/tick/([^/]+)$",
        r"^/agent/goals/([^/]+)(?:/|$)",
        r"^/relations/([^/]+)$",
        r"^/procedures/([^/]+)$",
        r"^/identity/([^/]+)$",
        r"^/reflection/([^/]+)$",
        r"^/autobiography/([^/]+)$",
        r"^/fs/([^/]+)$",
        r"^/snapshot/([^/]+)$",
    )
)


def path_user_id(path: str) -> str | None:
    for pattern in _USER_PATH_PATTERNS:
        match = pattern.fullmatch(path)
        if match:
            candidate = match.group(1)
            if candidate in _RESERVED_PATH_USER_SEGMENTS:
                return None
            return candidate
    return None


def json_user_ids(payload: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "user_id" and value is not None:
                found.add(str(value))
            elif isinstance(value, (dict, list)):
                found.update(json_user_ids(value))
    elif isinstance(payload, list):
        for value in payload:
            found.update(json_user_ids(value))
    return found


def request_user_ids(path: str, query_user_id: str | None, payload: Any) -> set[str]:
    found = json_user_ids(payload)
    if query_user_id:
        found.add(query_user_id)
    path_value = path_user_id(path)
    if path_value:
        found.add(path_value)
    return {value.strip() for value in found if value.strip()}


def is_user_scoped_path(path: str) -> bool:
    """Conservative boundary: service credentials never enter user-data domains."""
    if path in {"/auth/me", "/auth/logout"}:
        return True
    return path.startswith(
        (
            "/chat",
            "/memory",
            "/psyche",
            "/sse",
            "/fs",
            "/web/ingest",
            "/snapshot",
            "/system/health/",
            "/goals",
            "/planner",
            "/agent",
            "/relations",
            "/procedures",
            "/identity",
            "/reflection",
            "/autobiography",
            "/cockpit/",
        )
    )


def parse_json_body(raw: bytes, content_type: str) -> Any:
    if not raw or "application/json" not in (content_type or "").lower():
        return None
    try:
        return json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
