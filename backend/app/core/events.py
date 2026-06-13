"""In-process pub/sub for SSE: one asyncio.Queue per run_id.

The agent runner publishes trace events; the ``/flows/{run_id}/stream`` route
subscribes and forwards them as Server-Sent Events. Terminal statuses
(``done`` / ``error`` / ``hitl``) end a subscription.
"""
from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator

_TERMINAL = {"done", "error", "hitl", "cancelled"}


class EventBus:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def queue(self, run_id: str) -> asyncio.Queue[dict[str, Any]]:
        q = self._queues.get(run_id)
        if q is None:
            q = asyncio.Queue()
            self._queues[run_id] = q
        return q

    async def publish(self, run_id: str, event: dict[str, Any]) -> None:
        await self.queue(run_id).put(event)

    async def subscribe(self, run_id: str) -> AsyncIterator[dict[str, Any]]:
        q = self.queue(run_id)
        while True:
            event = await q.get()
            yield event
            if event.get("status") in _TERMINAL:
                break
        self.close(run_id)

    def close(self, run_id: str) -> None:
        self._queues.pop(run_id, None)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
