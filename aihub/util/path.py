import os
import logging
from fastapi import HTTPException

from aihub.core.config import settings

log = logging.getLogger("aihub.path")


def jail_path(p: str) -> str:
    # Normalize input early to avoid invisible garbage breaking jail checks
    raw = p
    p = (p or "").strip()

    if not p:
        raise HTTPException(status_code=400, detail="path required")

    # Expand ~
    if p.startswith("~"):
        p = os.path.expanduser(p)

    root = os.path.abspath(settings.fs_root)

    # Special case: "/" should mean "jail root", not host FS root.
    if os.path.abspath(p) == os.path.abspath(os.sep):
        log.debug("jail_path: raw=%r norm=%r -> root=%r (special '/')", raw, p, root)
        return root

    # If relative, make it relative to jail root
    if not os.path.isabs(p):
        p = os.path.join(root, p)

    # Normalize and collapse stuff like "..", double slashes, etc.
    p = os.path.abspath(p)

    ok = (p == root or p.startswith(root + os.sep))
    if not ok:
        log.warning("jail_path DENY: raw=%r norm=%r root=%r", raw, p, root)
        raise HTTPException(status_code=403, detail="path outside jail")

    log.debug("jail_path OK: raw=%r norm=%r root=%r", raw, p, root)
    return p
