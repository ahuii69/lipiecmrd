#!/usr/bin/env python3
"""Import danych z plików SQLite do PostgreSQL (idempotentny, bez kasowania SQLite).

- ``INSERT ... ON CONFLICT ... DO NOTHING`` — ponowne uruchomienie nie duplikuje PK.
- Log w ``sidecar.sqlite_import_log`` (jeśli tabela istnieje po bootstrapie).
- Nie usuwa ani nie obcina plików źródłowych.

Zmienne środowiskowe:

- ``AIHUB_SQLITE_IMPORT``: ``off`` | ``sidecar`` | ``full`` | ``auto``.
  ``auto``: pierwszy start (brak checkpointu w PG) = pełny import ``public.*``; kolejne = tylko sidecar/compat.
- ``AIHUB_SQLITE_REIMPORT``: ``1`` — usuń checkpoint i wymuś ponowny pełny import (przy ``auto``).
- ``AIHUB_SQLITE_IMPORT_BATCH``: rozmiar batcha (domyślnie ``500``).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_SKIP_SQLITE_TABLES = frozenset(
    {
        "sqlite_sequence",
    }
)


def _sqlite_table_skip(name: str, create_sql: str | None) -> bool:
    ln = name.lower()
    if ln in _SKIP_SQLITE_TABLES or ln.startswith("sqlite_"):
        return True
    for suf in (
        "_fts_data",
        "_fts_idx",
        "_fts_docsize",
        "_fts_config",
        "_fts_content",
    ):
        if ln.endswith(suf):
            return True
    if create_sql and "VIRTUAL TABLE" in create_sql.upper() and ln.endswith("_fts"):
        if ln != "memory_fts":
            return True
    return False


_CHECKPOINT_TABLE = "__full_public_done__"


def _import_mode() -> str:
    raw = (os.getenv("AIHUB_SQLITE_IMPORT") or "auto").strip().lower()
    if raw in ("0", "false", "no", "off", "none"):
        return "off"
    if raw == "sidecar":
        return "sidecar"
    if raw in ("full",):
        return "full"
    if raw in ("", "auto", "1", "true", "yes", "on"):
        return "auto"
    return "full"


def _full_import_checkpoint_exists(pg: Any) -> bool:
    try:
        cur = pg.cursor()
        cur.execute(
            """
            SELECT 1 FROM sidecar.sqlite_import_log
            WHERE phase = 'checkpoint' AND target_table = %s
            LIMIT 1
            """,
            (_CHECKPOINT_TABLE,),
        )
        return cur.fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def _set_full_import_checkpoint(pg: Any) -> None:
    if _full_import_checkpoint_exists(pg):
        return
    try:
        cur = pg.cursor()
        cur.execute(
            """
            INSERT INTO sidecar.sqlite_import_log(
                phase, source_path, target_table, rows_inserted, detail
            ) VALUES ('checkpoint', '', %s, 1, 'Pełny import public zakończony — auto użyje sidecar')
            """,
            (_CHECKPOINT_TABLE,),
        )
        pg.commit()
    except Exception as e:  # noqa: BLE001
        logger.debug("checkpoint: %s", e)


def _batch_size() -> int:
    try:
        return max(50, min(5000, int(os.getenv("AIHUB_SQLITE_IMPORT_BATCH", "500"))))
    except ValueError:
        return 500


def _pg_connect() -> Any:
    import psycopg2

    dsn = (os.getenv("POSTGRES_DSN") or "").strip()
    if not dsn:
        raise RuntimeError("POSTGRES_DSN jest pusty")
    return psycopg2.connect(dsn)


def _log_pg(
    cur: Any,
    phase: str,
    source: str,
    target: str,
    inserted: int,
    skipped: int,
    detail: str = "",
) -> None:
    try:
        cur.execute(
            """
            INSERT INTO sidecar.sqlite_import_log(
                phase, source_path, target_table, rows_inserted, rows_skipped, detail
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (phase, source[:2048], target[:512], inserted, skipped, detail[:4000]),
        )
    except Exception as e:  # noqa: BLE001
        logger.debug("sqlite_import_log: %s", e)


