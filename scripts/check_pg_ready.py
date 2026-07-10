#!/usr/bin/env python3
"""Szybki preflight: gdy DB_BACKEND=postgres — POSTGRES_DSN, psycopg2, połączenie.

Uruchom z katalogu repo:  python3 scripts/check_pg_ready.py [--soft]

Exit 0 = OK (lub --soft i błąd → tylko ostrzeżenie). Bez --soft: błąd = exit 1.
"""
from __future__ import annotations

import argparse
import os
import sys


def _load_root_dotenv() -> None:
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        root = Path(__file__).resolve().parents[1]
        load_dotenv(root / ".env")
    except Exception as exc:  # noqa: BLE001
        if os.getenv("AIHUB_DEBUG_ENV_LOAD") == "1":
            print(f"check_pg_ready: dotenv load skipped: {exc}", file=sys.stderr)


def run_check(*, soft: bool) -> int:
    backend = (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip()
    if backend != "postgres":
        print(f"check_pg_ready: DB_BACKEND={backend!r} — pomijam (OK)")
        return 0

    def fail(msg: str) -> int:
        if soft:
            print(f"check_pg_ready: OSTRZEŻENIE (soft): {msg}", file=sys.stderr)
            return 0
        print(msg, file=sys.stderr)
        return 1

    dsn = (os.getenv("POSTGRES_DSN") or "").strip()
    if not dsn:
        return fail("check_pg_ready: BŁĄD — DB_BACKEND=postgres ale POSTGRES_DSN jest puste")

    try:
        import psycopg2
    except ImportError:
        return fail(
            "check_pg_ready: BŁĄD — brak psycopg2-binary (pip install psycopg2-binary)"
        )

    try:
        conn = psycopg2.connect(dsn, connect_timeout=8)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        return fail(f"check_pg_ready: BŁĄD połączenia: {e}")

    print("check_pg_ready: OK (PostgreSQL odpowiada)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--soft",
        action="store_true",
        help="Nie kończ exit 1 przy błędzie (tylko stderr) — np. dev_gate bez działającego Dockera",
    )
    args = p.parse_args()
    _load_root_dotenv()
    return run_check(soft=args.soft)


if __name__ == "__main__":
    raise SystemExit(main())
