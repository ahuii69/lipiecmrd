#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zaszyfrowany magazyn sekretów użytkownika (osobno od STM/LTM i wektorów).

Logika produktowa, NLU i granice względem pamięci: pakiet ``aihub.vault``.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import time
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from aihub.db import exec_one, fetch_all, fetch_one
from aihub.secret_resolver import validate_vault_secret_material

logger = logging.getLogger(__name__)

# One-time (per process) warning latch so the dev-fallback notice doesn't spam logs.
_dev_fallback_warned = [False]


def _fernet() -> Fernet:
    """Build the Fernet cipher used to encrypt/decrypt vault entries.

    Key resolution order:
    1. ``AIHUB_USER_VAULT_KEY`` from the environment (raw 44-char urlsafe-base64 Fernet key,
       or arbitrary secret material that gets SHA-256-derived into a Fernet key).
    2. A deterministic fallback exists only under the explicit
       ``AIHUB_LOCAL_TEST_PROFILE=1`` test profile.
    """
    raw = os.environ.get("AIHUB_USER_VAULT_KEY", "").strip()
    if raw:
        validate_vault_secret_material(raw)
        try:
            b = raw.encode("ascii", errors="strict")
            if len(b) == 44:
                return Fernet(b)
        except (ValueError, UnicodeEncodeError) as exc:
            logger.debug("AIHUB_USER_VAULT_KEY is not a raw Fernet key; deriving key material: %s", exc)
        key_mat = hashlib.sha256(raw.encode("utf-8")).digest()
        return Fernet(base64.urlsafe_b64encode(key_mat))

    env_mode = os.environ.get("ENV", "development").strip().lower()
    local_test_profile = (
        os.environ.get("AIHUB_LOCAL_TEST_PROFILE", "").strip() == "1"
        and env_mode in {"development", "dev", "test", "testing"}
    )
    if not local_test_profile:
        raise RuntimeError(
            "AIHUB_USER_VAULT_KEY is required outside the explicit local test profile"
        )

    from aihub.config import DB_PATH

    if not _dev_fallback_warned[0]:
        logger.warning(
            "Using deterministic vault key under explicit AIHUB_LOCAL_TEST_PROFILE"
        )
        _dev_fallback_warned[0] = True

    dev_seed = hashlib.sha256(f"aihub-user-vault-dev|{DB_PATH}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(dev_seed))


def normalize_alias(alias: str) -> str:
    s = (alias or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


class UserVault:
    def upsert(self, user_id: str, alias: str, secret_plain: str) -> None:
        uid = (user_id or "").strip() or "default"
        key = normalize_alias(alias)
        if not key:
            raise ValueError("empty_alias")
        f = _fernet()
        blob = f.encrypt((secret_plain or "").encode("utf-8"))
        ts = time.time()
        exec_one(
            """
            INSERT INTO user_vault_entries (user_id, alias_key, ciphertext, updated_ts)
            VALUES (?,?,?,?)
            ON CONFLICT(user_id, alias_key) DO UPDATE SET
                ciphertext=excluded.ciphertext,
                updated_ts=excluded.updated_ts
            """,
            (uid, key, blob, ts),
        )

    def get_plain(self, user_id: str, alias: str) -> Optional[str]:
        uid = (user_id or "").strip() or "default"
        key = normalize_alias(alias)
        if not key:
            return None
        row = fetch_one(
            "SELECT ciphertext FROM user_vault_entries WHERE user_id=? AND alias_key=?",
            (uid, key),
        )
        if not row:
            return None
        try:
            return _fernet().decrypt(bytes(row["ciphertext"])).decode("utf-8")
        except InvalidToken:
            logger.warning("user_vault decrypt failed")
            return None

    def delete(self, user_id: str, alias: str) -> bool:
        uid = (user_id or "").strip() or "default"
        key = normalize_alias(alias)
        if not key:
            return False
        exists = fetch_one(
            "SELECT 1 AS o FROM user_vault_entries WHERE user_id=? AND alias_key=?",
            (uid, key),
        )
        if not exists:
            return False
        exec_one(
            "DELETE FROM user_vault_entries WHERE user_id=? AND alias_key=?",
            (uid, key),
        )
        return True

    def list_alias_keys(self, user_id: str) -> list[str]:
        uid = (user_id or "").strip() or "default"
        rows = fetch_all(
            "SELECT alias_key FROM user_vault_entries WHERE user_id=? ORDER BY alias_key ASC",
            (uid,),
        )
        return [str(r["alias_key"]) for r in rows]


_vault_singleton: Optional[UserVault] = None


def get_user_vault() -> UserVault:
    global _vault_singleton
    if _vault_singleton is None:
        _vault_singleton = UserVault()
    return _vault_singleton
