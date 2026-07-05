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

from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, Peer, brain_handshake
from milo_common.protocol import MiloSocket

from .config import BrainConfig

log = logging.getLogger(__name__)

SERVICE_TYPE = "_milo-brain._tcp.local."

RobotHandler = Callable[[MiloSocket, Peer], Awaitable[None]]


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
        host = socketlib.gethostbyname(socketlib.gethostname())
        return ServiceInfo(
            SERVICE_TYPE,
            f"{self._cfg.brain_id}.{SERVICE_TYPE}",
            addresses=[socketlib.inet_aton(host)],
            port=self._cfg.port,
            properties=props,
        )

    def start(self) -> None:
        from zeroconf import Zeroconf

        self._zc = Zeroconf()
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

        self.advertiser.start()
        try:
            async with websockets.serve(self._on_connection, "0.0.0.0", self._cfg.port):
                log.info(
                    "milo-brain %r (%s tier) listening on :%d",
                    self._cfg.name, self._cfg.tier, self._cfg.port,
                )
                await asyncio.Future()  # run until cancelled
        finally:
            self.advertiser.stop()
