import json
import time
import uuid
from typing import Any, Dict, List, Optional

from aihub.core.config import settings
from aihub.sidecar_db import (
    ensure_http_events_schema_sqlite,
    http_events_insert_row,
    http_events_list_for_store,
    is_postgres,
)


class EventsStore:

    def __init__(self, settings_obj: Any) -> None:
        self.settings = settings_obj
        if not is_postgres():
            self._init_db()

    def _init_db(self) -> None:
        ensure_http_events_schema_sqlite()

    def insert_event(
        self,
        method: str,
        path: str,
        query: str,
        status: int,
        latency_ms: int,
        req_headers: Dict[str, Any],
        req_body_b64: str,
        resp_headers: Dict[str, Any],
        resp_body_b64: str,
        client_ip: str,
        user_agent: str,
        api_key_fp: str,
    ) -> None:
        row = (
            str(uuid.uuid4()),
            int(time.time()),
            method,
            path,
            query,
            status,
            latency_ms,
            json.dumps(req_headers),
            req_body_b64,
            json.dumps(resp_headers),
            resp_body_b64,
            client_ip,
            user_agent,
            api_key_fp,
        )
        http_events_insert_row(row)

    def list_events(
        self,
        limit: int = 100,
        path_prefix: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        return http_events_list_for_store(limit, path_prefix)
