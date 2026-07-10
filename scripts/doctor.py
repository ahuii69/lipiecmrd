#!/usr/bin/env python3
"""AI-Hub preflight/doctor.

Runs before startup to fail fast on broken env, missing dependencies, DB problems,
module import errors, or HTTP surface collisions. It never prints secret values.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import socket
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from scripts.dotenv_tool import parse_dotenv, cmd_write_cockpit
except Exception:  # pragma: no cover - direct execution fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.dotenv_tool import parse_dotenv, cmd_write_cockpit  # type: ignore

SECRET_KEYS = {
    "API_KEY",
    "AIHUB_TOKEN_SECRET",
    "LLM_API_KEY",
    "DEEPINFRA_API_KEY",
    "VOYAGE_API_KEY",
    "BRAVE_API_KEY",
    "POSTGRES_DSN",
    "OLLAMA_API_KEY",
    "CHAT_STT_API_KEY",
    "CHAT_VISION_API_KEY",
}
REQUIRED_NONEMPTY = ["API_KEY", "AIHUB_TOKEN_SECRET"]
PLACEHOLDER_TOKENS = ("placeholder", "changeme", "change_me", "todo", "insert", "replace_me", "example-key")


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class Doctor:
    def __init__(self, repo: Path, env_file: Path, *, strict: bool, json_output: bool, profile: str = "") -> None:
        self.repo = repo.resolve()
        self.env_file = env_file.resolve()
        self.strict = strict
        self.json_output = json_output
        self.results: list[CheckResult] = []
        self.profile = profile
        self.env = parse_dotenv(self.env_file)
        self._apply_profile_overrides()

    def _apply_profile_overrides(self) -> None:
        if self.profile == "local":
            self.env["ENV"] = self.env.get("ENV") or "development"
            self.env["DB_BACKEND"] = "sqlite"
            self.env["DATA_DIR"] = self.env.get("DATA_DIR") or "data"
            self.env["DB_PATH"] = self.env.get("DB_PATH") or "data/aihub.sqlite3"
            self.env["FS_ROOT"] = self.env.get("FS_ROOT") or "data/fs"
            self.env["SNAPSHOT_DIR"] = self.env.get("SNAPSHOT_DIR") or "data/snapshots"
            # Local changes storage only.  It MUST NOT replace real embedding
            # providers with fake/offline vectors.  If .env says Voyage, local
            # still uses Voyage and fails fast when it cannot embed.
            self.env["AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK"] = "0"
            self.env["AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK"] = "0"
            self.env["AIHUB_DISABLE_REMOTE_EMBEDDINGS"] = self.env.get("AIHUB_DISABLE_REMOTE_EMBEDDINGS", "0")
        elif self.profile == "prod":
            self.env["ENV"] = self.env.get("ENV") or "production"
        elif self.profile:
            self.fail("profile.mode", f"unsupported profile: {self.profile}")

    def add(self, name: str, status: str, detail: str = "", **meta: Any) -> None:
        self.results.append(CheckResult(name=name, status=status, detail=detail, meta=meta))

    def fail(self, name: str, detail: str = "", **meta: Any) -> None:
        self.add(name, "fail", detail, **meta)

    def warn(self, name: str, detail: str = "", **meta: Any) -> None:
        self.add(name, "warn", detail, **meta)

    def ok(self, name: str, detail: str = "", **meta: Any) -> None:
        self.add(name, "ok", detail, **meta)

    def _masked_env_presence(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in sorted(SECRET_KEYS):
            val = (self.env.get(key) or "").strip()
            out[key] = "set" if val else "empty"
        return out

    def check_env_file(self) -> None:
        if not self.env_file.is_file():
            self.fail("env.file", f"missing {self.env_file}")
            return
        self.ok("env.file", str(self.env_file.relative_to(self.repo) if self.env_file.is_relative_to(self.repo) else self.env_file))
        missing = [k for k in REQUIRED_NONEMPTY if not (self.env.get(k) or "").strip()]
        if missing:
            self.fail("env.required", "missing required non-empty env keys", missing=missing)
        else:
            self.ok("env.required", "required secret keys are present", keys=REQUIRED_NONEMPTY)
        bad: list[str] = []
        for key, val in self.env.items():
            low = val.strip().lower()
            if key in SECRET_KEYS and low and any(tok in low for tok in PLACEHOLDER_TOKENS):
                bad.append(key)
        if bad:
            self.fail("env.placeholders", "secret-looking env values contain placeholder tokens", keys=bad)
        else:
            self.ok("env.placeholders", "no placeholder tokens in configured secret keys")
        self.ok("env.secret_presence", "secret values masked", presence=self._masked_env_presence())
        if self.profile:
            self.ok("env.profile", self.profile, db_backend=self.env.get("DB_BACKEND"), env=self.env.get("ENV"))

    def check_paths(self) -> None:
        for key, default in [
            ("DATA_DIR", "data"),
            ("LOG_DIR", "logs"),
            ("FS_ROOT", "data/fs"),
            ("SNAPSHOT_DIR", "data/snapshots"),
        ]:
            raw = self.env.get(key, default)
            p = Path(raw)
            if not p.is_absolute():
                p = self.repo / p
            try:
                p.mkdir(parents=True, exist_ok=True)
                probe = p / ".doctor_write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
                self.ok(f"path.{key}", "writable", path=str(p))
            except Exception as exc:  # noqa: BLE001
                self.fail(f"path.{key}", str(exc), path=str(p))

    def check_deps(self) -> None:
        required = ["fastapi", "uvicorn", "pydantic", "numpy", "httpx"]
        for mod in required:
            try:
                importlib.import_module(mod)
                self.ok(f"dep.{mod}", "import ok")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"dep.{mod}", str(exc))
        db_backend = (self.env.get("DB_BACKEND") or "sqlite").strip().lower()
        if db_backend == "postgres":
            try:
                importlib.import_module("psycopg2")
                self.ok("dep.psycopg2", "required for DB_BACKEND=postgres")
            except Exception as exc:  # noqa: BLE001
                self.fail("dep.psycopg2", "DB_BACKEND=postgres requires psycopg2-binary", error=str(exc))
        for mod, key in [("faiss", "required FAISS vector backend"), ("sentence_transformers", "required semantic local embedding backend")]:
            try:
                importlib.import_module(mod)
                self.ok(f"dep.{mod}", key)
            except Exception as exc:  # noqa: BLE001
                self.fail(
                    f"dep.{mod}",
                    f"{key} unavailable; install requirements.txt before startup",
                    error=str(exc)[:180],
                )
        try:
            import faiss  # type: ignore
            import numpy as _np

            idx = faiss.IndexFlatL2(4)
            idx.add(_np.zeros((1, 4), dtype=_np.float32))
            dist, ids = idx.search(_np.zeros((1, 4), dtype=_np.float32), 1)
            if ids.shape != (1, 1) or int(ids[0][0]) != 0:
                raise RuntimeError("FAISS add/search returned unexpected result")
            self.ok("dep.faiss.probe", "IndexFlatL2 add/search ok")
        except Exception as exc:  # noqa: BLE001
            self.fail("dep.faiss.probe", "FAISS is installed but not operational", error=str(exc)[:300])

    def check_db(self) -> None:
        backend = (self.env.get("DB_BACKEND") or "sqlite").strip().lower()
        if backend not in {"sqlite", "postgres"}:
            self.fail("db.backend", f"unsupported DB_BACKEND={backend!r}")
            return
        self.ok("db.backend", backend)
        if backend == "sqlite":
            raw = self.env.get("DB_PATH") or str(Path(self.env.get("DATA_DIR", "data")) / "aihub.sqlite3")
            p = Path(raw)
            if not p.is_absolute():
                p = self.repo / p
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                import sqlite3

                con = sqlite3.connect(str(p), timeout=5)
                con.execute("SELECT 1")
                con.close()
                self.ok("db.sqlite", "connect ok", path=str(p))
            except Exception as exc:  # noqa: BLE001
                self.fail("db.sqlite", str(exc), path=str(p))
            return
        dsn = (self.env.get("POSTGRES_DSN") or "").strip()
        if not dsn:
            self.fail("db.postgres.dsn", "DB_BACKEND=postgres but POSTGRES_DSN is empty")
            return
        try:
            import psycopg2

            con = psycopg2.connect(dsn, connect_timeout=5)
            cur = con.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            con.close()
            self.ok("db.postgres", "connect/select ok")
        except Exception as exc:  # noqa: BLE001
            self.fail("db.postgres", str(exc)[:800])

    def check_http_target(self, name: str, url: str, *, required: bool = False) -> None:
        if not url:
            if required:
                self.fail(name, "missing URL")
            else:
                self.warn(name, "not configured")
            return
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            self.fail(name, f"invalid URL: {url!r}")
            return
        try:
            with socket.create_connection((host, port), timeout=4):
                self.ok(name, "tcp reachable", host=host, port=port, scheme=parsed.scheme)
        except Exception as exc:  # noqa: BLE001
            status = "fail" if required else "warn"
            self.add(name, status, str(exc), host=host, port=port, scheme=parsed.scheme)

    def check_provider_config(self, *, live_network: bool) -> None:
        provider = (self.env.get("LLM_PROVIDER_NAME") or "").strip().lower()
        llm_key = (self.env.get("LLM_API_KEY") or self.env.get("DEEPINFRA_API_KEY") or "").strip()
        llm_base = (self.env.get("LLM_BASE_URL") or "").strip()
        if provider and llm_key:
            self.ok("llm.config", "LLM provider/key configured", provider=provider, base_url=llm_base)
        else:
            self.fail("llm.config", "LLM provider/key is not fully configured", provider=provider, base_url=llm_base)
        emb_provider = (self.env.get("EMBEDDING_PROVIDER") or "").strip().lower()
        voyage = bool((self.env.get("VOYAGE_API_KEY") or "").strip())
        provider_fallback = (self.env.get("AIHUB_ALLOW_EMBEDDING_PROVIDER_FALLBACK") or "0").strip().lower() in {"1", "true", "yes", "on"}
        deterministic_fallback = (self.env.get("AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK") or "0").strip().lower() in {"1", "true", "yes", "on"}
        numpy_fallback = (self.env.get("AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK") or "0").strip().lower() in {"1", "true", "yes", "on"}
        if emb_provider == "voyage" and not voyage:
            self.fail("embedding.config", "EMBEDDING_PROVIDER=voyage but VOYAGE_API_KEY is empty")
        elif emb_provider not in {"voyage", "sentence-transformers", "auto"}:
            self.fail("embedding.config", f"unsupported EMBEDDING_PROVIDER={emb_provider!r}")
        elif deterministic_fallback:
            self.fail("embedding.config", "AIHUB_DETERMINISTIC_EMBEDDING_FALLBACK must be 0 for real runtime")
        elif numpy_fallback:
            self.fail("embedding.config", "AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK must be 0 for real runtime")
        else:
            self.ok(
                "embedding.config",
                "embedding config present",
                provider=emb_provider,
                voyage_key="set" if voyage else "empty",
                provider_fallback_enabled=provider_fallback,
            )
        if live_network:
            if llm_base:
                self.check_http_target("llm.network", llm_base, required=True)
            # Voyage endpoint is fixed for provider=voyage; just TCP probe API host.
            if emb_provider == "voyage" and voyage:
                self.check_http_target("embedding.network", "https://api.voyageai.com", required=True)
            stt_enabled = (self.env.get("CHAT_STT_ENABLED") or "0").lower() in {"1", "true", "yes", "on"}
            if stt_enabled:
                self.check_http_target("stt.network", self.env.get("CHAT_STT_API_URL", ""), required=False)
            vision_enabled = (self.env.get("CHAT_VISION_ENABLED") or "0").lower() in {"1", "true", "yes", "on"}
            if vision_enabled:
                self.check_http_target("vision.network", self.env.get("CHAT_VISION_OLLAMA_URL", ""), required=False)

    def check_vector_engine_runtime(self) -> None:
        """Verify that vector_engine is using FAISS, not a silent fallback."""
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        old_env = dict(os.environ)
        try:
            os.environ.update(self.env)
            os.environ["AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK"] = self.env.get("AIHUB_ALLOW_NUMPY_VECTOR_FALLBACK", "0")
            from aihub import vector_engine

            vector_engine.clear()
            h = vector_engine.health()
            if not h.get("ok"):
                self.fail("vector.runtime", "vector engine health failed", health=h)
                return
            if h.get("backend") != "faiss":
                self.fail("vector.runtime", "vector engine must use FAISS", health=h)
                return
            add = vector_engine.add_memory("doctor faiss semantic vector probe", user_id="doctor", source="doctor")
            if not add.get("ok"):
                self.fail("vector.add", "vector add failed", result=add)
                return
            sr = vector_engine.search("semantic vector probe", user_id="doctor", k=1)
            if not sr.get("ok"):
                self.fail("vector.search", "vector search failed", result=sr)
                return
            if sr.get("backend") != "faiss":
                self.fail("vector.search", "vector search must use FAISS", result=sr)
                return
            self.ok("vector.runtime", "FAISS vector add/search ok", backend=sr.get("backend"))
        except Exception as exc:  # noqa: BLE001
            self.fail("vector.runtime", str(exc)[:800])
        finally:
            os.environ.clear()
            os.environ.update(old_env)

    def check_imports(self) -> None:
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        os.environ.update(self.env)
        try:
            import aihub
        except Exception as exc:  # noqa: BLE001
            self.fail("imports.aihub", str(exc))
            return
        failures: list[dict[str, str]] = []
        count = 0
        for m in pkgutil.walk_packages(aihub.__path__, aihub.__name__ + "."):
            count += 1
            try:
                importlib.import_module(m.name)
            except Exception as exc:  # noqa: BLE001
                failures.append({"module": m.name, "error": repr(exc)[:600]})
        if failures:
            self.fail("imports.all", f"{len(failures)}/{count} modules failed", failures=failures[:20])
        else:
            self.ok("imports.all", f"{count} modules imported")

    def check_routes(self) -> None:
        if str(self.repo) not in sys.path:
            sys.path.insert(0, str(self.repo))
        os.environ.update(self.env)
        try:
            from aihub.main import app

            seen: set[tuple[tuple[str, ...], str]] = set()
            dupes: list[dict[str, Any]] = []
            for r in app.routes:
                methods = tuple(sorted(getattr(r, "methods", []) or []))
                path = str(getattr(r, "path", ""))
                key = (methods, path)
                if key in seen:
                    dupes.append({"methods": methods, "path": path})
                seen.add(key)
            if dupes:
                self.fail("fastapi.routes", "duplicate method/path routes", dupes=dupes)
            else:
                self.ok("fastapi.routes", f"{len(seen)} routes, no duplicates")
        except Exception as exc:  # noqa: BLE001
            self.fail("fastapi.routes", str(exc)[:800])

    def check_cockpit_env(self, *, sync: bool, base_url: str | None) -> None:
        cockpit = self.repo / "cockpit"
        if not cockpit.is_dir():
            self.warn("cockpit.dir", "cockpit directory missing")
            return
        if sync:
            try:
                effective_base_url = base_url or self.env.get("AIHUB_BASE_URL") or "http://127.0.0.1:8080"

                class Args:
                    repo_root = str(self.repo)
                    env_file = str(self.env_file)
                    out_env = str(cockpit / ".env")
                    base_url = effective_base_url

                cmd_write_cockpit(Args())
                self.ok("cockpit.env.sync", "cockpit/.env regenerated from root .env")
            except Exception as exc:  # noqa: BLE001
                self.fail("cockpit.env.sync", str(exc))
        cp = cockpit / ".env"
        if cp.is_file():
            cenv = parse_dotenv(cp)
            if cenv.get("API_KEY") == self.env.get("API_KEY") and cenv.get("AIHUB_BASE_URL"):
                self.ok("cockpit.env", "present and aligned with root API key")
            else:
                self.fail("cockpit.env", "cockpit/.env missing API_KEY alignment or AIHUB_BASE_URL")
        else:
            self.fail("cockpit.env", "cockpit/.env missing")

    def run(self, args: argparse.Namespace) -> int:
        self.check_env_file()
        self.check_paths()
        self.check_deps()
        if args.check_db:
            self.check_db()
        self.check_provider_config(live_network=args.live_network)
        self.check_vector_engine_runtime()
        if args.sync_cockpit_env:
            self.check_cockpit_env(sync=True, base_url=args.backend_base_url)
        else:
            self.check_cockpit_env(sync=False, base_url=args.backend_base_url)
        if args.check_imports:
            self.check_imports()
        if args.check_routes:
            self.check_routes()
        return self.emit()

    def emit(self) -> int:
        data = {
            "ok": not any(r.status == "fail" for r in self.results),
            "strict_ok": not any(r.status in {"fail", "warn"} for r in self.results),
            "results": [r.__dict__ for r in self.results],
        }
        if self.json_output:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        else:
            icons = {"ok": "OK", "warn": "WARN", "fail": "FAIL"}
            for r in self.results:
                print(f"[{icons.get(r.status, r.status)}] {r.name}: {r.detail}")
                if r.meta and r.status != "ok":
                    print(json.dumps(r.meta, ensure_ascii=False, indent=2))
            print(f"\nRESULT: {'OK' if data['ok'] else 'FAIL'}" + (" (strict)" if self.strict else ""))
        if any(r.status == "fail" for r in self.results):
            return 1
        if self.strict and any(r.status == "warn" for r in self.results):
            return 2
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-Hub environment/runtime doctor")
    parser.add_argument("--repo", default=".", help="Repository root")
    parser.add_argument("--env", default=".env", help="Env file path")
    parser.add_argument("--strict", action="store_true", help="Return non-zero on warnings too")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--check-db", action="store_true", help="Check selected DB live")
    parser.add_argument("--check-imports", action="store_true", help="Import all aihub modules")
    parser.add_argument("--check-routes", action="store_true", help="Import FastAPI app and check route duplicates")
    parser.add_argument("--live-network", action="store_true", help="TCP probe configured external providers")
    parser.add_argument("--sync-cockpit-env", action="store_true", help="Regenerate cockpit/.env from root env")
    parser.add_argument("--backend-base-url", default=None, help="Base URL to write into cockpit env when syncing")
    parser.add_argument("--profile", choices=["", "local", "prod"], default="", help="Runtime profile override used by start.sh")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    env_file = Path(args.env)
    if not env_file.is_absolute():
        env_file = repo / env_file
    return Doctor(repo, env_file, strict=args.strict, json_output=args.json, profile=args.profile).run(args)


if __name__ == "__main__":
    raise SystemExit(main())
