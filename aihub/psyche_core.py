#!/usr/bin/env python3
"""Canonical psyche facade for runtime, HTTP adapters, tools and cockpit.

The project keeps compatibility routes under ``/psyche/*`` and the structured
Psyche V2 surface under ``/psyche/v2/*``. Both route families use this single
facade, so chat, agent, memory writeback and cockpit all share the same V2
service instance and the same v1 state helpers.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from aihub.psyche_v2_service import PsycheV2Service

_psyche_core_singleton: PsycheCanonicalCore | None = None


def get_psyche_core() -> PsycheCanonicalCore:
    global _psyche_core_singleton
    if _psyche_core_singleton is None:
        _psyche_core_singleton = PsycheCanonicalCore()
    return _psyche_core_singleton


class PsycheCanonicalCore:
    """Single entry for v1 psyche row + Psyche V2 orchestration."""

    __slots__ = ("_v2",)

    def __init__(self) -> None:
        self._v2 = PsycheV2Service()

    @property
    def v2_service(self) -> PsycheV2Service:
        """Shared :class:`PsycheV2Service` (agent/chat/cockpit/HTTP/runtime bridge)."""
        return self._v2

    def ensure_user(self, user_id: str) -> Dict[str, Any]:
        from aihub.psyche_engine import ensure_user as _ensure

        return _ensure(user_id)

    def evolve(self, user_id: str, text: str, role: str) -> Dict[str, Any]:
        from aihub.psyche_engine import evolve as _evolve

        return _evolve(user_id, text, role)

    def evolve_once(
        self,
        user_id: str,
        text: str,
        role: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """Apply a durable event once, including after worker restart."""
        from aihub.db import exec_one, exec_one_rowcount, fetch_one, now_ts

        exec_one(
            """
            CREATE TABLE IF NOT EXISTS psyche_idempotency (
                idempotency_key TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                role TEXT NOT NULL,
                completed_ts REAL
            )
            """
        )
        prior = fetch_one(
            "SELECT completed_ts FROM psyche_idempotency WHERE idempotency_key=?",
            (idempotency_key,),
        )
        if prior is not None:
            return self.ensure_user(user_id)
        inserted = exec_one_rowcount(
            """
            INSERT INTO psyche_idempotency(idempotency_key,user_id,role,completed_ts)
            VALUES(?,?,?,?)
            ON CONFLICT(idempotency_key) DO NOTHING
            """,
            (idempotency_key, user_id, role, now_ts()),
        )
        if inserted == 0:
            return self.ensure_user(user_id)
        return self.evolve(user_id, text, role)

    def reflect(self, user_id: str, context: List[Dict[str, Any]]) -> Dict[str, Any]:
        from aihub.psyche_engine import reflect as _reflect

        return _reflect(user_id, context)

    def analyze_sentiment(self, text: str) -> Tuple[float, float, Dict[str, Any]]:
        from aihub.psyche_engine import analyze_sentiment as _sent

        return _sent(text)

    def v2_event_history(self, user_id: str, limit: int = 50):
        """Recent V2 events (HTTP ``/psyche/v2/history`` adapter)."""
        from aihub.psyche_v2_repository import get_recent_psyche_events

        return get_recent_psyche_events(user_id, limit=limit)
