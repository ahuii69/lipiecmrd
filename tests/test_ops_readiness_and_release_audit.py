from fastapi.testclient import TestClient


def test_ops_ready_and_capabilities_surface(client: TestClient):
    ready = client.get("/ops/ready")
    assert ready.status_code in {200, 503}
    body = ready.json()
    assert "ready" in body
    assert "mandatory_layers" in body
    assert "database" in body["mandatory_layers"]

    caps = client.get("/ops/capabilities")
    assert caps.status_code == 200
    cbody = caps.json()
    assert "capabilities" in cbody
    assert "chat" in cbody["capabilities"]
    assert "memory" in cbody["capabilities"]


def test_release_audit_core_checks_are_green(tmp_path):
    from pathlib import Path

    from scripts.release_audit import exact_duplicates, module_collisions, unfinished_markers

    repo = Path.cwd()
    assert module_collisions(repo) == []
    assert unfinished_markers(repo) == []
    assert exact_duplicates(repo) == []


def test_release_audit_ignores_runtime_upload_artifacts(tmp_path):
    """Byte-identical files under runtime/build/cache artifact trees are runtime coincidences,
    not source duplication, and must NOT be reported by the exact-duplicate check.

    Covers the full runtime-artifact surface: user uploads, scratch/tmp, on-disk cache, sandbox
    fs, snapshots, runtime SQLite DBs (+ WAL sidecar) and the logs/ dir."""
    from scripts.release_audit import exact_duplicates

    same_bytes = b"\x89PNG\r\n\x1a\n fixture image bytes reused across users"

    # Same fixture image uploaded under two different user ids -> identical bytes on disk.
    for sub in ("u_img", "um"):
        d = tmp_path / "data" / "uploads" / sub
        d.mkdir(parents=True)
        (d / "image.png").write_bytes(same_bytes)

    # Duplicates inside other runtime data subtrees must also be ignored.
    for tree in ("tmp", "cache", "fs", "snapshots"):
        for sub in ("a", "b"):
            d = tmp_path / "data" / tree / sub
            d.mkdir(parents=True)
            (d / "artifact.bin").write_bytes(b"runtime-" + tree.encode())

    # Runtime SQLite database + WAL sidecar with identical bytes -> ignored.
    (tmp_path / "data" / "aihub.sqlite3").write_bytes(b"SQLITE-RUNTIME-DB")
    (tmp_path / "data" / "aihub_copy.sqlite3").write_bytes(b"SQLITE-RUNTIME-DB")

    # logs/ is a runtime dir (test/app logs).
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "a.log").write_bytes(b"same log line\n")
    (logs / "b.log").write_bytes(b"same log line\n")

    assert exact_duplicates(tmp_path) == []


def test_release_audit_ignores_uploads_after_real_upload_run(isolated_db, tmp_path, monkeypatch):
    """End-to-end: after the real chat upload service writes files to data/uploads, the release
    audit must still report zero exact-duplicate groups for that tree (regression guard for the
    original failure where uploaded fixtures polluted the audit)."""
    import io

    import aihub.chat_file_service as cfs
    from scripts.release_audit import exact_duplicates

    audit_root = tmp_path / "audit_root"
    data_dir = audit_root / "data"
    (data_dir / "uploads").mkdir(parents=True)
    monkeypatch.setattr(cfs, "DATA_DIR", data_dir)

    same_bytes = b"identical upload bytes reused across two different users\n"
    for uid in ("user_a", "user_b"):
        res = cfs.save_multipart_upload(
            user_id=uid,
            session_id="s1",
            filename="note.txt",
            content_type="text/plain",
            file_obj=io.BytesIO(same_bytes),
        )
        assert res["ok"] is True

    # Sanity: byte-identical files really landed under data/uploads (would be a duplicate group
    # if the audit did not treat this tree as a runtime artifact).
    written = [p for p in (data_dir / "uploads").rglob("*") if p.is_file()]
    assert len(written) == 2
    assert written[0].read_bytes() == written[1].read_bytes()

    assert exact_duplicates(audit_root) == []


def test_release_audit_still_detects_real_code_duplicates(tmp_path):
    """The audit must still catch genuine source duplication (code/docs/config/tests), so the
    runtime-artifact ignore rule cannot be used to smuggle real duplicates past the gate."""
    from scripts.release_audit import exact_duplicates

    dup_source = "def helper():\n    return 42\n"

    pkg = tmp_path / "aihub"
    pkg.mkdir(parents=True)
    (pkg / "mod_a.py").write_text(dup_source, encoding="utf-8")
    (pkg / "mod_b.py").write_text(dup_source, encoding="utf-8")

    # Duplicate across source trees (docs + config) must also be caught.
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text("# Same\ncontent\n", encoding="utf-8")
    cfg = tmp_path / "config"
    cfg.mkdir()
    (cfg / "guide.md").write_text("# Same\ncontent\n", encoding="utf-8")

    groups = exact_duplicates(tmp_path)
    assert ["aihub/mod_a.py", "aihub/mod_b.py"] in groups
    assert ["config/guide.md", "docs/guide.md"] in groups


def test_release_audit_scans_source_file_named_like_runtime_dir(tmp_path):
    """The `logs` skip rule must match only a DIRECTORY literally named `logs` — a SOURCE file
    such as ``aihub/logs.py`` must still be scanned. Guards against accidentally excluding source
    whose name merely contains a skip token."""
    from scripts.release_audit import exact_duplicates

    same = "LOGGER_NAME = 'aihub'\n"
    (tmp_path / "aihub").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "aihub" / "logs.py").write_text(same, encoding="utf-8")
    (tmp_path / "scripts" / "logs.py").write_text(same, encoding="utf-8")

    groups = exact_duplicates(tmp_path)
    assert ["aihub/logs.py", "scripts/logs.py"] in groups


def test_release_audit_detects_duplicates_in_scripts_and_tests(tmp_path):
    """Real duplicates in scripts/ and tests/ source trees are still detected."""
    from scripts.release_audit import exact_duplicates

    dup = "def _shared():\n    return 'dup'\n"
    (tmp_path / "scripts").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "scripts" / "a.py").write_text(dup, encoding="utf-8")
    (tmp_path / "tests" / "b.py").write_text(dup, encoding="utf-8")

    groups = exact_duplicates(tmp_path)
    assert ["scripts/a.py", "tests/b.py"] in groups


def test_release_audit_data_dir_is_not_blanket_ignored(tmp_path):
    """`data/` must ignore ONLY runtime artifacts (uploads/tmp/cache/fs/snapshots + runtime
    SQLite), not the whole tree. A non-artifact file placed directly under data/ is still scanned,
    so a real duplicate there is detected."""
    from scripts.release_audit import exact_duplicates

    same = "# same content\nkey = 1\n"
    (tmp_path / "data").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "data" / "seed.md").write_text(same, encoding="utf-8")
    (tmp_path / "docs" / "seed.md").write_text(same, encoding="utf-8")

    groups = exact_duplicates(tmp_path)
    assert ["data/seed.md", "docs/seed.md"] in groups
