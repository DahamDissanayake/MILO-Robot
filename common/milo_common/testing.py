"""Test doubles shared by the common/bridge/brain test suites."""

from __future__ import annotations

import asyncio

from .protocol import MiloSocket


class FakeWebSocket:
    """In-memory duplex: frames sent on one end appear on the peer's recv queue."""

    def __init__(self):
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.peer: "FakeWebSocket | None" = None
        self.closed = False

    async def send(self, frame):
        assert self.peer is not None
        await self.peer.outbox.put(frame)

    async def recv(self):
        return await self.outbox.get()

    async def close(self, code=1000, reason=""):
        self.closed = True


def socket_pair() -> tuple[MiloSocket, MiloSocket]:
    a, b = FakeWebSocket(), FakeWebSocket()
    a.peer, b.peer = b, a
    return MiloSocket(a), MiloSocket(b)
