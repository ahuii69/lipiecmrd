"""Minimal chat file uploads: disk + SQLite, text extraction (.txt/.md/.pdf)."""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from aihub.config import DATA_DIR
from aihub.db import _DB_LOCK, _conn, fetch_all, now_ts

UPLOADS_SUBDIR = "uploads"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MiB
MAX_FILES_PER_TURN = 5
MAX_TOTAL_CONTEXT_CHARS = 120_000
MAX_SINGLE_FILE_CHARS = 60_000
PREVIEW_CHARS = 420

_TEXT_SUFFIX = frozenset({".txt", ".md", ".pdf"})
_IMAGE_SUFFIX = frozenset({".png", ".jpg", ".jpeg", ".webp"})
_ALLOWED_SUFFIX = _TEXT_SUFFIX | _IMAGE_SUFFIX


def _safe_filename(name: str) -> str:
    base = Path(name or "file").name
    base = re.sub(r"[^\w.\-]+", "_", base, flags=re.UNICODE)
    return (base[:180] or "file")[:180]


def _read_text_utf8(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8", "utf-8-sig", "cp1250", "latin-1"):
        try:
            return data.decode(enc), "ok"
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "fallback_replaced_chars"


def _extract_pdf_text(data: bytes) -> tuple[str, str, str | None]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        return "", "error", f"pypdf_missing:{e}"

    try:
        from io import BytesIO

        reader = PdfReader(BytesIO(data))
        parts: list[str] = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                parts.append(t)
        text = "\n\n".join(parts).strip()
        if not text:
            return "", "empty", "no_text_extracted"
        return text, "ok", None
    except Exception as e:  # noqa: BLE001
        return "", "error", str(e)[:500]


def extract_file_bytes(
    *,
    suffix: str,
    raw: bytes,
) -> tuple[str, str, str | None]:
    suf = suffix.lower()
    if suf in {".txt", ".md"}:
        text, _st = _read_text_utf8(raw)
        return text.strip(), "ok", None
    if suf == ".pdf":
        return _extract_pdf_text(raw)
    if suf in _IMAGE_SUFFIX:
        # Binarny załącznik — treść wizualna przez osobną ścieżkę (vision) lub jawny fallback.
        return "", "image", None
    return "", "error", "unsupported_type"


def uploads_root() -> Path:
    root = (DATA_DIR / UPLOADS_SUBDIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def insert_upload_row(
    *,
    file_id: str,
    user_id: str,
    session_id: str,
    original_filename: str,
    stored_path: str,
    content_type: str,
    size_bytes: int,
    extracted_text: str,
    extract_status: str,
    extract_error: str | None,
) -> None:
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            INSERT INTO chat_uploaded_files (
                file_id, user_id, session_id, original_filename, stored_path,
                content_type, size_bytes, extracted_text, extract_status,
                extract_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                file_id,
                user_id,
                session_id,
                original_filename,
                stored_path,
                content_type,
                size_bytes,
                extracted_text,
                extract_status,
                extract_error,
                now_ts(),
            ),
        )
        con.commit()


def save_multipart_upload(
    *,
    user_id: str,
    session_id: str,
    filename: str,
    content_type: str,
    file_obj: BinaryIO,
) -> dict[str, Any]:
    raw = file_obj.read()
    if len(raw) > MAX_UPLOAD_BYTES:
        return {
            "ok": False,
            "error": "file_too_large",
            "message": f"Max {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB",
        }

    suf = Path(filename).suffix.lower()
    if suf not in _ALLOWED_SUFFIX:
        return {
            "ok": False,
            "error": "unsupported_type",
            "message": "Dozwolone: .txt, .md, .pdf, .png, .jpg, .jpeg, .webp",
        }

    text, st, err = extract_file_bytes(suffix=suf, raw=raw)
    if st == "image":
        preview = "(plik obrazkowy)"
    else:
        preview = (
            (text[:PREVIEW_CHARS] + ("…" if len(text) > PREVIEW_CHARS else ""))
            if text
            else ""
        )

    file_id = f"cf_{uuid.uuid4().hex}"
    user_dir = uploads_root() / re.sub(r"[^\w\-]+", "_", user_id)[:120]
    user_dir.mkdir(parents=True, exist_ok=True)
    safe = _safe_filename(filename)
    disk_name = f"{file_id}_{safe}"
    path = user_dir / disk_name
    path.write_bytes(raw)

    extract_status = st
    extract_error = err
    if st != "ok":
        preview = preview or ""

    insert_upload_row(
        file_id=file_id,
        user_id=user_id,
        session_id=session_id,
        original_filename=safe,
        stored_path=str(path),
        content_type=content_type or "application/octet-stream",
        size_bytes=len(raw),
        extracted_text=text if st == "ok" else "",
        extract_status=extract_status,
        extract_error=extract_error,
    )

    return {
        "ok": True,
        "file_id": file_id,
        "filename": safe,
        "content_type": content_type or "application/octet-stream",
        "size": len(raw),
        "extracted_text_preview": preview,
        "status": extract_status,
        "extract_error": extract_error,
    }


def fetch_recent_session_attachment_ids(
    *,
    user_id: str,
    session_id: str,
    limit: int = MAX_FILES_PER_TURN,
) -> list[str]:
    """Ostatnie N plików w sesji (chronologicznie: najstarszy → najnowszy; ostatni = najświeższy)."""
    uid = (user_id or "").strip() or "default"
    sid = (session_id or "").strip() or "default"
    lim = max(1, min(int(limit), MAX_FILES_PER_TURN))
    rows = fetch_all(
        """
        SELECT file_id FROM chat_uploaded_files
        WHERE user_id = ? AND session_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (uid, sid, lim),
    )
    ids = [str(r["file_id"]).strip() for r in rows if str(r["file_id"]).strip()]
    ids.reverse()
    return ids


def get_upload_for_user(
    *,
    file_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Load one upload by id scoped to user (for GET /chat/file/{id})."""
    fid = (file_id or "").strip()
    uid = (user_id or "").strip() or "default"
    if not fid:
        return None
    with _DB_LOCK, _conn() as con:
        row = con.execute(
            """
            SELECT file_id, user_id, session_id, original_filename, stored_path,
                   content_type, size_bytes, extract_status
            FROM chat_uploaded_files
            WHERE file_id = ? AND user_id = ?
            """,
            (fid, uid),
        ).fetchone()
    if row is None:
        return None
    return {
        "file_id": row["file_id"],
        "user_id": row["user_id"],
        "session_id": row["session_id"],
        "original_filename": row["original_filename"],
        "stored_path": row["stored_path"] or "",
        "content_type": row["content_type"] or "application/octet-stream",
        "size_bytes": int(row["size_bytes"] or 0),
        "extract_status": row["extract_status"],
    }


def fetch_files_for_ids(
    *,
    user_id: str,
    session_id: str,
    file_ids: list[str],
) -> list[dict[str, Any]]:
    if not file_ids:
        return []
    ids = [str(x).strip() for x in file_ids if str(x).strip()][:MAX_FILES_PER_TURN]
    if not ids:
        return []
    bind_marks = ",".join("?" * len(ids))
    with _DB_LOCK, _conn() as con:
        rows = con.execute(
            f"""
            SELECT file_id, original_filename, stored_path, content_type,
                   extracted_text, extract_status, extract_error, size_bytes
            FROM chat_uploaded_files
            WHERE user_id = ? AND session_id = ? AND file_id IN ({bind_marks})
            """,
            (user_id, session_id, *ids),
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "file_id": r["file_id"],
                "original_filename": r["original_filename"],
                "stored_path": r["stored_path"] or "",
                "content_type": r["content_type"] or "",
                "extracted_text": r["extracted_text"] or "",
                "extract_status": r["extract_status"],
                "extract_error": r["extract_error"],
                "size_bytes": int(r["size_bytes"] or 0),
            }
        )
    return out


def update_file_extraction(
    *,
    file_id: str,
    user_id: str,
    session_id: str,
    extracted_text: str,
    extract_status: str,
    extract_error: str | None = None,
) -> None:
    """Aktualizacja wyniku ekstrakcji (np. podpis vision po pierwszym użyciu w turze)."""
    with _DB_LOCK, _conn() as con:
        con.execute(
            """
            UPDATE chat_uploaded_files
            SET extracted_text = ?, extract_status = ?, extract_error = ?
            WHERE file_id = ? AND user_id = ? AND session_id = ?
            """,
            (
                extracted_text,
                extract_status,
                extract_error,
                file_id,
                user_id,
                session_id,
            ),
        )
        con.commit()


def file_kind_for_row(row: dict[str, Any]) -> str:
    st = str(row.get("extract_status") or "")
    name = str(row.get("original_filename") or "")
    suf = Path(name).suffix.lower()
    if st == "image" or suf in _IMAGE_SUFFIX:
        return "image"
    return "text"


def _image_dimensions(raw: bytes) -> tuple[int, int] | None:
    """Best-effort width/height from image header bytes, no external deps (PNG/GIF/WebP/JPEG).

    Returns ``None`` when the format is unknown or the header is truncated. Never raises.
    """
    try:
        if len(raw) >= 24 and raw[:8] == b"\x89PNG\r\n\x1a\n" and raw[12:16] == b"IHDR":
            w = int.from_bytes(raw[16:20], "big")
            h = int.from_bytes(raw[20:24], "big")
            return (w, h) if w > 0 and h > 0 else None
        if len(raw) >= 10 and raw[:6] in (b"GIF87a", b"GIF89a"):
            w = int.from_bytes(raw[6:8], "little")
            h = int.from_bytes(raw[8:10], "little")
            return (w, h) if w > 0 and h > 0 else None
        if len(raw) >= 30 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            fmt = raw[12:16]
            if fmt == b"VP8 " and len(raw) >= 30:
                w = int.from_bytes(raw[26:28], "little") & 0x3FFF
                h = int.from_bytes(raw[28:30], "little") & 0x3FFF
                return (w, h) if w > 0 and h > 0 else None
            if fmt == b"VP8L" and len(raw) >= 25:
                b = raw[21:25]
                bits = int.from_bytes(b, "little")
                w = (bits & 0x3FFF) + 1
                h = ((bits >> 14) & 0x3FFF) + 1
                return (w, h)
            if fmt == b"VP8X" and len(raw) >= 30:
                w = (int.from_bytes(raw[24:27], "little")) + 1
                h = (int.from_bytes(raw[27:30], "little")) + 1
                return (w, h)
        if len(raw) >= 4 and raw[:2] == b"\xff\xd8":
            # JPEG: walk the marker segments to the first Start-Of-Frame.
            i = 2
            n = len(raw)
            while i + 9 < n:
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                    h = int.from_bytes(raw[i + 5 : i + 7], "big")
                    w = int.from_bytes(raw[i + 7 : i + 9], "big")
                    return (w, h) if w > 0 and h > 0 else None
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg_len = int.from_bytes(raw[i + 2 : i + 4], "big")
                if seg_len < 2:
                    break
                i += 2 + seg_len
    except Exception:  # noqa: BLE001 — metadata is best-effort, never fatal
        return None
    return None


def _human_size(num_bytes: int) -> str:
    if num_bytes <= 0:
        return "0 B"
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / (1024 * 1024):.1f} MB"


