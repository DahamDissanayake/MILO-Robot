"""The robot's post-handshake dispatch loop for a live brain connection.

Connection/pairing itself lives in server.py (accept loop) and
advertiser.py/pairing.py (mDNS + PIN) -- this is just what runs once a
brain is already authenticated: pumps media out, executes what comes back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from milo_common import protocol
from milo_common.protocol import MiloSocket

from . import streams

log = logging.getLogger(__name__)


class RobotSession:
    """One authenticated connection: pumps media out, executes what comes back."""

    def __init__(
        self,
        sock: MiloSocket,
        *,
        display,
        media_hub=None,
        audio=None,
        graph_api=None,
    ):
        self._sock = sock
        self._display = display
        self._hub = media_hub
        # `audio` here is the local speaker only (T_TTS playback below);
        # outbound mic/camera capture streaming is owned by the media hub.
        self._audio = audio
        self._graph_api = graph_api

    async def run(self) -> None:
        pumps: list[asyncio.Task] = []
        if self._hub is not None and self._hub.video is not None:
            pumps.append(asyncio.create_task(streams.pump_video(self._sock, self._hub.video)))
        if self._hub is not None and self._hub.audio is not None:
            pumps.append(asyncio.create_task(streams.pump_audio(self._sock, self._hub.audio)))
        try:
            while True:
                msg = await self._sock.recv()
                await self.dispatch(msg)
        finally:
            for task in pumps:
                task.cancel()
            for task in pumps:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

    async def dispatch(self, msg: protocol.Message) -> None:
        if msg.t == protocol.T_TTS:
            if self._audio is not None and msg.payload:
                self._audio.play_pcm(msg.payload)
        elif msg.t == protocol.T_GRAPH:
            await self._handle_graph(msg)
        else:
            log.debug("ignoring message type %r", msg.t)

    async def _handle_graph(self, msg: protocol.Message) -> None:
        if self._graph_api is None:
            await self._sock.send(
                protocol.T_GRAPH_RESULT, id=msg.get("id"), error="graph unavailable"
            )
            return
        result = self._graph_api.handle(dict(msg.header))
        await self._sock.send(protocol.T_GRAPH_RESULT, **result)
