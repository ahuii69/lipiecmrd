#!/usr/bin/env python3
"""Sprawdza, że cockpit-proxy-allowlist.json ⊆ CANONICAL_HTTP_ROUTES.

Uruchom z katalogu repo (wymaga PYTHONPATH=. lub pip install -e .)::

    python3 scripts/check_allowlist_canonical_sync.py

Kod wyjścia 0 = OK, 1 = brak zgodności.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))

    from aihub.canonical_http_surface import CANONICAL_HTTP_ROUTES

    path = root / "cockpit" / "lib" / "api" / "cockpit-proxy-allowlist.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    routes = data.get("routes")
    if not isinstance(routes, list):
        print("cockpit-proxy-allowlist.json: brak tablicy routes", file=sys.stderr)
        return 1

    allow_keys = {(str(r["method"]).upper(), str(r["path"])) for r in routes if isinstance(r, dict)}
    canonical_keys = {(m, p) for m, p, *_ in CANONICAL_HTTP_ROUTES}
    missing = sorted(allow_keys - canonical_keys)
    if missing:
        print("FAIL: wpisy allowlisty nie ma w CANONICAL_HTTP_ROUTES:", file=sys.stderr)
        for item in missing:
            print(f"  {item[0]} {item[1]}", file=sys.stderr)
        return 1

    print(f"OK: allowlist ⊆ canonical ({len(allow_keys)} tras).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
