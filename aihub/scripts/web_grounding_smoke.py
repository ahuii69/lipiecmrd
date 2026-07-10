#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real web-grounding smoke: ChatRuntime + configured LLM + Brave/fetch tools.

Loads environment from ``.env`` via ``aihub.config`` (development).

Run from repo root::

    python -m aihub.scripts.web_grounding_smoke
    python -m aihub.scripts.web_grounding_smoke --log-file artifacts/web_grounding_smoke/run.log

Exit code ``0`` if all scenarios pass, ``1`` otherwise. ``--log-file`` zapisuje te same
linie co podsumowanie na stdout (nagłówek ze znacznikiem czasu UTC).

Walidacja (dopasowanie do produkcyjnego trace):

- **S1:** ``tool_verified`` albo ``fallback`` po ``ProviderError``, o ile
  ``used_tools`` i był udany ``research.query`` / ``web.fetch_url`` (synteza LLM
  mogła się nie domknąć, ale web się wykonał).
- **S3:** oficjalna domena musi pojawić się albo w args/tekście ``web.fetch_url``,
  albo w URL wyników ``research.query`` (model często robi najpierw search).

Do **5 prób** na scenariusz (fluktuacja odpowiedzi LLM, bez mocków).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TextIO

# Side effect: loads .env in development (see aihub.config).
import aihub.config  # noqa: F401  # pylint: disable=unused-import
from aihub.chat_contracts import ChatTurnInput, ToolCallResult
from aihub.chat_runtime import ChatRuntime
from aihub.psyche_engine import ensure_user
from aihub.tools.router import _normalize_tool_name


class _TeeStdout:
    """Duplicate ``print`` output to a log file without changing validation code."""

    __slots__ = ("_primary", "_extra")

    def __init__(self, primary: TextIO, extra: TextIO) -> None:
        self._primary = primary
        self._extra = extra

    def write(self, data: str) -> int:
        self._primary.write(data)
        self._extra.write(data)
        self._primary.flush()
        self._extra.flush()
        return len(data)

    def flush(self) -> None:
        self._primary.flush()
        self._extra.flush()

    def __getattr__(self, name: str):
        return getattr(self._primary, name)


def _norm_tool(name: str) -> str:
    return _normalize_tool_name(name or "")


def _norm_tool_set(results: Iterable[ToolCallResult]) -> set[str]:
    return {_norm_tool(r.name) for r in results}


def _grounding_ok_for_s1(tr: dict) -> bool:
    """tool_verified, albo fallback po błędzie providera mimo udanych narzędzi web."""
    gm = tr.get("response_grounding_mode")
    if gm == "tool_verified":
        return True
    if gm == "fallback" and tr.get("used_tools") and tr.get("used_fallback"):
        return True
    return False


def _any_ok_web_tool(results: Iterable[ToolCallResult]) -> bool:
    for r in results:
        if not r.ok:
            continue
        n = _norm_tool(r.name)
        if n in ("research.query", "web.fetch_url"):
            return True
    return False


def _row_is_official_fastapi(row: dict) -> bool:
    u = str(row.get("url", "")).lower()
    t = str(row.get("title", "")).lower()
    if "fastapi.tiangolo.com" in u:
        return True
    if "tiangolo.com" in u and "fastapi" in u + t:
        return True
    return False


def _research_hits_official_fastapi(results: Iterable[ToolCallResult]) -> bool:
    for r in results:
        if _norm_tool(r.name) != "research.query" or not r.ok or not r.output:
            continue
        res = r.output.get("result")
        if not isinstance(res, dict):
            continue
        for row in res.get("results") or []:
            if isinstance(row, dict) and _row_is_official_fastapi(row):
                return True
    return False


def _research_mentions_fastapi(results: Iterable[ToolCallResult]) -> bool:
    for r in results:
        if _norm_tool(r.name) != "research.query" or not r.ok or not r.output:
            continue
        res = r.output.get("result")
        if not isinstance(res, dict):
            continue
        for row in res.get("results") or []:
            if not isinstance(row, dict):
                continue
            blob = f"{row.get('url', '')} {row.get('title', '')}".lower()
            if "fastapi" in blob:
                return True
    return False


def _has_http_url(text: str) -> bool:
    return bool(re.search(r"https?://[^\s\)\]>'\"]+", text or "", re.I))


@dataclass
class ScenarioResult:
    ok: bool
    detail: str


async def _run_turn(message: str, uid: str, session_id: str) -> tuple:
    ensure_user(uid)
    turn = ChatTurnInput(
        user_id=uid,
        session_id=session_id,
        message=message,
        mode="chat",
        history=[],
    )
    rt = ChatRuntime()
    out = await rt.run_turn(turn)
    return turn, out


def _validate_s1_bitcoin(out) -> ScenarioResult:
    tr = out.trace or {}
    if not tr.get("controlled_web_triggered"):
        return ScenarioResult(False, "controlled_web_triggered is not True")
    if not _grounding_ok_for_s1(tr):
        return ScenarioResult(
            False,
            f"grounding not acceptable: mode={tr.get('response_grounding_mode')!r} "
            f"used_tools={tr.get('used_tools')} used_fallback={tr.get('used_fallback')}",
        )
    if not _any_ok_web_tool(out.tool_results or []):
        return ScenarioResult(
            False, "no successful research.query/web.fetch_url in tool_results"
        )
    if not _has_http_url(out.response_text or ""):
        return ScenarioResult(False, "response_text has no http(s) URL")
    return ScenarioResult(True, "ok")


