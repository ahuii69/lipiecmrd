"""LEGACY schema for aihub.db.database — see that module's docstring. Not the canonical schema."""

import time
from aihub.db.database import get_conn


SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS memory_items (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  layer         TEXT NOT NULL,             -- 'stm' | 'ltm' | 'psyche' | 'web'
  text          TEXT NOT NULL,
  text_hash     TEXT NOT NULL,
  meta_json     TEXT NOT NULL DEFAULT '{}',
  importance    REAL NOT NULL DEFAULT 0.5, -- 0..1
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  last_access   INTEGER NOT NULL,
  access_count  INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_text_hash ON memory_items(text_hash);

CREATE TABLE IF NOT EXISTS embeddings (
  item_id       INTEGER PRIMARY KEY,
  dim           INTEGER NOT NULL,
  vec_f32       BLOB NOT NULL,
  updated_at    INTEGER NOT NULL,
  FOREIGN KEY(item_id) REFERENCES memory_items(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts
USING fts5(text, content='memory_items', content_rowid='id', tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory_items BEGIN
  INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS memory_au AFTER UPDATE OF text ON memory_items BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
  INSERT INTO memory_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory_items BEGIN
  INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.id, old.text);
END;

CREATE TABLE IF NOT EXISTS psyche_state (
  key        TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  ts         INTEGER NOT NULL,
  actor      TEXT NOT NULL,
  action     TEXT NOT NULL,
  details    TEXT NOT NULL
);
"""


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def now_ts() -> int:
    return int(time.time())
