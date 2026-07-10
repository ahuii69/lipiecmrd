#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E2E cockpit (Playwright, async) — prawdziwy frontend + backend, bez mocków.

Wymaga uruchomionego cockpitu (np. http://localhost:3000) i działającego API.

    pip install playwright
    playwright install chromium

Uruchomienie::

    python -m aihub.scripts.cockpit_e2e_test

Zmienne środowiskowe::

    COCKPIT_E2E_BASE_URL   — domyślnie http://localhost:3000
    COCKPIT_E2E_HEADLESS   — 1 (domyślnie) lub 0 (okno przeglądarki)
    COCKPIT_E2E_WAIT_SEC   — ile sekund czekać na port frontendu (domyślnie 90);
                             ustaw 0 aby wyłączyć

Uwaga API Python: ``Locator.last`` / ``Locator.first`` to **właściwości**;
``locator.last()`` wywołuje zwrócony ``Locator`` jak funkcję → TypeError.
Ostatnią bańkę asystenta wybieramy przez ``count()`` + ``nth(n - 1)``.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import urllib.parse
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")

BASE_URL = os.getenv("COCKPIT_E2E_BASE_URL", "http://localhost:3000").rstrip("/")
USER_CHAT_PATH = "/user"
PAGE_URL = f"{BASE_URL}{USER_CHAT_PATH}"
RESPONSE_TIMEOUT_MS = 30_000
NAV_TIMEOUT_MS = 60_000
MAX_RETRIES_PER_STEP = 2


def _frontend_host_port() -> tuple[str, int]:
    u = urllib.parse.urlparse(BASE_URL)
    host = u.hostname or "localhost"
    if u.port is not None:
        return host, u.port
    if u.scheme == "https":
        return host, 443
    return host, 80


async def _wait_for_frontend_tcp() -> str | None:
    """Zwraca None jeśli port otwarty; opis błędu jeśli timeout."""
    wait_sec = int(os.getenv("COCKPIT_E2E_WAIT_SEC", "90"))
    if wait_sec <= 0:
        return None
    host, port = _frontend_host_port()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_sec
    attempt = 0
    while loop.time() < deadline:
        attempt += 1
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=3.0,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError) as exc:
                _dbg(f"frontend TCP close warning: {exc}")
            _dbg(f"frontend TCP {host}:{port} OK (próba {attempt})")
            return None
        except (asyncio.TimeoutError, OSError, ConnectionError):
            left = max(0, int(deadline - loop.time()))
            if attempt == 1 or attempt % 10 == 0:
                _dbg(
                    f"czekam na frontend {host}:{port}… "
                    f"pozostało ~{left}s (uruchom ./start.sh lub npm run dev w cockpit)"
                )
            await asyncio.sleep(1.0)
    return (
        f"brak połączenia TCP z {host}:{port} po {wait_sec}s — "
        "frontend nie działa. Uruchom: cd /root/morda && ./start.sh "
        "(albo COCKPIT_E2E_WAIT_SEC=0 jeśli serwer już stoi i problem jest inny)."
    )


def _dbg(msg: str) -> None:
    print(f"[cockpit_e2e] {msg}", flush=True)


