"""Milo Brain server: WebSocket endpoint + mDNS advertisement.

Each connecting robot is authenticated (or paired) via the shared handshake,
then handed to a session handler. The default handler just logs; the cognition
pipelines (pipelines/, llm/) plug in through the same interface.
"""

from __future__ import annotations

import asyncio
import logging
import socket as socketlib
from collections.abc import Awaitable, Callable
from dataclasses import replace

from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, Peer, brain_handshake
from milo_common.protocol import MiloSocket

from .config import BrainConfig

log = logging.getLogger(__name__)

SERVICE_TYPE = "_milo-brain._tcp.local."
PORT_FALLBACK_ATTEMPTS = 200  # Windows/Hyper-V can exclude ~100-port blocks

RobotHandler = Callable[[MiloSocket, Peer], Awaitable[None]]


def _port_free(port: int) -> bool:
    with socketlib.socket(socketlib.AF_INET, socketlib.SOCK_STREAM) as s:
        s.setsockopt(socketlib.SOL_SOCKET, socketlib.SO_REUSEADDR, 1)
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def pick_port(preferred: int, attempts: int = PORT_FALLBACK_ATTEMPTS, port_free=_port_free) -> int:
    """Preferred port, or the next free one -- Windows/Hyper-V reserves ~100-port
    exclusion blocks for NAT that shift across reboots, so a fixed single fallback
    (unlike the bridge webapp's port 80->8080) isn't reliable here."""
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


def _local_ip() -> str:
    """Best-effort real LAN IP to advertise to the robot.

    ``gethostbyname(gethostname())`` is unreliable on a multi-homed machine --
    on Windows with a VPN client, WSL/Hyper-V, or VirtualBox installed, it can
    resolve to one of *those* virtual adapters instead of the real WiFi/LAN
    one (confirmed: a Fortinet VPN adapter's address, unreachable from the
    robot, on a dev machine with several such adapters). Connecting a UDP
    socket sends no packets -- it only asks the OS routing table which local
    interface would reach the destination, which is the real outbound-facing
    adapter regardless of how many virtual ones exist alongside it.
    """
    with socketlib.socket(socketlib.AF_INET, socketlib.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return socketlib.gethostbyname(socketlib.gethostname())


async def default_handler(sock: MiloSocket, peer: Peer) -> None:
    """Phase C debug handler: log stream arrival rates until the robot leaves."""
    frames = 0
    while True:
        msg = await sock.recv()
        frames += 1
        if frames % 100 == 0:
            log.info("robot %s: %d frames received (last: %s)", peer.id, frames, msg.t)


class Advertiser:
    """Registers/updates the ``_milo-brain._tcp`` mDNS service."""

    def __init__(self, cfg: BrainConfig):
        self._cfg = cfg
        self._zc = None
        self._info = None
        self.busy = False
        self.pairing = False

    def _service_info(self):
        from zeroconf import ServiceInfo

        props = {
            "id": self._cfg.brain_id,
            "name": self._cfg.name,
            "gpu": self._cfg.gpu,
            "tier": self._cfg.tier,
            "busy": "1" if self.busy else "0",
            "pairing": "1" if self.pairing else "0",
        }
        host = _local_ip()
        return ServiceInfo(
            SERVICE_TYPE,
            f"{self._cfg.brain_id}.{SERVICE_TYPE}",
            # zeroconf's own register path back-fills a missing `server` from
            # the instance name (ServiceInfo.set_server_if_missing()), but its
            # update path doesn't -- and Advertiser.update() builds a fresh
            # ServiceInfo every call, so it must be set explicitly here or
            # update_service() hits `AssertionError: ServiceInfo must have a
            # server` in zeroconf's registry.
            server=f"{self._cfg.brain_id}.local.",
            addresses=[socketlib.inet_aton(host)],
            port=self._cfg.port,
            properties=props,
        )

    def start(self) -> None:
        from zeroconf import InterfaceChoice, Zeroconf

        # InterfaceChoice.All (the zeroconf default) enumerates and binds to
        # every adapter, including virtual ones that will never see the robot
        # (VPN, Hyper-V/WSL switches, VirtualBox host-only, ...). Default
        # restricts this to interfaces with a default route -- the real
        # LAN/WiFi link the robot is actually reachable on.
        self._zc = Zeroconf(interfaces=InterfaceChoice.Default)
        self._info = self._service_info()
        self._zc.register_service(self._info)

    def update(self, *, busy: bool | None = None, pairing: bool | None = None) -> None:
        if busy is not None:
            self.busy = busy
        if pairing is not None:
            self.pairing = pairing
        if self._zc is not None:
            new_info = self._service_info()
            self._zc.update_service(new_info)
            self._info = new_info

    def stop(self) -> None:
        if self._zc is not None:
            if self._info is not None:
                self._zc.unregister_service(self._info)
            self._zc.close()
            self._zc = None


class BrainServer:
    def __init__(
        self,
        cfg: BrainConfig,
        *,
        handler: RobotHandler = default_handler,
        request_pin: Callable[[str], Awaitable[str | None]] | None = None,
        advertiser: Advertiser | None = None,
    ):
        self._cfg = cfg
        self._handler = handler
        self._request_pin = request_pin
        self._store = PairedStore(cfg.paired_path)
        self.advertiser = advertiser or Advertiser(cfg)
        self.connected_robot: Peer | None = None

    async def _on_connection(self, ws) -> None:
        sock = MiloSocket(ws)
        try:
            peer = await brain_handshake(
                sock,
                self._cfg.brain_id,
                self._cfg.name,
                self._cfg.tier,
                self._store,
                request_pin=self._request_pin if self.advertiser.pairing else None,
            )
        except HandshakeError as exc:
            log.warning("refused connection: %s", exc)
            await sock.close(4001, "auth failed")
            return
        if peer.mcp_port:
            host = ws.remote_address[0]
            peer = replace(peer, mcp_url=f"http://{host}:{peer.mcp_port}")
        log.info("robot connected: %s (%s)", peer.name, peer.id)
        self.connected_robot = peer
        try:
            await self._handler(sock, peer)
        except Exception as exc:
            log.info("robot session ended: %s: %s", type(exc).__name__, exc)
        finally:
            self.connected_robot = None

    async def serve_forever(self) -> None:
        import websockets

        # Resolve before advertising -- the mDNS record embeds self._cfg.port,
        # so it must already reflect whatever port we're actually about to
        # bind. Mutated in place: self.advertiser shares this same cfg object.
        port = await asyncio.to_thread(pick_port, self._cfg.port)
        self._cfg.port = port

        # Advertiser.start/stop use zeroconf's synchronous API, which detects
        # the running event loop on its calling thread and schedules its own
        # engine on it -- calling it directly here (this coroutine's own loop
        # thread) deadlocks that scheduling and raises EventLoopBlocked. Run
        # it on a worker thread instead, where zeroconf sees no running loop
        # and spins up its own, as it's designed to.
        await asyncio.to_thread(self.advertiser.start)
        try:
            async with websockets.serve(self._on_connection, "0.0.0.0", self._cfg.port):
                log.info(
                    "milo-brain %r (%s tier) listening on :%d",
                    self._cfg.name, self._cfg.tier, self._cfg.port,
                )
                await asyncio.Future()  # run until cancelled
        finally:
            await asyncio.to_thread(self.advertiser.stop)
