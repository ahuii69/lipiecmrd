# LEGACY / UNMOUNTED: not mounted in aihub.main; canonical HTTP surface is aihub.main + aihub/*_api.py. See aihub/api/_LEGACY.md.
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aihub.db import fetch_all
from aihub.sidecar_db import PSYCHE_DB_PATH, ensure_psyche_rules_schema_sqlite, is_postgres

router = APIRouter(prefix="/psyche", tags=["psyche"])


class PredictReq(BaseModel):
    method: str
    path: str
    status: Optional[int] = None


class PredictResp(BaseModel):
    pattern: str
    known: bool
    expected_status: Optional[int]
    confidence: float
    anomaly_score: float
    matches: int


def ensure_db() -> None:
    if is_postgres():
        return
    if not PSYCHE_DB_PATH.exists():
        raise HTTPException(500, "psyche.db missing")
    ensure_psyche_rules_schema_sqlite()


def load_rules(pattern_prefix: str) -> List[Dict]:
    ensure_db()
    like_arg = pattern_prefix + "%"
    if is_postgres():
        rows = fetch_all(
            """
            SELECT pattern, weight FROM sidecar.psyche_rules
            WHERE pattern LIKE ?
              AND (kind IS NULL OR kind = 'endpoint')
            """,
            (like_arg,),
        )
        return [{"pattern": r["pattern"], "weight": float(r["weight"])} for r in rows]
    with sqlite3.connect(str(PSYCHE_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT pattern, weight
            FROM rules
            WHERE pattern LIKE ?
              AND (kind IS NULL OR kind = 'endpoint')
            """,
            (like_arg,),
        ).fetchall()
    return [dict(r) for r in rows]


def extract_status(pattern: str) -> Optional[int]:
    try:
        return int(pattern.split(":")[-1])
    except Exception:
        return None


@router.post("/predict", response_model=PredictResp)
def predict(req: PredictReq):
    pattern_prefix = f"{req.method}:{req.path}"
    rules = load_rules(pattern_prefix)
    if not rules:
        return PredictResp(
            pattern=pattern_prefix,
            known=False,
            expected_status=None,
            confidence=0.0,
            anomaly_score=1.0,
            matches=0,
        )
    weights = []
    statuses = []
    for r in rules:
        status = extract_status(r["pattern"])
        if status:
            statuses.append(status)
            weights.append(r["weight"])
    if not statuses:
        return PredictResp(
            pattern=pattern_prefix,
            known=False,
            expected_status=None,
            confidence=0.0,
            anomaly_score=1.0,
            matches=len(rules),
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
        matches=len(rules),
    )
