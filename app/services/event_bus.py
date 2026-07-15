"""Lightweight in-process pub/sub so the SSE live-tail doesn't busy-poll the
DB: `RunService` notifies subscribers immediately after each `append_event`
succeeds (M5.T4). One `asyncio.Queue` per subscriber, keyed by `run_id`.
"""

from __future__ import annotations

import asyncio

from app.domain.events import Event


class RunEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue[Event]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue[Event]) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers is None:
            return
        if queue in subscribers:
            subscribers.remove(queue)
        if not subscribers:
            self._subscribers.pop(run_id, None)

    def publish(self, run_id: str, event: Event) -> None:
        for queue in self._subscribers.get(run_id, ()):
            queue.put_nowait(event)
