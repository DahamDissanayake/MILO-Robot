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
from milo_common.handshake import HandshakeError, Peer, robot_handshake
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
        # Multiple brains may be connected at once (e.g. a phone-tier brain
        # and a desktop-tier brain); only one of them -- active_brain_id --
        # actually gets to move the robot at a time (see mcp/server.py's
        # per-tool gate), the rest just observe. The first brain to connect
        # becomes active automatically; the webapp can switch it explicitly
        # (see webapp/motion.py's switch_active_brain).
        self.connected_brains: dict[str, Peer] = {}
        self.active_brain_id: str | None = None
        # Live socket per connected brain, so the webapp can close a
        # specific brain's session (see disconnect_brain / webapp Brain card).
        self._brain_socks: dict[str, MiloSocket] = {}

    @property
    def connected_brain(self):
        """Back-compat single-brain accessor for callers that only care
        whether *any* brain is connected (e.g. tests, simple status
        checks) -- prefer connected_brains/active_brain_id for anything
        that needs to reason about more than one."""
        if self.active_brain_id is not None:
            return self.connected_brains.get(self.active_brain_id)
        return next(iter(self.connected_brains.values()), None)

    @property
    def link_state(self) -> str:
        return "connected" if self.connected_brains else "disconnected"

    def connected_brains_info(self) -> list[dict]:
        """[{'id':..., 'name':..., 'active': bool}] for the webapp's Brain
        card -- every brain connected right now, not just the active one."""
        return [
            {"id": peer.id, "name": peer.name, "active": peer.id == self.active_brain_id}
            for peer in self.connected_brains.values()
        ]

    def set_active_brain(self, peer_id: str) -> bool:
        """Switch which connected brain is allowed to move the robot.
        Returns False (no-op) if that brain isn't actually connected."""
        if peer_id not in self.connected_brains:
            return False
        self.active_brain_id = peer_id
        return True

    async def disconnect_brain(self, peer_id: str) -> bool:
        """Close a specific connected brain's session. The session's own
        finally in _on_connection does the bookkeeping (drops it from
        connected_brains, reassigns active_brain_id, updates busy). Returns
        False if that brain isn't connected."""
        sock = self._brain_socks.get(peer_id)
        if sock is None:
            return False
        await sock.close(4003, "disconnected by operator")
        return True

    @property
    def port(self) -> int:
        """The actual bound WS port (may differ from the configured
        preference -- see pick_port()); for the webapp's Brain card, so a
        brain can be told exactly where to connect when mDNS discovery
        doesn't reach it (some routers don't forward multicast between
        WiFi clients even on the same network)."""
        return self._cfg.robot_ws_port

    def paired_brains(self) -> list[dict]:
        """[{'id':..., 'name':...}] for the webapp's Brain card -- brains
        this robot knows about, whether or not one is online right now."""
        return [{"id": pid, "name": self._store.name_for(pid)} for pid in self._store.peer_ids()]

    async def _on_connection(self, ws) -> None:
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
        if peer.id in self.connected_brains:
            # Same brain identity already has a live session (e.g. a stale
            # connection that hasn't torn down yet) -- refuse the duplicate
            # rather than running two sessions for one brain. Different
            # brains are fine; several may be connected at once.
            log.warning("rejecting duplicate connection: %s already connected", peer.id)
            await sock.close(4002, "this brain already has a connection open")
            return
        if pin is not None:
            # A connection completed while pairing mode was on -- whether
            # it was this exact PIN's new pairing or an already-paired
            # brain reconnecting during the window, the window has served
            # its purpose; close it so the PIN stops being valid/shown.
            await self.pairing.exit_pairing_mode()
        log.info("brain connected: %s (%s)", peer.name, peer.id)
        self.connected_brains[peer.id] = peer
        self._brain_socks[peer.id] = sock
        if self.active_brain_id is None:
            # First brain in gets motion rights automatically; anyone who
            # joins after that just observes until the webapp switches them
            # in (see webapp/motion.py's switch_active_brain).
            self.active_brain_id = peer.id
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
            self.connected_brains.pop(peer.id, None)
            self._brain_socks.pop(peer.id, None)
            if self.active_brain_id == peer.id:
                # Hand motion rights to whoever else is still here, if anyone.
                self.active_brain_id = next(iter(self.connected_brains), None)
            still_busy = bool(self.connected_brains)
            await asyncio.to_thread(self.advertiser.update, busy=still_busy)
            if self._broker is not None:
                self._broker.set_brain_connected(still_busy)

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
