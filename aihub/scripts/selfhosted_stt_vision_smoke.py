#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke: Ollama (tags) + opcjonalnie POST /chat/stt (wymaga działającego API + CHAT_STT_ENABLED)."""

from __future__ import annotations

import argparse
import os
import sys
import wave
from pathlib import Path

import httpx


def _wav_silence_16k_mono(path: Path, duration_s: float = 0.4) -> None:
    """Krótki cisza WAV 16-bit mono 16 kHz (wejście dla faster-whisper bez ffmpeg)."""
    nframes = int(16000 * duration_s)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * nframes)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--ollama-url",
        default=os.getenv("CHAT_VISION_OLLAMA_URL", "http://127.0.0.1:11434").rstrip(
            "/"
        ),
    )
    p.add_argument(
        "--hub-url",
        default=os.getenv("AIHUB_SMOKE_BASE_URL", "http://127.0.0.1:8080").rstrip("/"),
    )
    p.add_argument(
        "--api-key",
        default=os.getenv("API_KEY", os.getenv("CHAT_SMOKE_API_KEY", "")).strip(),
    )
    p.add_argument("--skip-stt", action="store_true")
    args = p.parse_args()

    print("[selfhosted_smoke] Ollama GET /api/tags …", flush=True)
    try:
        r = httpx.get(f"{args.ollama_url}/api/tags", timeout=10.0)
    except Exception as exc:
        print(f"[selfhosted_smoke] FAIL ollama reachability: {exc}", file=sys.stderr)
        return 2
    if r.status_code != 200:
        print(
            f"[selfhosted_smoke] FAIL ollama HTTP {r.status_code}: {r.text[:200]}",
            file=sys.stderr,
        )
        return 3
    print("[selfhosted_smoke] PASS ollama /api/tags", flush=True)

    if args.skip_stt:
        return 0

    tmp = Path(os.getenv("TMPDIR", "/tmp")) / "morda_smoke_stt.wav"
    _wav_silence_16k_mono(tmp)
    headers = {}
    if args.api_key:
        headers["x-api-key"] = args.api_key
    print(f"[selfhosted_smoke] Hub POST /chat/stt (WAV) → {args.hub_url} …", flush=True)
    try:
        with tmp.open("rb") as f:
            resp = httpx.post(
                f"{args.hub_url}/chat/stt",
                headers=headers,
                files={"file": ("smoke.wav", f, "audio/wav")},
                timeout=600.0,
            )
    except Exception as exc:
        print(f"[selfhosted_smoke] FAIL stt transport: {exc}", file=sys.stderr)
        return 4
    try:
        body = resp.json()
    except Exception:
        print(
            f"[selfhosted_smoke] FAIL stt not JSON: {resp.text[:300]}", file=sys.stderr
        )
        return 5
    if resp.status_code != 200:
        print(
            f"[selfhosted_smoke] FAIL stt HTTP {resp.status_code}: {body}",
            file=sys.stderr,
        )
        return 6
    if "ok" not in body:
        print(f"[selfhosted_smoke] FAIL stt missing ok field: {body}", file=sys.stderr)
        return 7
    print(
        f"[selfhosted_smoke] PASS stt response: ok={body.get('ok')} code={body.get('code')}",
        flush=True,
    )
    try:
        tmp.unlink(missing_ok=True)
    except OSError as exc:
        print(f"[selfhosted_smoke] cleanup warning: {exc}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
