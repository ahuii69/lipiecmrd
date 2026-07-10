#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import json
import time
from typing import Any, AsyncGenerator, Dict

from aihub.config import SSE_KEEPALIVE_S
from aihub.db import get_events_since


def sse_format(event: str, data: Dict[str, Any], event_id: int) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event_id}\nevent: {event}\ndata: {payload}\n\n"


async def event_stream(user_id: str, last_id: int = 0) -> AsyncGenerator[bytes, None]:
    # Poll DB; durable log means reconnect works.
    keepalive = SSE_KEEPALIVE_S
    next_ka = time.time() + keepalive
    cur = last_id
    while True:
        events = get_events_since(user_id, cur, limit=250)
        if events:
            for ev in events:
                cur = int(ev["id"])
                yield sse_format(
                    ev["type"], {"ts": ev["ts"], **(ev["data"] or {})}, cur
                ).encode("utf-8")
            next_ka = time.time() + keepalive
        else:
            if time.time() >= next_ka:
                # keepalive comment
                yield b": keepalive\n\n"
                next_ka = time.time() + keepalive
            await asyncio.sleep(0.4)
