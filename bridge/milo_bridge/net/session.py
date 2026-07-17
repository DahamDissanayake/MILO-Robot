"""The robot's live link to a brain: dispatch loop + connect/failover manager."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from milo_common import protocol
from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, robot_handshake
from milo_common.protocol import MiloSocket

from . import streams
from .discovery import BrainDiscovery, select_brain

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


class SessionManager:
    """Discovery -> select -> connect -> session; failover in a loop.

    Sleep/wake is not this class's concern -- ControlBroker's on_change hook
    (wired in main()) drives SleepController from the single, unified
    "is anyone in control" signal (brain connected OR web client holding
    control), so this class only needs to keep the broker's brain-connected
    flag accurate via set_brain_connected().
    """

    def __init__(
        self,
        cfg,
        *,
        display,
        runner,
        audio=None,
        graph_api=None,
        gait=None,
        media_hub=None,
        broker=None,
        discovery: BrainDiscovery | None = None,
        connect=None,
    ):
        self._cfg = cfg
        self._display = display
        self._runner = runner
        # Local speaker only (T_TTS playback); capture streaming is owned by
        # media_hub, built once in main() from the same driver.
        self._audio = audio
        self._graph_api = graph_api
        self._gait = gait
        self._media_hub = media_hub
        self._broker = broker
        self._store = PairedStore(cfg.paired_path)
        self._discovery = discovery or BrainDiscovery()
        self._connect = connect
        self.link_state: str = "disconnected"

    async def run_forever(self) -> None:
        if self._connect is None:
            import websockets

            self._connect = websockets.connect
        self._discovery.start()
        try:
            while True:
                await self._tick()
        finally:
            self._discovery.stop()

    async def _tick(self) -> None:
        choice = select_brain(self._discovery.snapshot(), self._store)
        if choice is None:
            await asyncio.sleep(self._cfg.reconnect_seconds)
            return
        record, _needs_pairing = choice
        try:
            async with self._connect(record.url) as ws:
                sock = MiloSocket(ws)
                peer = await robot_handshake(
                    sock,
                    self._cfg.robot_id,
                    self._cfg.robot_name,
                    self._store,
                    show_pin=self._show_pin,
                )
                log.info("connected to brain %s (%s)", peer.name, peer.id)
                if self._broker is not None:
                    self._broker.set_brain_connected(True)
                self.link_state = "connected"
                try:
                    session = RobotSession(
                        sock,
                        display=self._display,
                        media_hub=self._media_hub,
                        audio=self._audio,
                        graph_api=self._graph_api,
                    )
                    await session.run()
                finally:
                    if self._broker is not None:
                        self._broker.set_brain_connected(False)
                    self.link_state = "disconnected"
        except HandshakeError as exc:
            log.warning("handshake with %s failed: %s", record.brain_id, exc)
            await asyncio.sleep(self._cfg.reconnect_seconds)
        except Exception as exc:  # connection drop -> fail over on next tick
            log.info("brain link lost (%s: %s), rescanning", type(exc).__name__, exc)
            await asyncio.sleep(1.0)

    async def _show_pin(self, pin: str) -> None:
        await self._display.show_pin(pin)
