import base64
import hmac
import hashlib
import json
import time
from typing import Dict, Any

from aihub.core.config import settings


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64urldec(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode((s + pad).encode("utf-8"))


def mint(scope: str, payload: Dict[str, Any], exp_sec: int) -> str:
    now = int(time.time())
    exp = now + max(10, min(int(exp_sec), 3600))
    obj = {"scope": scope, "iat": now, "exp": exp, "p": payload}
    raw = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(settings.token_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    return f"{_b64url(raw)}.{_b64url(sig)}"


def verify(token: str, scope: str) -> Dict[str, Any]:
    if "." not in token:
        raise ValueError("bad token")
    a, b = token.split(".", 1)
    raw = _b64urldec(a)
    sig = _b64urldec(b)
    good = hmac.new(settings.token_secret.encode("utf-8"), raw, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, good):
        raise ValueError("bad signature")
    obj = json.loads(raw.decode("utf-8"))
    if obj.get("scope") != scope:
        raise ValueError("bad scope")
    exp = int(obj.get("exp", 0))
    if int(time.time()) > exp:
        raise ValueError("expired")
    return obj
