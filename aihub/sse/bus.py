from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Set


class EventBus:
    def __init__(self) -> None:
        self._subs: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        async with self._lock:
            self._subs.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subs.discard(q)

    async def publish(self, ev: Dict[str, Any]) -> None:
        ev = dict(ev)
        ev.setdefault("ts", int(time.time()))
        payload = json.dumps(ev, ensure_ascii=False, separators=(",", ":"))
        async with self._lock:
            dead = []
            for q in self._subs:
                try:
                    q.put_nowait(payload)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subs.discard(q)


BUS = EventBus()
