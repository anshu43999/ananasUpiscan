from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import WebSocket
else:
    WebSocket = Any


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, job_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[job_id].add(websocket)

    async def disconnect(self, job_id: str, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(job_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(job_id, None)

    async def broadcast(self, job_id: str, payload: dict[str, Any]) -> None:
        async with self._lock:
            sockets = list(self._connections.get(job_id, set()))
        stale: list[WebSocket] = []
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                stale.append(websocket)
        if stale:
            async with self._lock:
                sockets_set = self._connections.get(job_id)
                if sockets_set:
                    for websocket in stale:
                        sockets_set.discard(websocket)
