from __future__ import annotations

import binascii
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, Field

from aihub.fs import manager

# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.


router = APIRouter(prefix="/fs", tags=["fs"])


class WriteReq(BaseModel):
    path: str = Field(..., min_length=1)
    content_base64: str = Field(..., min_length=1)


@router.get("/list")
def list_dir(path: str = "/") -> List[Dict[str, Any]]:
    try:
        return manager.list_dir(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="path outside jail")


@router.get("/read")
def read_file(path: str) -> Dict[str, Any]:
    try:
        return manager.read_file(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="path outside jail")


@router.post("/write")
async def write_file(
    request: Request,
    req: Optional[WriteReq] = Body(default=None),
    path: Optional[str] = None,
    content_base64: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Backward-compatible write:
    - JSON body: {"path": "...", "content_base64": "..."}
    - OR query params: /fs/write?path=...&content_base64=...
    """
    # 1) Prefer explicit JSON body (req), if provided
    if req is not None:
        use_path = req.path
        use_b64 = req.content_base64
    else:
        # 2) If no body model parsed, try reading raw JSON (some clients send JSON but FastAPI didn't parse)
        use_path = path
        use_b64 = content_base64

        if (use_path is None or use_b64 is None) and request.headers.get(
            "content-type", ""
        ).lower().startswith("application/json"):
            try:
                payload = await request.json()
                if isinstance(payload, dict):
                    use_path = use_path or payload.get("path")
                    use_b64 = use_b64 or payload.get("content_base64")
            except Exception as exc:
                logger.debug("filesystem upload JSON body parse failed; validating query params: %s", exc)

    if not use_path or not use_b64:
        raise HTTPException(
            status_code=422,
            detail="missing required fields: path and content_base64 (send JSON body or query params)",
        )

    try:
        return manager.write_file(use_path, use_b64)
    except PermissionError:
        raise HTTPException(status_code=403, detail="path outside jail")
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="invalid base64")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"write failed: {e}")


@router.post("/mkdir")
def mkdir(path: str) -> Dict[str, Any]:
    try:
        return manager.mkdir(path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="path outside jail")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"mkdir failed: {e}")


@router.post("/delete")
def delete(path: str) -> Dict[str, Any]:
    try:
        return manager.delete(path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="path outside jail")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"delete failed: {e}")


@router.post("/move")
def move(src: str, dst: str) -> Dict[str, Any]:
    try:
        return manager.move(src, dst)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="not found")
    except PermissionError:
        raise HTTPException(status_code=403, detail="path outside jail")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"move failed: {e}")