def _validate_s2_example(out) -> ScenarioResult:
    tr = out.trace or {}
    names = _norm_tool_set(out.tool_results or [])
    if "web.fetch_url" not in names:
        return ScenarioResult(
            False,
            f"web.fetch_url not in tool_results, got {sorted(names)}",
        )
    if tr.get("controlled_web_ok") is not True:
        return ScenarioResult(
            False,
            f"controlled_web_ok={tr.get('controlled_web_ok')!r} want True",
        )
    if "example domain" not in (out.response_text or "").lower():
        return ScenarioResult(False, 'response missing "Example Domain"')
    return ScenarioResult(True, "ok")


def _validate_s3_fastapi(out) -> ScenarioResult:
    calls = out.tool_calls or []
    results = out.tool_results or []
    if not results:
        return ScenarioResult(False, "empty tool_results")

    def _is_web_fetch(name: str) -> bool:
        return _norm_tool(name) == "web.fetch_url"

    def _url_blob_official_fastapi(blob: str) -> bool:
        b = blob.lower()
        return "fastapi.tiangolo.com" in b or ("tiangolo.com" in b and "fastapi" in b)

    hit_fetch = False
    for c in calls:
        if not _is_web_fetch(c.name):
            continue
        u = str((c.arguments or {}).get("url", "")).lower()
        if _url_blob_official_fastapi(u):
            hit_fetch = True
            break
    if not hit_fetch:
        for r in results:
            if not _is_web_fetch(r.name) or not r.ok or not r.output:
                continue
            res = r.output.get("result")
            if isinstance(res, dict):
                blob = f"{res.get('url', '')} {str(res.get('text', ''))[:12000]}"
                if _url_blob_official_fastapi(blob):
                    hit_fetch = True
                    break

    hit_research = _research_hits_official_fastapi(
        results
    ) or _research_mentions_fastapi(results)

    if not hit_fetch and not hit_research:
        return ScenarioResult(
            False,
            "no web.fetch nor research.query evidence for official FastAPI (tiangolo) URL",
        )
    text = out.response_text or ""
    if not _has_http_url(text):
        return ScenarioResult(False, "response has no URL")
    if "fastapi" not in text.lower():
        return ScenarioResult(False, "response missing FastAPI description hint")
    return ScenarioResult(True, "ok")


async def _main_async() -> int:
    scenarios: list[tuple[str, str, str]] = [
        (
            "S1 Bitcoin",
            "Sprawdź aktualną cenę Bitcoina i podaj źródło.",
            "s1",
        ),
        (
            "S2 example.com",
            "Co jest na stronie https://example.com ?",
            "s2",
        ),
        (
            "S3 FastAPI",
            "Co to jest FastAPI i podaj źródło z oficjalnej strony. "
            "Użyj research.query albo web.fetch_url — odpowiedź musi być poparta narzędziem.",
            "s3",
        ),
    ]

    labels_out: list[tuple[str, ScenarioResult]] = []

    max_attempts = 5

    for label, message, tag in scenarios:
        res = ScenarioResult(False, "no attempts")
        last_out = None
        for attempt in range(1, max_attempts + 1):
            uid = f"web-smoke-{uuid.uuid4()}"
            session_id = f"{tag}-a{attempt}-{uuid.uuid4().hex[:10]}"
            _turn, out = await _run_turn(message, uid, session_id)
            last_out = out

            if tag == "s1":
                res = _validate_s1_bitcoin(out)
            elif tag == "s2":
                res = _validate_s2_example(out)
            else:
                res = _validate_s3_fastapi(out)

            if res.ok:
                break

        if not res.ok and last_out is not None:
            tr = last_out.trace or {}
            res = ScenarioResult(
                False,
                f"{res.detail} | attempts={max_attempts} "
                f"controlled_web_triggered={tr.get('controlled_web_triggered')} "
                f"response_grounding_mode={tr.get('response_grounding_mode')!r}",
            )
        labels_out.append((label, res))

    s1_ok = labels_out[0][1].ok
    s2_ok = labels_out[1][1].ok
    s3_ok = labels_out[2][1].ok

    print("[WEB SMOKE]")
    print(f"S1 Bitcoin: {'OK' if s1_ok else 'FAIL'}")
    if not s1_ok:
        print(f"  -> {labels_out[0][1].detail}")
    print(f"S2 example.com: {'OK' if s2_ok else 'FAIL'}")
    if not s2_ok:
        print(f"  -> {labels_out[1][1].detail}")
    print(f"S3 FastAPI: {'OK' if s3_ok else 'FAIL'}")
    if not s3_ok:
        print(f"  -> {labels_out[2][1].detail}")

    final_ok = s1_ok and s2_ok and s3_ok
    print()
    print(f"FINAL: {'OK' if final_ok else 'FAIL'}")
    return 0 if final_ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="In-process web grounding smoke (real LLM + tools)."
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        metavar="PATH",
        help="Write script stdout (summary lines) to PATH; parent dirs are created.",
    )
    args = parser.parse_args()

    log_fp = None
    old_stdout = sys.stdout
    if args.log_file is not None:
        args.log_file.parent.mkdir(parents=True, exist_ok=True)
        log_fp = args.log_file.open("w", encoding="utf-8")
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        log_fp.write(f"# web_grounding_smoke started {ts} (utc)\n")
        log_fp.write(f"# argv: {sys.argv!r}\n")
        log_fp.flush()
        sys.stdout = _TeeStdout(old_stdout, log_fp)

    try:
        try:
            code = asyncio.run(_main_async())
        except KeyboardInterrupt:
            print("\n[WEB SMOKE] INTERRUPTED", file=sys.stderr)
            code = 1
        ts_end = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if log_fp is not None:
            log_fp.write(f"# finished {ts_end} (utc) exit={code}\n")
            log_fp.flush()
    finally:
        if log_fp is not None:
            sys.stdout = old_stdout
            log_fp.close()

    sys.exit(code)


if __name__ == "__main__":
    main()
