# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import hashlib
import logging
import sqlite3
import time
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from aihub.config import DATA_DIR

log = logging.getLogger("aihub.psyche")

router = APIRouter(prefix="/psyche", tags=["psyche"])

PSYCHE_DB = DATA_DIR / "psyche.db"
EVENTS_DB = DATA_DIR / "events.db"


def ensure_psyche_db():
    PSYCHE_DB.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PSYCHE_DB) as conn:
        conn.execute(
            """
        CREATE TABLE IF NOT EXISTS rules(
            id TEXT PRIMARY KEY,
            ts INTEGER,
            kind TEXT,
            pattern TEXT,
            weight REAL
        )
        """
        )
        conn.commit()


class SignalReq(BaseModel):
    kind: str
    value: str
    weight: float = 1.0


class PredictReq(BaseModel):
    method: str
    path: str


class PredictResp(BaseModel):
    pattern: str
    known: bool
    expected_status: Optional[int]
    confidence: float
    anomaly_score: float
    matches: int


@router.post("/signal")
def signal(req: SignalReq):

    ensure_psyche_db()

    rule_id = hashlib.sha256(f"{req.kind}:{req.value}".encode()).hexdigest()

    with sqlite3.connect(PSYCHE_DB) as conn:

        conn.execute(
            """
            INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?)
        """,
            (rule_id, int(time.time()), req.kind, req.value, req.weight),
        )

        conn.commit()

    return {"ok": True}


@router.post("/predict", response_model=PredictResp)
def predict(req: PredictReq):

    ensure_psyche_db()

    pattern_prefix = f"{req.method}:{req.path}"

    with sqlite3.connect(PSYCHE_DB) as conn:

        rows = conn.execute(
            """
            SELECT pattern, weight FROM rules
            WHERE kind='endpoint'
            AND pattern LIKE ?
        """,
            (pattern_prefix + "%",),
        ).fetchall()

    if not rows:

        return PredictResp(
            pattern=pattern_prefix,
            known=False,
            expected_status=None,
            confidence=0.0,
            anomaly_score=1.0,
            matches=0,
        )

    statuses: List[int] = []
    weights: List[float] = []

    for pattern, weight in rows:

        parts = pattern.split(":")
        if len(parts) == 3:

            status = int(parts[2])
            statuses.append(status)
            weights.append(weight)

    if not statuses:

        return PredictResp(
            pattern=pattern_prefix,
            known=False,
            expected_status=None,
            confidence=0.0,
            anomaly_score=1.0,
            matches=len(rows),
        )

    total_weight = sum(weights)

    status_weights: Dict[int, float] = {}

    for s, w in zip(statuses, weights):
        status_weights[s] = status_weights.get(s, 0) + w

    expected_status = max(status_weights, key=status_weights.get)

    confidence = status_weights[expected_status] / total_weight

    anomaly_score = 1.0 - confidence

    return PredictResp(
        pattern=pattern_prefix,
        known=True,
        expected_status=expected_status,
        confidence=round(confidence, 3),
        anomaly_score=round(anomaly_score, 3),
        matches=len(rows),
    )


@router.post("/train")
def train(limit: int = 100):

    ensure_psyche_db()

    with sqlite3.connect(EVENTS_DB) as conn:

        rows = conn.execute(
            """
            SELECT method, path, status
            FROM events
            ORDER BY ts DESC
            LIMIT ?
        """,
            (limit,),
        ).fetchall()

    count = 0

    with sqlite3.connect(PSYCHE_DB) as conn:

        for method, path, status in rows:

            pattern = f"{method}:{path}:{status}"

            rule_id = hashlib.sha256(f"endpoint:{pattern}".encode()).hexdigest()

            conn.execute(
                """
                INSERT OR REPLACE INTO rules VALUES(?,?,?,?,?)
            """,
                (rule_id, int(time.time()), "endpoint", pattern, 1.0),
            )

            count += 1

        conn.commit()

    return {"trained": True, "rules": count}
