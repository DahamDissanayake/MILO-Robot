"""The robot's live link to a brain: dispatch loop + connect/failover manager."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from milo_common import protocol
from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, robot_handshake
from milo_common.protocol import MiloSocket

from ..drivers.display import AnimMode
from . import streams
from .discovery import BrainDiscovery, select_brain

log = logging.getLogger(__name__)


class RobotSession:
    """One authenticated connection: pumps media out, executes what comes back."""

    def __init__(
        self,
        sock: MiloSocket,
        *,
        runner,
        display,
        media_hub=None,
        broker=None,
        audio=None,
        graph_api=None,
        gait=None,
    ):
        self._sock = sock
        self._runner = runner
        self._display = display
        self._hub = media_hub
        self._broker = broker
        # `audio` here is the local speaker only (T_TTS playback below);
        # outbound mic/camera capture streaming is owned by the media hub.
        self._audio = audio
        self._graph_api = graph_api
        self._gait = gait
        self._pose_task: asyncio.Task | None = None

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
        elif msg.t == protocol.T_CMD:
            await self._handle_cmd(msg)
        elif msg.t == protocol.T_GRAPH:
            await self._handle_graph(msg)
        else:
            log.debug("ignoring message type %r", msg.t)

    async def _handle_cmd(self, msg: protocol.Message) -> None:
        face = msg.get("face")
        if face:
            await self._display.set_face(face, AnimMode.LOOP if face.startswith("talk_") else AnimMode.ONCE)
        move = msg.get("move") or {}
        if move.get("stop"):
            # STOP is always allowed, regardless of who holds control (see
            # ControlBroker docstring) — never gated below.
            self._runner.abort()
            if self._gait is not None:
                self._gait.set_velocity_command(0.0, 0.0, 0.0)
        elif self._broker is not None and not self._broker.allow_brain_motion():
            log.info("dropping brain motion cmd while web client controls: %s", msg)
        elif "velocity" in move and self._gait is not None:
            vx, vy, yaw = move["velocity"]
            self._gait.set_velocity_command(vx, vy, yaw)
        elif "turn" in move:
            await self._turn(float(move["turn"]))
        elif "pose" in move:
            self._start_pose(move["pose"])

    async def _turn(self, bearing_deg: float) -> None:
        """Turn toward a bearing (negative = left). Prefers the gait engine."""
        if abs(bearing_deg) < 10:
            return
        if self._gait is not None:
            yaw_rate = -30.0 if bearing_deg < 0 else 30.0
            self._gait.set_velocity_command(0.0, 0.0, yaw_rate)
        else:
            self._start_pose("turn_left" if bearing_deg < 0 else "turn_right", cycles=1)

    def _start_pose(self, name: str, cycles: int | None = None) -> None:
        if self._pose_task is not None and not self._pose_task.done():
            self._runner.abort()
        kwargs = {} if cycles is None else {"cycles": cycles}
        self._pose_task = asyncio.create_task(self._runner.run(name, **kwargs))

    async def _handle_graph(self, msg: protocol.Message) -> None:
        if self._graph_api is None:
            await self._sock.send(
                protocol.T_GRAPH_RESULT, id=msg.get("id"), error="graph unavailable"
            )
            return
        result = self._graph_api.handle(dict(msg.header))
        await self._sock.send(protocol.T_GRAPH_RESULT, **result)


class SessionManager:
    """Discovery -> select -> connect -> session; failover and sleep in a loop."""

    def __init__(
        self,
        cfg,
        *,
        servos,
        display,
        runner,
        audio=None,
        graph_api=None,
        gait=None,
        media_hub=None,
        broker=None,
        sleep_controller=None,
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
        if sleep_controller is None:
            from ..sleep import SleepController

            sleep_controller = SleepController(
                runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=servos
            )
        self._sleep = sleep_controller

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
            await self._sleep.ensure_asleep()
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
                await self._sleep.ensure_awake()
                if self._broker is not None:
                    self._broker.set_brain_connected(True)
                self.link_state = "connected"
                try:
                    session = RobotSession(
                        sock,
                        runner=self._runner,
                        display=self._display,
                        media_hub=self._media_hub,
                        broker=self._broker,
                        audio=self._audio,
                        graph_api=self._graph_api,
                        gait=self._gait,
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
