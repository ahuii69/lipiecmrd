"""ARCHIVED — 06.07 repair sprint (P0 security).

This module is **not part of the ``aihub`` Python package** and is **not importable, mounted,
or reachable from the running application** in any way. It is kept here, outside the runtime
tree, purely as historical reference.

Why archived instead of left in ``aihub/api/`` as "unmounted":
- ``POST /python/run`` executed arbitrary Python source via ``subprocess.run([python, "-c", code])``
  with attacker-supplied ``code`` — remote code execution if this router were ever mounted.
- ``/docker/info`` and ``/docker/ps`` shelled out to the ``docker`` CLI.
- A prior import-smoke test imported this module "because it exists", which gave false comfort
  that "unmounted == safe". It does not: a single accidental ``app.include_router(...)`` line
  would have exposed RCE. See ``aihub/api/_LEGACY.md`` and ``archive/legacy_routers/README.md``.

Do **not** move this back into ``aihub/`` or add any ``include_router`` for it without a
deliberate, reviewed security decision.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
import subprocess
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# LEGACY / ARCHIVED / NOT PART OF THE aihub PACKAGE: see module docstring above and
# archive/legacy_routers/README.md.


router = APIRouter(prefix="/ai", tags=["ai_compat"])


# ------------------------
# Models
# ------------------------


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(10, ge=1, le=200)
    mode: str = Field("fts", pattern="^(fts|basic)$")


class PyRunRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=20000)
    timeout_sec: int = Field(8, ge=1, le=60)
    python_path: str = Field("/root/ai-hub/.venv/bin/python")
    max_output_bytes: int = Field(200_000, ge=10_000, le=2_000_000)


class AgentRegister(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field("", max_length=2000)
    endpoint: str = Field("", max_length=2000)
    meta: Dict[str, Any] = Field(default_factory=dict)


class AgentCall(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    input: Dict[str, Any] = Field(default_factory=dict)


# ------------------------
# In-memory agents registry
# ------------------------

_AGENTS: Dict[str, Dict[str, Any]] = {}


# ------------------------
# Helpers
# ------------------------


def _run_cmd(cmd: List[str], timeout: int = 5) -> Dict[str, Any]:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
        )
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": p.stdout,
            "stderr": p.stderr,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": f"{type(e).__name__}: {e}",
        }


def _limit_bytes(s: str, max_bytes: int) -> str:
    b = s.encode("utf-8", errors="replace")
    if len(b) <= max_bytes:
        return s
    b = b[:max_bytes]
    return b.decode("utf-8", errors="replace") + "\n...[truncated]\n"


# ------------------------
# Core
# ------------------------


@router.get("/ping")
def ai_ping():
    return {"ok": True, "ts": int(time.time())}


@router.post("/hash/sha256")
def ai_hash_sha256(payload: Dict[str, Any]):
    # expects {"text": "..."} or {"data_b64":"..."}
    if "text" in payload:
        raw = str(payload["text"]).encode("utf-8", errors="replace")
    elif "data_b64" in payload:
        raw = base64.b64decode(str(payload["data_b64"]))
    else:
        raise HTTPException(status_code=422, detail="provide text or data_b64")
    return {"sha256": hashlib.sha256(raw).hexdigest()}


@router.get("/random/hex")
def ai_random_hex(nbytes: int = 16):
    if nbytes < 1 or nbytes > 1024:
        raise HTTPException(status_code=422, detail="nbytes out of range")
    return {"hex": secrets.token_hex(nbytes)}


@router.post("/b64/encode")
def ai_b64_encode(payload: Dict[str, Any]):
    if "text" not in payload:
        raise HTTPException(status_code=422, detail="provide text")
    raw = str(payload["text"]).encode("utf-8", errors="replace")
    return {"b64": base64.b64encode(raw).decode("ascii")}


@router.post("/b64/decode")
def ai_b64_decode(payload: Dict[str, Any]):
    if "b64" not in payload:
        raise HTTPException(status_code=422, detail="provide b64")
    raw = base64.b64decode(str(payload["b64"]))
    return {"text": raw.decode("utf-8", errors="replace")}


# ------------------------
# Search (proxy to your memory endpoints)
# ------------------------


@router.post("/search")
def ai_search(req: SearchRequest):
    """
    Compatibility endpoint.
    Uses local HTTP to call existing memory endpoints (so we don't need to know DB internals here).
    Prefers /memory/search_fts if exists, fallback to /memory/search.
    """
    import requests

    base = "http://127.0.0.1:8080"

    # try fts
    if req.mode == "fts":
        try:
            r = requests.get(
                f"{base}/memory/search_fts",
                params={"query": req.query, "limit": req.limit},
                timeout=5,
            )
            if r.status_code == 200:
                return {"mode": "fts", "result": r.json()}
        except Exception as exc:
            logger.debug("compat /ai search_fts fallback triggered: %s", exc)

    # fallback basic
    try:
        r = requests.get(
            f"{base}/memory/search",
            params={"query": req.query},
            timeout=5,
        )
        if r.status_code == 200:
            return {"mode": "basic", "result": r.json()}
        raise HTTPException(status_code=r.status_code, detail=r.text[:500])
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"search proxy failed: {type(e).__name__}: {e}"
        )


# ------------------------
# Python run
# ------------------------


@router.post("/python/run")
def ai_python_run(req: PyRunRequest):
    py = req.python_path
    if not os.path.exists(py):
        raise HTTPException(status_code=500, detail=f"python not found: {py}")

    # tiny sandbox-ish env
    env = {
        "PYTHONNOUSERSITE": "1",
        "PYTHONUNBUFFERED": "1",
        "PATH": os.environ.get("PATH", ""),
    }

    try:
        p = subprocess.run(
            [py, "-c", req.code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=req.timeout_sec,
            text=True,
            env=env,
        )
        out = _limit_bytes(p.stdout, req.max_output_bytes)
        err = _limit_bytes(p.stderr, req.max_output_bytes)
        return {
            "ok": p.returncode == 0,
            "returncode": p.returncode,
            "stdout": out,
            "stderr": err,
        }
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "timeout"}
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"python run failed: {type(e).__name__}: {e}"
        )


# ------------------------
# Docker info / ps
# ------------------------


@router.get("/docker/info")
def ai_docker_info():
    return _run_cmd(["docker", "info", "--format", "{{json .}}"], timeout=6)


@router.get("/docker/ps")
def ai_docker_ps():
    return _run_cmd(["docker", "ps", "--format", "{{json .}}"], timeout=6)


# ------------------------
# Agents
# ------------------------


@router.post("/agents/register")
def ai_agents_register(req: AgentRegister):
    _AGENTS[req.name] = {
        "name": req.name,
        "description": req.description,
        "endpoint": req.endpoint,
        "meta": req.meta,
        "ts": int(time.time()),
    }
    return {"ok": True, "agent": _AGENTS[req.name]}


@router.get("/agents/list")
def ai_agents_list():
    return {"ok": True, "agents": sorted(_AGENTS.values(), key=lambda x: x["name"])}


@router.post("/agents/call")
def ai_agents_call(req: AgentCall):
    agent = _AGENTS.get(req.name)
    if not agent:
        raise HTTPException(status_code=404, detail="unknown agent")
    # compat: for now return echo. Later you can route to real endpoint.
    return {"ok": True, "agent": agent, "output": {"echo": req.input}}
    # compat: for now return echo. Later you can route to real endpoint.
    return {"ok": True, "agent": agent, "output": {"echo": req.input}}