def _headless() -> bool:
    return os.getenv("COCKPIT_E2E_HEADLESS", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


async def _with_retries(
    label: str,
    fn: Callable[[], Awaitable[T]],
) -> T:
    last_exc: BaseException | None = None
    for attempt in range(1, MAX_RETRIES_PER_STEP + 1):
        try:
            return await fn()
        except BaseException as exc:
            last_exc = exc
            if attempt < MAX_RETRIES_PER_STEP:
                _dbg(f"retry {label} attempt {attempt}/{MAX_RETRIES_PER_STEP}: {exc!r}")
                await asyncio.sleep(2.0)
            else:
                break
    assert last_exc is not None
    raise last_exc


async def _wait_last_assistant_text(page) -> str:
    from playwright.async_api import expect

    items = page.locator('[data-testid="chat-message"][data-role="assistant"]')
    _dbg("waiting for assistant response")
    await expect(items.first).to_be_visible(timeout=RESPONSE_TIMEOUT_MS)
    n = await items.count()
    if n < 1:
        raise RuntimeError("brak wiadomości assistant po expect(first)")
    loc = items.nth(n - 1)
    _dbg(f"targeting last assistant bubble index={n - 1} (count={n})")
    await expect(loc).to_be_visible(timeout=RESPONSE_TIMEOUT_MS)
    await expect(loc).to_have_attribute(
        "data-streaming",
        "false",
        timeout=RESPONSE_TIMEOUT_MS,
    )
    text = (await loc.inner_text()).strip()
    _dbg(f"assistant response captured (len={len(text)})")
    return text


async def _send_chat(page, message: str) -> None:
    inp = page.get_by_test_id("user-chat-input")
    _dbg("locating chat input")
    await inp.wait_for(state="visible", timeout=NAV_TIMEOUT_MS)
    _dbg("input found")
    await inp.fill(message)
    _dbg("message typed")
    await page.get_by_test_id("user-chat-send").click()
    _dbg("send clicked")


async def _run_memory_flow(page) -> tuple[bool, str]:
    """Krok 1 + 2: zapamiętanie i odczyt kodu (osobno max 2 próby na krok)."""

    try:
        tcp_err = await _wait_for_frontend_tcp()
        if tcp_err:
            return False, tcp_err
        _dbg(f"opening page {PAGE_URL}")
        await page.goto(PAGE_URL, wait_until="load", timeout=NAV_TIMEOUT_MS)
        _dbg("page opened")
        await page.get_by_test_id("user-shell").wait_for(
            state="visible",
            timeout=NAV_TIMEOUT_MS,
        )
        _dbg("user-shell visible")
    except BaseException as exc:
        msg = f"{type(exc).__name__}: {exc}"
        if "ERR_CONNECTION_REFUSED" in str(exc) or "Connection refused" in str(
            exc,
        ):
            msg += (
                " | Frontend na "
                f"{BASE_URL} nie odpowiada — dokończ ./start.sh (Next :3000) "
                "lub zwiększ COCKPIT_E2E_WAIT_SEC."
            )
        return False, f"goto / user-shell: {msg}"

    async def _krok1() -> str:
        await _send_chat(
            page,
            "Zapamiętaj: mój kod to KURWA_123",
        )
        t1 = await _wait_last_assistant_text(page)
        if not t1:
            raise RuntimeError("pusta odpowiedź (krok 1)")
        return t1

    try:
        await _with_retries("krok1_remember", _krok1)
    except BaseException as exc:
        return False, f"krok 1: {type(exc).__name__}: {exc}"

    async def _krok2() -> str:
        await _send_chat(page, "Jaki jest mój kod?")
        t2 = await _wait_last_assistant_text(page)
        if not t2:
            raise RuntimeError("pusta odpowiedź (krok 2)")
        if "KURWA_123" not in t2:
            raise RuntimeError(f"brak KURWA_123 w odpowiedzi: {t2[:500]!r}")
        return t2

    try:
        await _with_retries("krok2_recall", _krok2)
    except BaseException as exc:
        return False, f"krok 2: {type(exc).__name__}: {exc}"

    return True, ""


async def _run_web_flow(page) -> tuple[bool, str]:
    """Krok 3: grounding / URL + FastAPI (max 2 próby)."""

    async def _krok3() -> None:
        await _send_chat(
            page,
            "Sprawdź czym jest FastAPI i podaj źródło",
        )
        t = await _wait_last_assistant_text(page)
        if not t:
            raise RuntimeError("pusta odpowiedź (krok 3)")
        low = t.lower()
        if "fastapi" not in low:
            raise RuntimeError(f"brak 'fastapi' w odpowiedzi: {t[:500]!r}")
        if not re.search(r"https?://", t):
            raise RuntimeError(f"brak URL http(s) w odpowiedzi: {t[:500]!r}")

    try:
        await _with_retries("krok3_web", _krok3)
    except BaseException as exc:
        return False, f"krok 3: {type(exc).__name__}: {exc}"

    return True, ""


async def _main_async() -> int:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print(
            "Brak pakietu playwright. Zainstaluj: pip install playwright",
            file=sys.stderr,
        )
        print("Następnie: playwright install chromium", file=sys.stderr)
        return 1

    mem_ok = False
    web_ok = False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=_headless())
        context = await browser.new_context()
        page = await context.new_page()

        try:
            mem_ok, mem_err = await _run_memory_flow(page)
            if mem_ok:
                web_ok, web_err = await _run_web_flow(page)
            else:
                web_ok = False
                web_err = "(pominięty — S1 MEMORY FAIL)"
        finally:
            await context.close()
            await browser.close()

    print("[COCKPIT E2E TEST]")
    print(f"S1 MEMORY: {'OK' if mem_ok else 'FAIL'}")
    if not mem_ok:
        print(f"  → {mem_err}")
    print(f"S2 WEB: {'OK' if web_ok else 'FAIL'}")
    if not web_ok:
        print(f"  → {web_err}")

    final = mem_ok and web_ok
    print(f"FINAL: {'OK' if final else 'FAIL'}")
    return 0 if final else 1


def main() -> int:
    return asyncio.run(_main_async())


if __name__ == "__main__":
    raise SystemExit(main())
