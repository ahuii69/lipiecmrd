"""Eksperymentalny moduł klastrowania pamięci na osobnym pliku SQLite.

Nie jest montowany w ``aihub.main``. Przy ``DB_BACKEND=postgres`` używaj
kanonicznej warstwy pamięci (``aihub.memory`` / ``legacy_ui``), nie tej klasy.
"""
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional, Tuple


class MemoryIntel:
    def __init__(self, settings):
        self.settings = settings
        os.makedirs(self.settings.AIHUB_DATA_DIR, exist_ok=True)
        self.db_path = getattr(self.settings, "AIHUB_DB_PATH", os.path.join(self.settings.AIHUB_DATA_DIR, "aihub.db"))
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn

    def _init_db(self) -> None:
        with self.lock:
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_items (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    meta_json TEXT,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL,
                    access_count INTEGER NOT NULL DEFAULT 0,
                    last_access_ts INTEGER NOT NULL DEFAULT 0,
                    importance REAL NOT NULL DEFAULT 0.0,
                    centroid_id TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory_items(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_updated ON memory_items(updated_ts DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_centroid ON memory_items(centroid_id)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_centroids (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    fp TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_ts INTEGER NOT NULL,
                    updated_ts INTEGER NOT NULL,
                    size INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_centroids_kind ON memory_centroids(kind)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_centroids_updated ON memory_centroids(updated_ts DESC)")
            conn.commit()
            conn.close()

    def _fingerprint(self, text: str) -> str:
        h = hashlib.sha256(text.strip().lower().encode("utf-8", errors="replace")).hexdigest()
        return h

    def upsert_item(self, kind: str, text: str, meta: Optional[Dict[str, Any]] = None) -> str:
        now = int(time.time())
        item_id = hashlib.sha256((kind + "\n" + text).encode("utf-8", errors="replace")).hexdigest()
        meta_json = json.dumps(meta or {}, ensure_ascii=False)

        with self.lock:
            conn = self._connect()
            row = conn.execute("SELECT id FROM memory_items WHERE id=?", (item_id,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE memory_items SET text=?, meta_json=?, updated_ts=? WHERE id=?",
                    (text, meta_json, now, item_id),
                )
            else:
                conn.execute(
                    "INSERT INTO memory_items (id, kind, text, meta_json, created_ts, updated_ts) VALUES (?, ?, ?, ?, ?, ?)",
                    (item_id, kind, text, meta_json, now, now),
                )
            conn.commit()
            conn.close()

        return item_id

    def touch_access(self, item_id: str) -> None:
        now = int(time.time())
        with self.lock:
            conn = self._connect()
            conn.execute(
                "UPDATE memory_items SET access_count=access_count+1, last_access_ts=? WHERE id=?",
                (now, item_id),
            )
            conn.commit()
            conn.close()

    def score_importance(self, item_id: str) -> float:
        with self.lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT created_ts, updated_ts, access_count, last_access_ts, text FROM memory_items WHERE id=?",
                (item_id,),
            ).fetchone()
            conn.close()
        if not row:
            return 0.0

        created_ts, updated_ts, access_count, last_access_ts, text = row
        now = time.time()

        age_days = max(0.0, (now - float(created_ts)) / 86400.0)
        recency_days = max(0.0, (now - float(updated_ts)) / 86400.0)
        access = max(0, int(access_count or 0))

        # novelty: “rzadko spotykane” znaki/hashe -> proxy
        fp = self._fingerprint(text)
        novelty = (int(fp[:8], 16) % 1000) / 1000.0

        # kontradykcje: prosta heurystyka (nie LLM): “tak/nie”, “jest/nie jest”, “można/nie można” w jednym tekście
        t = text.lower()
        contradiction = 1.0 if ((" nie " in t) and (" tak " in t or " jest " in t)) else 0.0

        # access pattern: logarytmicznie
        access_score = math.log1p(access) / 5.0

        # decay: starsze mniej ważne
        decay = 1.0 / (1.0 + 0.15 * age_days)

        # recency boost
        recency_boost = 1.0 / (1.0 + 0.8 * recency_days)

        importance = (0.25 + 0.35 * access_score + 0.25 * novelty + 0.15 * contradiction) * decay * (0.6 + 0.4 * recency_boost)

        with self.lock:
            conn = self._connect()
            conn.execute("UPDATE memory_items SET importance=?, updated_ts=? WHERE id=?", (float(importance), int(now), item_id))
            conn.commit()
            conn.close()

        return float(importance)

    def centroid_consolidate(self, kind: str, max_items: int = 2000, target_centroids: int = 64) -> Dict[str, Any]:
        """
        Bez embeddings: centroidy robimy po fingerprint bucketach.
        To działa lepiej niż “cosine threshold”, bo nie udaje semantyki, tylko robi stabilne klastry tekstów.
        """
        kind = (kind or "").strip() or "default"
        max_items = max(50, min(int(max_items), 20000))
        target_centroids = max(8, min(int(target_centroids), 512))

        with self.lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, text FROM memory_items WHERE kind=? ORDER BY updated_ts DESC LIMIT ?",
                (kind, max_items),
            ).fetchall()
            conn.close()

        buckets: Dict[str, List[Tuple[str, str]]] = {}
        for item_id, text in rows:
            fp = self._fingerprint(text)
            bucket = fp[:4]  # 16^4 = 65536, stabilne bucketowanie
            buckets.setdefault(bucket, []).append((item_id, text))

        # wybierz największe bucket-y jako “centroidy”
        sorted_b = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
        chosen = sorted_b[:target_centroids]

        now = int(time.time())
        created = 0
        updated = 0
        assigned = 0

        for bucket, items in chosen:
            centroid_id = hashlib.sha256((kind + ":" + bucket).encode("utf-8")).hexdigest()
            summary = items[0][1][:4000]  # twardy limit
            fp = bucket

            with self.lock:
                conn = self._connect()
                row = conn.execute("SELECT id FROM memory_centroids WHERE id=?", (centroid_id,)).fetchone()
                if row:
                    conn.execute(
                        "UPDATE memory_centroids SET summary=?, updated_ts=?, size=? WHERE id=?",
                        (summary, now, len(items), centroid_id),
                    )
                    updated += 1
                else:
                    conn.execute(
                        "INSERT INTO memory_centroids (id, kind, fp, summary, created_ts, updated_ts, size) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (centroid_id, kind, fp, summary, now, now, len(items)),
                    )
                    created += 1

                # assign centroid_id
                for item_id, _ in items:
                    conn.execute("UPDATE memory_items SET centroid_id=? WHERE id=?", (centroid_id, item_id))
                    assigned += 1

                conn.commit()
                conn.close()

        return {"kind": kind, "centroids_created": created, "centroids_updated": updated, "items_assigned": assigned, "buckets_seen": len(buckets)}

    def dedupe_exact(self, kind: str, limit: int = 5000) -> Dict[str, Any]:
        """
        Dedupe bez semantyki: identyczny fingerprint tekstu = ten sam wpis.
        """
        kind = (kind or "").strip() or "default"
        limit = max(100, min(int(limit), 50000))

        with self.lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT id, text FROM memory_items WHERE kind=? ORDER BY created_ts DESC LIMIT ?",
                (kind, limit),
            ).fetchall()

            seen: Dict[str, str] = {}
            deleted = 0

            for item_id, text in rows:
                fp = self._fingerprint(text)
                if fp in seen:
                    conn.execute("DELETE FROM memory_items WHERE id=?", (item_id,))
                    deleted += 1
                else:
                    seen[fp] = item_id

            conn.commit()
            conn.close()

        return {"kind": kind, "scanned": len(rows), "deleted": deleted, "unique": len(seen)}
