"""The robot's WebSocket server: accepts incoming brain connections,
authenticates/pairs them via the shared handshake, then hands off to
RobotSession -- direction-swapped mirror of
``brain/milo_brain/server.py``'s old ``BrainServer`` (the robot is now the
server+advertiser, the brain is the client+discoverer; see
``advertiser.py`` and ``brain/milo_brain/net/connector.py``).
"""

from __future__ import annotations

import asyncio
import logging
import socket as socketlib

from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, robot_handshake
from milo_common.protocol import MiloSocket

from .advertiser import RobotAdvertiser
from .pairing import PairingController
from .session import RobotSession

log = logging.getLogger(__name__)

PORT_FALLBACK_ATTEMPTS = 200  # Windows/Hyper-V dev boxes exclude ~100-port blocks; harmless on the Pi


def _port_free(port: int) -> bool:
    with socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM) as s:
        s.setsockopt(socketlib.SOL_SOCKET, socketlib.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int, attempts: int = PORT_FALLBACK_ATTEMPTS, port_free=_port_free) -> int:
    """Preferred port, or the next free one (see brain/milo_brain/server.py's
    identical helper -- kept symmetric between the two services)."""
    for port in range(preferred, preferred + attempts):
        if port_free(port):
            if port != preferred:
                log.warning("port %d unavailable, using %d instead", preferred, port)
            return port
    log.warning(
        "no free port found in %d-%d, trying preferred %d anyway",
        preferred, preferred + attempts - 1, preferred,
    )
    return preferred


class RobotServer:
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
        advertiser: RobotAdvertiser | None = None,
    ):
        self._cfg = cfg
        self._display = display
        self._runner = runner
        self._audio = audio
        self._graph_api = graph_api
        self._gait = gait
        self._media_hub = media_hub
        self._broker = broker
        self._store = PairedStore(cfg.paired_path)
        self.advertiser = advertiser or RobotAdvertiser(cfg)
        self.pairing = PairingController(self.advertiser, display)
        self.connected_brain = None
        self.link_state: str = "disconnected"

    def paired_brains(self) -> list[dict]:
        """[{'id':..., 'name':...}] for the webapp's Brain card -- brains
        this robot knows about, whether or not one is online right now."""
        return [{"id": pid, "name": self._store.name_for(pid)} for pid in self._store.peer_ids()]

    async def _on_connection(self, ws) -> None:
        if self.connected_brain is not None:
            # One controlling brain at a time -- reject before even running
            # the handshake so a busy robot doesn't waste either side's time.
            log.warning(
                "rejecting connection: already connected to %s", self.connected_brain.id
            )
            await ws.close(code=4002, reason="robot already has a brain connected")
            return
        sock = MiloSocket(ws)
        pin = self.pairing.pin_for_incoming()
        try:
            peer = await robot_handshake(
                sock, self._cfg.robot_id, self._cfg.robot_name, self._store,
                pending_pin=pin, mcp_port=self._cfg.mcp_port,
            )
        except HandshakeError as exc:
            log.warning("refused connection: %s", exc)
            await sock.close(4001, "auth failed")
            return
        if pin is not None:
            # A connection completed while pairing mode was on -- whether
            # it was this exact PIN's new pairing or an already-paired
            # brain reconnecting during the window, the window has served
            # its purpose; close it so the PIN stops being valid/shown.
            await self.pairing.exit_pairing_mode()
        log.info("brain connected: %s (%s)", peer.name, peer.id)
        self.connected_brain = peer
        self.link_state = "connected"
        await asyncio.to_thread(self.advertiser.update, busy=True)
        if self._broker is not None:
            self._broker.set_brain_connected(True)
        try:
            session = RobotSession(
                sock, display=self._display, media_hub=self._media_hub,
                audio=self._audio, graph_api=self._graph_api,
            )
            await session.run()
        except Exception as exc:
            log.info("brain session ended: %s: %s", type(exc).__name__, exc)
        finally:
            self.connected_brain = None
            self.link_state = "disconnected"
            await asyncio.to_thread(self.advertiser.update, busy=False)
            if self._broker is not None:
                self._broker.set_brain_connected(False)

    async def serve_forever(self) -> None:
        import websockets

        # Resolve before advertising -- the mDNS record embeds the port, so
        # it must already reflect whatever we're actually about to bind.
        port = await asyncio.to_thread(pick_port, self._cfg.robot_ws_port)
        self._cfg.robot_ws_port = port

        # Zeroconf's synchronous API detects the running event loop on its
        # calling thread and schedules its own engine on it -- calling it
        # directly here (this coroutine's own loop thread) deadlocks that
        # scheduling. Run it on a worker thread instead (see
        # brain/milo_brain/server.py's identical note).
        await asyncio.to_thread(self.advertiser.start)
        try:
            async with websockets.serve(self._on_connection, "0.0.0.0", self._cfg.robot_ws_port):
                log.info(
                    "milo-robot %r listening on :%d", self._cfg.robot_name, self._cfg.robot_ws_port
                )
                await asyncio.Future()  # run until cancelled
        finally:
            await asyncio.to_thread(self.advertiser.stop)
