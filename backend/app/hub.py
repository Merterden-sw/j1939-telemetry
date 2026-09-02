"""
WebSocket yayin katmani.

Her istemci icin sinirli boyutlu bir kuyruk ve bagimsiz bir yazici gorevi
tutulur. Yavas bir istemci simulasyon dongusunu bloke edemez; kuyruk dolarsa
en eski mesaj dusurulur (telemetri verisinde tazelik, butunlukten onemlidir).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from starlette.websockets import WebSocket, WebSocketState

logger = logging.getLogger(__name__)

QUEUE_SIZE = 32


class ClientConnection:
    """Tek bir WebSocket istemcisi ve ona ait cikis kuyrugu."""

    _next_id = 0

    def __init__(self, websocket: WebSocket) -> None:
        ClientConnection._next_id += 1
        self.id = f"c{ClientConnection._next_id}"
        self.websocket = websocket
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=QUEUE_SIZE)
        self.subscriptions: set[str] = set()
        self.dropped = 0
        self._writer: asyncio.Task | None = None

    async def start(self) -> None:
        self._writer = asyncio.create_task(self._write_loop(), name=f"ws-writer-{self.id}")

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
            self._writer = None
        if self.websocket.client_state is WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await self.websocket.close()

    def enqueue(self, message: str) -> None:
        """Mesaji kuyruga koyar; kuyruk doluysa en eskiyi dusurur."""
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            self.dropped += 1
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(message)

    async def _write_loop(self) -> None:
        try:
            while True:
                message = await self.queue.get()
                await self.websocket.send_text(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # baglanti koptu
            logger.debug("Istemci %s yazma hatasi: %s", self.id, exc)


class Hub:
    """Bagli tum istemcilere mesaj dagitan yayin merkezi."""

    def __init__(self) -> None:
        self._clients: dict[str, ClientConnection] = {}
        self._lock = asyncio.Lock()

    @property
    def client_count(self) -> int:
        return len(self._clients)

    @property
    def clients(self) -> list[ClientConnection]:
        return list(self._clients.values())

    async def connect(self, websocket: WebSocket) -> ClientConnection:
        await websocket.accept()
        client = ClientConnection(websocket)
        await client.start()
        async with self._lock:
            self._clients[client.id] = client
        logger.info("Istemci baglandi: %s (toplam %d)", client.id, len(self._clients))
        return client

    async def disconnect(self, client: ClientConnection) -> None:
        async with self._lock:
            self._clients.pop(client.id, None)
        await client.close()
        logger.info("Istemci ayrildi: %s (toplam %d)", client.id, len(self._clients))

    async def send(self, client: ClientConnection, payload: dict[str, Any]) -> None:
        client.enqueue(json.dumps(payload, separators=(",", ":")))

    async def broadcast(self, payload: dict[str, Any]) -> None:
        message = json.dumps(payload, separators=(",", ":"))
        for client in list(self._clients.values()):
            client.enqueue(message)

    async def broadcast_per_client(self, builder) -> None:
        """Her istemci icin abonelige gore ozellestirilmis mesaj uretir."""
        for client in list(self._clients.values()):
            payload = builder(client)
            if payload is not None:
                client.enqueue(json.dumps(payload, separators=(",", ":")))

    async def shutdown(self) -> None:
        for client in list(self._clients.values()):
            await self.disconnect(client)