def _pg_insertable_columns(cur: Any, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
          AND (is_generated IS NULL OR is_generated = 'NEVER')
        ORDER BY ordinal_position
        """,
        (schema, table),
    )
    return [r[0] for r in cur.fetchall()]


def _pg_primary_key_columns(cur: Any, schema: str, table: str) -> list[str]:
    cur.execute(
        """
        SELECT kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_schema = kcu.constraint_schema
         AND tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.table_schema = %s AND tc.table_name = %s
          AND tc.constraint_type = 'PRIMARY KEY'
        ORDER BY kcu.ordinal_position
        """,
        (schema, table),
    )
    return [r[0] for r in cur.fetchall()]


def _sqlite_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    cur = conn.execute(f'PRAGMA table_info("{table}")')
    return {str(r[1]): str(r[2]) for r in cur.fetchall()}


def _flush_http_events_batch(
    pg: Any, cols: list[str], rows: list[tuple[Any, ...]]
) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_batch

    id_idx = cols.index("id") if "id" in cols else None
    fixed: list[tuple[Any, ...]] = []
    for row in rows:
        r = list(row)
        if id_idx is not None and (r[id_idx] is None or r[id_idx] == ""):
            r[id_idx] = str(uuid.uuid4())
        fixed.append(tuple(r))

    quoted = ", ".join(f'"{c}"' for c in cols)
    ph = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO sidecar.http_events ({quoted}) VALUES ({ph}) "
        f"ON CONFLICT (id) DO NOTHING"
    )
    cur = pg.cursor()
    execute_batch(cur, sql, fixed, page_size=min(len(fixed), _batch_size()))


def _import_http_events(pg: Any, sqlite_path: Path, label: str) -> tuple[int, int]:
    if not sqlite_path.is_file():
        return 0, 0
    ins = 0
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        n = int(sconn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
        if not n:
            return 0, 0
        cur_s = sconn.execute("SELECT * FROM events")
        cols = [d[0] for d in cur_s.description]
        bsz = _batch_size()
        batch: list[tuple[Any, ...]] = []
        for row in cur_s:
            batch.append(tuple(row))
            if len(batch) >= bsz:
                _flush_http_events_batch(pg, cols, batch)
                ins += len(batch)
                batch.clear()
                pg.commit()
        if batch:
            _flush_http_events_batch(pg, cols, batch)
            ins += len(batch)
            pg.commit()
    finally:
        sconn.close()
    _log_pg(pg.cursor(), "http_events", str(sqlite_path), "sidecar.http_events", ins, 0, label)
    pg.commit()
    logger.info(
        "sqlite_pg_import: %s sidecar.http_events przetworzono ~%d wierszy (ON CONFLICT może pominąć duplikaty)",
        label,
        ins,
    )
    return ins, 0


def _import_psyche_rules(pg: Any, sqlite_path: Path) -> tuple[int, int]:
    if not sqlite_path.is_file():
        return 0, 0
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        has = sconn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='rules'"
        ).fetchone()
        if not has:
            return 0, 0
        rows = sconn.execute("SELECT id, ts, kind, pattern, weight FROM rules").fetchall()
    finally:
        sconn.close()
    from psycopg2.extras import execute_batch

    ins = 0
    cur = pg.cursor()
    sql = """
        INSERT INTO sidecar.psyche_rules(id, ts, kind, pattern, weight)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT (id) DO NOTHING
        """
    execute_batch(cur, sql, rows, page_size=500)
    ins = len(rows)
    _log_pg(pg.cursor(), "psyche_rules", str(sqlite_path), "sidecar.psyche_rules", ins, 0, "")
    pg.commit()
    logger.info("sqlite_pg_import: psyche_rules przetworzono %d wierszy", ins)
    return ins, 0


def _import_healed(pg: Any, sqlite_path: Path) -> tuple[int, int]:
    if not sqlite_path.is_file():
        return 0, 0
    sconn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        has = sconn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='healed'"
        ).fetchone()
        if not has:
            return 0, 0
        rows = sconn.execute(
            "SELECT path, backup_path, snapshot, ts FROM healed ORDER BY id"
        ).fetchall()
    finally:
        sconn.close()
    cur = pg.cursor()
    ins = 0
    sql = """
        INSERT INTO sidecar.healed(path, backup_path, snapshot, ts)
        SELECT %s,%s,%s,%s
        WHERE NOT EXISTS (
            SELECT 1 FROM sidecar.healed h
            WHERE h.path = %s AND h.backup_path = %s AND h.ts = %s
        )
        """
    for path, backup_path, snapshot, ts in rows:
        cur.execute(sql, (path, backup_path, snapshot, ts, path, backup_path, ts))
        ins += int(cur.rowcount > 0)
    _log_pg(pg.cursor(), "healed", str(sqlite_path), "sidecar.healed", ins, 0, "")
    pg.commit()
    logger.info("sqlite_pg_import: healed przetworzono %d wierszy", ins)
    return ins, 0


def _import_compat_router(pg: Any, sconn: sqlite3.Connection) -> None:
    curp = pg.cursor()
    for table, sql_table, cols, q in (
        (
            "memory",
            "compat_router.mem",
            "id, ts, key, text, meta_json, access_count, last_access_ts, importance, deleted",
            """
                INSERT INTO compat_router.mem(
                    id, ts, key, text, meta_json, access_count, last_access_ts, importance, deleted
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (id) DO NOTHING
                """,
        ),
        (
            "policy",
            "compat_router.policy",
            "key, value, confidence, ts",
            """
                INSERT INTO compat_router.policy(key, value, confidence, ts)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (key) DO NOTHING
                """,
        ),
    ):
        has = sconn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if not has:
            continue
        n = sconn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not n:
            continue
        rows = sconn.execute(f"SELECT {cols} FROM {table}").fetchall()
        ins = 0
        for tup in rows:
            curp.execute(q, tup)
            ins += int(curp.rowcount > 0)
        _log_pg(curp, "compat_router", "main_sqlite", sql_table, ins, 0, table)
        logger.info("sqlite_pg_import: %s wstawiono ~%d", sql_table, ins)
    pg.commit()


def _import_main_table_events(pg: Any, sconn: sqlite3.Connection) -> None:
    has = sconn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'"
    ).fetchone()
    if not has:
        return
    n = int(sconn.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    if not n:
        return
    cur_s = sconn.execute("SELECT * FROM events")
    cols = [d[0] for d in cur_s.description]
    bsz = _batch_size()
    batch: list[tuple[Any, ...]] = []
    ins = 0
    for row in cur_s:
        batch.append(tuple(row))
        if len(batch) >= bsz:
            _flush_http_events_batch(pg, cols, batch)
            ins += len(batch)
            batch.clear()
            pg.commit()
    if batch:
        _flush_http_events_batch(pg, cols, batch)
        ins += len(batch)
        pg.commit()
    _log_pg(
        pg.cursor(),
        "http_events",
        "main_sqlite.events",
        "sidecar.http_events",
        ins,
        0,
        "merge z głównej bazy SQLite",
    )
    pg.commit()
    logger.info(
        "sqlite_pg_import: main.events -> sidecar.http_events przetworzono ~%d wierszy",
        ins,
    )


def _import_public_table(
    pg: Any,
    sconn: sqlite3.Connection,
    table: str,
) -> tuple[int, int]:
    schema = "public"
    cur_pg = pg.cursor()
    pg_cols = _pg_insertable_columns(cur_pg, schema, table)
    if not pg_cols:
        return 0, 0
    sq_cols_map = _sqlite_columns(sconn, table)
    common = [c for c in pg_cols if c in sq_cols_map]
    if not common:
        return 0, 0
    pk_cols = _pg_primary_key_columns(cur_pg, schema, table)
    if not pk_cols:
        logger.warning("sqlite_pg_import: brak PK dla %s — pomijam", table)
        return 0, 0
    for c in pk_cols:
        if c not in common:
            logger.warning(
                "sqlite_pg_import: PK kolumna %s nie w wspólnych dla %s — pomijam",
                c,
                table,
            )
            return 0, 0

    col_list = ", ".join(f'"{c}"' for c in common)
    ph = ", ".join(["%s"] * len(common))
    conflict = ", ".join(f'"{c}"' for c in pk_cols)
    sql = (
        f'INSERT INTO "{schema}"."{table}" ({col_list}) VALUES ({ph}) '
        f"ON CONFLICT ({conflict}) DO NOTHING"
    )

    sel_cols = ", ".join(f'"{c}"' for c in common)
    cur_s = sconn.execute(f"SELECT {sel_cols} FROM \"{table}\"")
    bsz = _batch_size()
    processed = 0
    batch: list[tuple[Any, ...]] = []
    while True:
        chunk = cur_s.fetchmany(bsz)
        if not chunk:
            break
        batch.extend(chunk)
        while len(batch) >= bsz:
            sub = batch[:bsz]
            batch = batch[bsz:]
            for tup in sub:
                cur_pg.execute(sql, tup)
            processed += len(sub)
            pg.commit()
    for tup in batch:
        cur_pg.execute(sql, tup)
        processed += 1
    pg.commit()

    _log_pg(pg.cursor(), "public", "main_sqlite", table, processed, 0, "streaming")
    logger.info("sqlite_pg_import: public.%s przetworzono %d wierszy", table, processed)
    return processed, 0


def _public_tables_to_copy(pg: Any, sconn: sqlite3.Connection) -> list[str]:
    cur_pg = pg.cursor()
    cur_pg.execute(
        """
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    pg_tables = {r[0] for r in cur_pg.fetchall()}
    cur_s = sconn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    out: list[str] = []
    for name, sql in cur_s.fetchall():
        if name not in pg_tables:
            continue
        if _sqlite_table_skip(name, sql):
            continue
        if name in ("memory", "policy", "events"):
            continue
        out.append(name)
    return out


def _import_brain_memory_db(pg: Any, path: Path) -> None:
    """Opcjonalny ``data/memory.db`` (stary brain) → ``compat_router.mem`` (bez kasowania źródła)."""
    if not path.is_file():
        return
    sconn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        has = sconn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='memories'"
        ).fetchone()
        if not has:
            return
        rows = sconn.execute(
            "SELECT uuid, ts, text, importance, access_count, last_access_ts FROM memories"
        ).fetchall()
    finally:
        sconn.close()
    if not rows:
        return
    cur = pg.cursor()
    ins = 0
    for uid, ts, text, imp, ac, la in rows:
        cur.execute(
            """
            INSERT INTO compat_router.mem(
                id, ts, key, text, meta_json, access_count, last_access_ts, importance, deleted
            ) VALUES (%s,%s,NULL,%s,'{}',%s,%s,%s,0)
            ON CONFLICT (id) DO NOTHING
            """,
            (str(uid), int(ts), str(text), int(ac), int(la), float(imp)),
        )
        ins += int(cur.rowcount > 0)
    _log_pg(cur, "brain_memory_db", str(path), "compat_router.mem", ins, 0, "memory.db")
    pg.commit()
    logger.info("sqlite_pg_import: memory.db (brain) → compat_router.mem wstawiono ~%d", ins)


def run_sqlite_import_if_enabled() -> None:
    from aihub.config import DATA_DIR, DB_PATH

    if (os.getenv("DB_BACKEND", "sqlite") or "sqlite").lower().strip() != "postgres":
        return
    mode = _import_mode()
    if mode == "off":
        logger.info("sqlite_pg_import: wyłączone (AIHUB_SQLITE_IMPORT=off)")
        return

    t0 = time.time()
    data_dir = Path(DATA_DIR)
    main_sqlite = Path(DB_PATH)

    try:
        pg = _pg_connect()
    except Exception as e:  # noqa: BLE001
        logger.error("sqlite_pg_import: brak połączenia PG: %s", e)
        return

    try:
        cur = pg.cursor()
        cur.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'sidecar' AND table_name = 'sqlite_import_log'
            """
        )
        if not cur.fetchone():
            logger.warning(
                "sqlite_pg_import: brak sidecar.sqlite_import_log — uruchom bootstrap PG"
            )
    except Exception as e:  # noqa: BLE001
        logger.debug("sqlite_pg_import: check log table: %s", e)

    reimport = (os.getenv("AIHUB_SQLITE_REIMPORT") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if reimport:
        try:
            c = pg.cursor()
            c.execute(
                "DELETE FROM sidecar.sqlite_import_log WHERE phase = 'checkpoint' AND target_table = %s",
                (_CHECKPOINT_TABLE,),
            )
            pg.commit()
            logger.info("sqlite_pg_import: checkpoint usunięty (AIHUB_SQLITE_REIMPORT)")
        except Exception as e:  # noqa: BLE001
            logger.debug("sqlite_pg_import: reimport: %s", e)

    if mode == "auto":
        if _full_import_checkpoint_exists(pg) and not reimport:
            mode = "sidecar"
            logger.info(
                "sqlite_pg_import: auto → sidecar (pełny import już w PG; "
                "AIHUB_SQLITE_REIMPORT=1 lub AIHUB_SQLITE_IMPORT=full aby ponowić)"
            )
        else:
            mode = "full"
            logger.info("sqlite_pg_import: auto → full")

    try:
        events_db = data_dir / "events.db"
        _import_http_events(pg, events_db, "events.db")

        psyche_db = data_dir / "psyche.db"
        _import_psyche_rules(pg, psyche_db)

        self_heal = Path(
            os.getenv("AIHUB_SELF_HEAL_DB", str(data_dir / "self_heal.db"))
        )
        _import_healed(pg, self_heal)
        _import_brain_memory_db(pg, data_dir / "memory.db")

        if not main_sqlite.is_file():
            logger.info("sqlite_pg_import: brak głównego pliku SQLite %s", main_sqlite)
            return

        sconn = sqlite3.connect(f"file:{main_sqlite}?mode=ro", uri=True)
        try:
            _import_compat_router(pg, sconn)
            _import_main_table_events(pg, sconn)
        finally:
            sconn.close()

        if mode == "sidecar":
            logger.info(
                "sqlite_pg_import: tryb sidecar zakończony w %.1fs", time.time() - t0
            )
            return

        sconn = sqlite3.connect(f"file:{main_sqlite}?mode=ro", uri=True)
        try:
            tables = _public_tables_to_copy(pg, sconn)
            logger.info(
                "sqlite_pg_import: import public (%d tabel): %s",
                len(tables),
                ",".join(tables[:40]) + ("..." if len(tables) > 40 else ""),
            )
            public_failed = False
            for tbl in tables:
                try:
                    _import_public_table(pg, sconn, tbl)
                except Exception as e:  # noqa: BLE001
                    public_failed = True
                    logger.error(
                        "sqlite_pg_import: błąd tabeli %s: %s", tbl, e, exc_info=True
                    )
            if not public_failed:
                _set_full_import_checkpoint(pg)
        finally:
            sconn.close()

    finally:
        try:
            pg.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("PostgreSQL connection close failed: %s", exc)

    logger.info("sqlite_pg_import: zakończono w %.1fs", time.time() - t0)


def main() -> None:
    """CLI: ``python -m aihub.sqlite_pg_import`` — import przy ustawionym POSTGRES_DSN."""
    logging.basicConfig(level=logging.INFO)
    os.environ.setdefault("DB_BACKEND", "postgres")
    run_sqlite_import_if_enabled()


if __name__ == "__main__":
    main()
