"""STT: faster-whisper (self-hosted) lub OpenAI-compatible (opcjonalnie)."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

import httpx

from aihub.config import (
    CHAT_STT_API_KEY,
    CHAT_STT_API_URL,
    CHAT_STT_BACKEND,
    CHAT_STT_COMPUTE_TYPE,
    CHAT_STT_DEVICE,
    CHAT_STT_ENABLED,
    CHAT_STT_MODEL,
    CHAT_STT_OPENAI_MODEL,
    CHAT_STT_TIMEOUT_S,
    HTTP_CA_BUNDLE,
    HTTP_TRUST_ENV,
)

logger = logging.getLogger(__name__)

_whisper_lock = threading.Lock()
_whisper_model: Any = None


def _ssl_verify() -> bool | str:
    if HTTP_CA_BUNDLE:
        return HTTP_CA_BUNDLE
    return True


def _is_self_hosted_backend() -> bool:
    b = (CHAT_STT_BACKEND or "").strip().lower()
    return b in (
        "self_hosted_whisper",
        "faster_whisper",
        "faster-whisper",
        "whisper",
    )


def _get_whisper_model() -> Any:
    global _whisper_model
    with _whisper_lock:
        if _whisper_model is not None:
            return _whisper_model
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(
                "Brak pakietu faster-whisper — zainstaluj: pip install -r requirements.txt"
            ) from e
        logger.info(
            "STT: ładuję faster-whisper model=%s device=%s compute_type=%s",
            CHAT_STT_MODEL,
            CHAT_STT_DEVICE,
            CHAT_STT_COMPUTE_TYPE,
        )
        _whisper_model = WhisperModel(
            CHAT_STT_MODEL,
            device=CHAT_STT_DEVICE,
            compute_type=CHAT_STT_COMPUTE_TYPE,
        )
        return _whisper_model


def _which_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


def _suffix_for_filename(filename: str) -> str:
    low = (filename or "audio.webm").lower()
    for ext in (".webm", ".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".opus", ".flac"):
        if low.endswith(ext):
            return ext
    return ".webm"


def _ffmpeg_to_wav16_mono(
    src: Path, dst_wav: Path, *, timeout: float
) -> tuple[bool, str]:
    ff = _which_ffmpeg()
    if not ff:
        return False, "ffmpeg_not_in_path"
    try:
        proc = subprocess.run(
            [
                ff,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(src),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-c:a",
                "pcm_s16le",
                str(dst_wav),
            ],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "ffmpeg_timeout"
    except Exception as exc:  # noqa: BLE001
        return False, f"ffmpeg_error:{type(exc).__name__}"
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[:400]
        return False, f"ffmpeg_exit_{proc.returncode}:{err}"
    if not dst_wav.is_file() or dst_wav.stat().st_size < 64:
        return False, "ffmpeg_empty_output"
    return True, ""


def _transcribe_file_sync(audio_path: Path) -> tuple[str, str | None]:
    """Zwraca (tekst, kod_błędu)."""
    try:
        model = _get_whisper_model()
    except RuntimeError as e:
        return "", f"stt_model_error:{e}"
    try:
        segments, _info = model.transcribe(
            str(audio_path),
            beam_size=5,
            language="pl",
            vad_filter=False,
        )
        parts: list[str] = []
        for seg in segments:
            t = (seg.text or "").strip()
            if t:
                parts.append(t)
        text = " ".join(parts).strip()
        if not text:
            return "", "stt_no_speech_detected"
        return text, None
    except Exception as exc:  # noqa: BLE001
        logger.exception("faster-whisper transcribe failed")
        return "", f"stt_transcribe:{type(exc).__name__}:{exc!s}"[:500]


async def _transcribe_self_hosted(*, data: bytes, filename: str) -> dict[str, Any]:
    if not data:
        return {"ok": False, "code": "empty_audio", "error": "Pusty plik audio."}
    if len(data) > 25 * 1024 * 1024:
        return {
            "ok": False,
            "code": "audio_too_large",
            "error": "Plik audio przekracza 25 MiB.",
        }

    suffix = _suffix_for_filename(filename)
    tmp_in = (
        Path(
            tempfile.mkdtemp(prefix="morda_stt_"),
        )
        / f"in{suffix}"
    )
    tmp_wav = tmp_in.parent / "converted.wav"
    try:
        tmp_in.write_bytes(data)
        path_for_whisper: Path = tmp_in
        ok_ff, ff_code = _ffmpeg_to_wav16_mono(
            tmp_in, tmp_wav, timeout=min(CHAT_STT_TIMEOUT_S, 120.0)
        )
        if ok_ff:
            path_for_whisper = tmp_wav
        else:
            logger.debug(
                "STT: ffmpeg nie skonwertował (%s) — próbuję bezpośrednio na źródle",
                ff_code,
            )

        text, err = await asyncio.wait_for(
            asyncio.to_thread(_transcribe_file_sync, path_for_whisper),
            timeout=CHAT_STT_TIMEOUT_S,
        )
        if text:
            return {"ok": True, "text": text}
        if not ok_ff and ff_code == "ffmpeg_not_in_path":
            return {
                "ok": False,
                "code": "ffmpeg_required",
                "error": (
                    "Nie udało się odczytać audio i brak ffmpeg w PATH — "
                    "zainstaluj ffmpeg (np. apt install ffmpeg)."
                ),
            }
        return {
            "ok": False,
            "code": err or "stt_failed",
            "error": (
                "Transkrypcja nie powiodła się. Sprawdź format nagrania, ffmpeg i log serwera."
            ),
        }
    except asyncio.TimeoutError:
        return {
            "ok": False,
            "code": "stt_timeout",
            "error": f"Przekroczono czas transkrypcji ({int(CHAT_STT_TIMEOUT_S)} s).",
        }
    finally:
        try:
            if tmp_in.parent.is_dir():
                for p in tmp_in.parent.iterdir():
                    try:
                        p.unlink(missing_ok=True)
                    except OSError as exc:
                        logger.debug("Temporary STT file cleanup skipped for %s: %s", p, exc)
                tmp_in.parent.rmdir()
        except OSError as exc:
            logger.debug("Temporary STT directory cleanup skipped for %s: %s", tmp_in.parent, exc)


async def _transcribe_openai_compatible(
    *, data: bytes, filename: str
) -> dict[str, Any]:
    if not CHAT_STT_API_KEY:
        return {
            "ok": False,
            "code": "stt_not_configured",
            "error": "Brak CHAT_STT_API_KEY dla backendu openai_compatible.",
        }
    if not data:
        return {"ok": False, "code": "empty_audio", "error": "Pusty plik audio."}
    low = filename.lower()
    ct = "audio/webm"
    if low.endswith(".wav"):
        ct = "audio/wav"
    elif low.endswith(".mp3"):
        ct = "audio/mpeg"
    elif low.endswith(".mp4") or low.endswith(".m4a"):
        ct = "audio/mp4"
    files = {"file": (filename, data, ct)}
    form = {"model": CHAT_STT_OPENAI_MODEL or "whisper-1"}
    headers = {"Authorization": f"Bearer {CHAT_STT_API_KEY}"}
    try:
        async with httpx.AsyncClient(
            timeout=CHAT_STT_TIMEOUT_S,
            verify=_ssl_verify(),
            trust_env=HTTP_TRUST_ENV,
        ) as client:
            resp = await client.post(
                CHAT_STT_API_URL,
                headers=headers,
                files=files,
                data=form,
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("stt transport error: %s", exc)
        return {
            "ok": False,
            "code": "stt_transport",
            "error": f"Błąd połączenia z usługą STT: {type(exc).__name__}",
        }

    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:400]
        logger.warning("stt HTTP %s: %s", resp.status_code, detail)
        return {
            "ok": False,
            "code": f"stt_http_{resp.status_code}",
            "error": "Usługa STT zwróciła błąd.",
        }

    try:
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "code": "stt_bad_json",
            "error": f"Niepoprawna odpowiedź STT: {exc}",
        }
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip():
        return {
            "ok": False,
            "code": "stt_no_text",
            "error": "Transkrypcja pusta — spróbuj mówić bliżej mikrofonu.",
        }
    return {"ok": True, "text": text.strip()}


async def transcribe_audio_bytes(
    *,
    data: bytes,
    filename: str = "audio.webm",
) -> dict[str, Any]:
    """Transkrypcja — ``ok`` + ``text`` albo ``ok: False`` i ``error`` / ``code``."""
    if not CHAT_STT_ENABLED:
        return {
            "ok": False,
            "code": "stt_disabled",
            "error": "STT wyłączone (ustaw CHAT_STT_ENABLED=1).",
        }

    backend = (CHAT_STT_BACKEND or "self_hosted_whisper").strip().lower()
    if _is_self_hosted_backend():
        out = await _transcribe_self_hosted(data=data, filename=filename)
    elif backend in ("openai_compatible", "openai", "remote"):
        out = await _transcribe_openai_compatible(data=data, filename=filename)
    else:
        return {
            "ok": False,
            "code": "stt_bad_backend",
            "error": (
                f"Nieznany CHAT_STT_BACKEND={backend!r}. "
                "Użyj self_hosted_whisper lub openai_compatible."
            ),
        }

    if out.get("ok") and isinstance(out.get("text"), str):
        out = {**out, "text": out["text"].strip()}
    return out
