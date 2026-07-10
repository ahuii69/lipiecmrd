"""Administrative API router.

Compatibility module restored for main application bootstrap.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/ping")
def admin_ping() -> dict[str, Any]:
    """Administrative liveness endpoint."""
    return {"ok": True, "scope": "admin"}
