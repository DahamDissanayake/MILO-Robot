"""The brain's live link to a robot: discover -> select -> connect ->
session, failover in a loop -- direction-swapped mirror of the old
bridge/milo_bridge/net/session.py's SessionManager (the brain is now the
client dialing out to a robot it discovered via mDNS, instead of the robot
dialing out to a brain).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, Peer, brain_handshake
from milo_common.protocol import MiloSocket

from .discovery import RobotDiscovery, select_robot

log = logging.getLogger(__name__)

RobotHandler = Callable[[MiloSocket, Peer], Awaitable[None]]


async def default_handler(sock: MiloSocket, peer: Peer) -> None:
    """Phase C debug handler: log stream arrival rates until the robot leaves."""
    frames = 0
    while True:
        msg = await sock.recv()
        frames += 1
        if frames % 100 == 0:
            log.info("robot %s: %d frames received (last: %s)", peer.id, frames, msg.t)


class RobotConnectorManager:
    """Discover -> select -> connect -> session; failover in a loop, plus a
    one-shot manual-connect override for the Connect Robots tab. One tick
    loop drives both auto-reconnect and manual connects, so there's never
    more than one connection attempt in flight and the single
    connected_robot invariant holds by construction."""

    def __init__(
        self,
        cfg,
        *,
        request_pin: Callable[[str], Awaitable[str | None]] | None = None,
        session_handler: RobotHandler,
        discovery: RobotDiscovery | None = None,
        connect=None,
    ):
        self._cfg = cfg
        self._request_pin = request_pin
        self._session_handler = session_handler
        self._store = PairedStore(cfg.paired_path)
        self.discovery = discovery or RobotDiscovery()  # public: ConnectRobotsScreen reads .snapshot()
        self._connect = connect
        self.connected_robot: Peer | None = None
        self.link_state: str = "disconnected"
        self._manual_target: str | None = None

    def paired_ids(self) -> list[str]:
        return self._store.peer_ids()

    def is_paired(self, robot_id: str) -> bool:
        return self._store.is_paired(robot_id)

    def request_manual_connect(self, robot_id: str) -> None:
        """The "Connect" action from the Connect Robots tab. One-shot:
        consumed by the very next _tick(), whether it succeeds or not."""
        self._manual_target = robot_id

    async def run_forever(self) -> None:
        if self._connect is None:
            import websockets

            self._connect = websockets.connect
        self.discovery.start()
        try:
            while True:
                await self._tick()
        finally:
            self.discovery.stop()

    async def _tick(self) -> None:
        manual_target, self._manual_target = self._manual_target, None
        choice = select_robot(self.discovery.snapshot(), self._store, manual_target=manual_target)
        if choice is None:
            await asyncio.sleep(self._cfg.reconnect_seconds)
            return
        record, needs_pairing = choice
        try:
            async with self._connect(record.url) as ws:
                sock = MiloSocket(ws)
                peer = await brain_handshake(
                    sock,
                    self._cfg.brain_id,
                    self._cfg.name,
                    self._cfg.tier,
                    self._store,
                    request_pin=self._request_pin if needs_pairing else None,
                )
                if peer.mcp_port:
                    host = ws.remote_address[0]  # websockets client connections expose this too
                    peer = replace(peer, mcp_url=f"http://{host}:{peer.mcp_port}")
                log.info("connected to robot %s (%s)", peer.name, peer.id)
                self.connected_robot = peer
                self.link_state = "connected"
                try:
                    await self._session_handler(sock, peer)
                finally:
                    self.connected_robot = None
                    self.link_state = "disconnected"
        except HandshakeError as exc:
            log.warning("handshake with %s failed: %s", record.robot_id, exc)
            await asyncio.sleep(self._cfg.reconnect_seconds)
        except Exception as exc:  # connection drop -> fail over on next tick
            log.info("robot link lost (%s: %s), rescanning", type(exc).__name__, exc)
            await asyncio.sleep(1.0)
