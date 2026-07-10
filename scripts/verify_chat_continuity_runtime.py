#!/usr/bin/env python3
"""Weryfikacja runtime ciągłości czatu i STM w system prompt (pełna ścieżka ChatRuntime).

Nie wymaga działającego LLM — provider jest prawdziwy w produkcji; tutaj testy
ustawiają fałszywkę przez pytest + monkeypatch (jak w CI).

Uruchom z katalogu repo morda::

    python scripts/verify_chat_continuity_runtime.py

Kod źródłowy scenariuszy: ``tests/test_chat_continuity_runtime_scenarios.py``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        str(root / "tests" / "test_chat_continuity_runtime_scenarios.py"),
        "-v",
        "-s",
        "--tb=short",
    ]
    print("Running:", " ".join(cmd), flush=True)
    return subprocess.call(cmd, cwd=str(root))


if __name__ == "__main__":
    raise SystemExit(main())