def image_basic_metadata(row: dict[str, Any]) -> str:
    """Concrete, honest local metadata for an image (format, WxH if parseable, size). No fake vision."""
    name = str(row.get("original_filename") or "")
    suf = Path(name).suffix.lower().lstrip(".") or "obraz"
    size = int(row.get("size_bytes") or 0)
    dims = ""
    stored = str(row.get("stored_path") or "")
    if stored:
        try:
            p = Path(stored)
            if p.is_file():
                head = p.read_bytes()
                wh = _image_dimensions(head)
                if wh:
                    dims = f", {wh[0]}x{wh[1]} px"
        except Exception:  # noqa: BLE001
            dims = ""
    return f"format {suf.upper()}{dims}, rozmiar {_human_size(size)}"


def build_attachment_prompt_block(
    *,
    user_id: str,
    session_id: str,
    file_ids: list[str],
) -> tuple[str, dict[str, Any]]:
    """Build ATTACHMENTS_CONTEXT for system prompt + meta for trace / UI."""
    raw_ids = [str(x).strip() for x in (file_ids or []) if str(x).strip()]
    if len(raw_ids) > MAX_FILES_PER_TURN:
        truncated_ids = True
        use_ids = raw_ids[:MAX_FILES_PER_TURN]
    else:
        truncated_ids = False
        use_ids = raw_ids

    if not use_ids:
        return "", {"files": [], "truncated_ids": truncated_ids}

    rows = fetch_files_for_ids(user_id=user_id, session_id=session_id, file_ids=use_ids)
    by_id = {r["file_id"]: r for r in rows}

    parts: list[str] = []
    meta_files: list[dict[str, Any]] = []
    budget = MAX_TOTAL_CONTEXT_CHARS
    missing = [fid for fid in use_ids if fid not in by_id]
    primary_file_id: str | None = use_ids[-1] if use_ids else None

    for fid in use_ids:
        row = by_id.get(fid)
        if not row:
            meta_files.append(
                {
                    "file_id": fid,
                    "name": "?",
                    "ok": False,
                    "kind": "unknown",
                    "error": "missing_on_server",
                }
            )
            parts.append(
                f"--- Załącznik id={fid} [BŁĄD]\n"
                f"Nie znaleziono pliku dla tej sesji. Nie zgaduj treści."
            )
            continue
        name = row["original_filename"]
        kind = file_kind_for_row(row)
        st = row["extract_status"]

        if st != "ok":
            err = row["extract_error"] or st
            meta_files.append(
                {
                    "file_id": fid,
                    "name": name,
                    "ok": False,
                    "kind": kind,
                    "error": err,
                }
            )
            if st == "image":
                from aihub.config import CHAT_VISION_ENABLED

                meta_data = image_basic_metadata(row)
                if not CHAT_VISION_ENABLED:
                    # Honest, specific: no provider wired at all — not a generic "no access".
                    reason = (
                        "VISION_PROVIDER_MISSING: ta instalacja nie ma skonfigurowanego providera "
                        "ani modelu wizji, więc treść wizualna obrazu nie została odczytana."
                    )
                elif err and err != "image":
                    # Vision is enabled but the provider call failed — a wiring/availability problem,
                    # NOT a missing feature. Surface the concrete error code, do not pretend.
                    reason = (
                        "Vision jest włączony, ale provider wizji nie zwrócił opisu "
                        f"(błąd backendu: {err}). To problem konfiguracji/dostępności providera, "
                        "nie brak funkcji."
                    )
                else:
                    reason = (
                        "Opis wizualny tego obrazu nie jest jeszcze gotowy (przetwarzanie w toku)."
                    )
                parts.append(
                    f"--- Obraz: {name} (id={fid}, typ=image)\n"
                    f"Metadane pliku: {meta_data}.\n"
                    f"{reason}\n"
                    "Nie opisuj zawartości obrazu z wyobraźni. Podaj użytkownikowi konkretnie powyższy "
                    "status i metadane; jeśli potrzebny jest opis treści obrazu, powiedz wprost, że "
                    "wymaga to działającego providera wizji."
                )
            else:
                parts.append(
                    f"--- Plik: {name} (id={fid}, typ={kind})\n"
                    f"[BŁĄD ODCZYTU] {err}\n"
                    f"Nie zgaduj treści tego pliku ani nie „dopowiadaj” z pamięci modelu."
                )
            continue

        body = (row["extracted_text"] or "").strip()
        truncated = False
        if len(body) > MAX_SINGLE_FILE_CHARS:
            body = body[:MAX_SINGLE_FILE_CHARS]
            truncated = True
        if len(body) > budget:
            body = body[:budget]
            truncated = True
        budget -= len(body)
        if budget < 0:
            break
        header = f"--- Plik: {name} (id={fid}, typ={kind})" + (
            " [ucięto]" if truncated else ""
        )
        parts.append(f"{header}\n{body}")
        meta_files.append(
            {
                "file_id": fid,
                "name": name,
                "ok": True,
                "kind": kind,
                "chars": len(body),
                "truncated": truncated,
            }
        )
        if budget <= 0:
            break

    block = "\n\n".join(parts)
    if block:
        block = (
            "=== ATTACHMENTS_CONTEXT (źródło prawdy dla tej tury) ===\n"
            "Kolejność = kolejność dołączenia w żądaniu; OSTATNI na liście = najświeższy aktywny.\n"
            "Gdy użytkownik pisze „plik”, „załącznik”, „ten obraz”, „to zdjęcie”, „ten dokument”, "
            "„to co dołączyłem” — domyślnie chodzi o "
            f"OSTATNI załącznik (id={primary_file_id}), chyba że wskaże inny plik po nazwie.\n"
            "Odpowiadaj wyłącznie na podstawie poniższej treści załączników; "
            "nie wymyślaj fragmentów spoza niej. Jeśli treści brak lub jest błąd odczytu — "
            "powiedz to wprost, bez „może chodziło o…”.\n\n" + block
        )

    usable = sum(1 for f in meta_files if f.get("ok"))
    return block, {
        "files": meta_files,
        "truncated_ids": truncated_ids,
        "missing_ids": missing,
        "primary_file_id": primary_file_id,
        "total_context_chars": sum(
            int(f.get("chars") or 0) for f in meta_files if f.get("ok")
        ),
        "attachments_usable_count": usable,
    }


def summarize_attachments_for_user(meta: dict[str, Any]) -> str | None:
    files = meta.get("files") or []
    ok = [f for f in files if f.get("ok")]
    if not ok:
        if files:
            return "Załączniki: brak treści do modelu (błąd / obraz bez vision)"
        return None
    names = [str(f.get("name") or "?") for f in ok][:4]
    extra = len(ok) - len(names)
    tail = f" +{extra}" if extra > 0 else ""
    n = len(ok)
    kinds = [f.get("kind") for f in ok]
    any_img = any(k == "image" for k in kinds)
    if n == 1:
        label = "Obraz" if any_img else "Plik"
        return f"{label}: {names[0]}{tail}"
    img_n = sum(1 for k in kinds if k == "image")
    if img_n == n:
        return f"{n} obrazy: {', '.join(names)}{tail}"
    if img_n > 0:
        return f"{n} załączniki (w tym obrazy): {', '.join(names)}{tail}"
    return f"{n} pliki: {', '.join(names)}{tail}"
