"""The brain's live link to a robot: discover -> select -> connect ->
session, failover in a loop -- direction-swapped mirror of the old
bridge/milo_bridge/net/session.py's SessionManager (the brain is now the
client dialing out to a robot it discovered via mDNS, instead of the robot
dialing out to a brain).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from milo_common.auth import PairedStore
from milo_common.handshake import HandshakeError, Peer, brain_handshake
from milo_common.protocol import MiloSocket

from .discovery import RobotDiscovery, select_robot

log = logging.getLogger(__name__)

RobotHandler = Callable[[MiloSocket, Peer], Awaitable[None]]

DEFAULT_ROBOT_PORT = 8765  # matches BridgeConfig.robot_ws_port's default

MAX_DROP_BACKOFF_SECONDS = 30.0


def _drop_backoff_seconds(consecutive_drops: int) -> float:
    """Capped exponential backoff after a connection drop: 1, 2, 4, 8, 16,
    30, 30, ... -- fast retry for a one-off blip, capped so a real outage
    doesn't turn into a retry storm hammering DNS/the network every second."""
    return min(2.0 ** (consecutive_drops - 1), MAX_DROP_BACKOFF_SECONDS)


def _parse_host_port(url: str) -> tuple[str, int]:
    """Every url this module builds is exactly "ws://host:port" (record.url
    or request_manual_ip_connect's f-string) -- no path, no IPv6 host, so a
    plain rsplit is enough to recover (host, port) for last_connected."""
    host, port = url.removeprefix("ws://").rsplit(":", 1)
    return host, int(port)


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
        # "idle" | "connecting" | "handshaking" | "connected" | "retrying" --
        # read by the dashboard every poll to show what's actually happening.
        self.link_state: str = "idle"
        self.link_target: tuple[str, int] | None = None  # host:port currently being dialed
        self.last_error: str | None = None  # most recent connect/handshake failure
        self.retry_at: float | None = None  # time.monotonic() deadline for the next attempt
        self._manual_target: str | None = None
        self._manual_host_target: tuple[str, int] | None = None
        # Last robot this process actually completed a handshake with --
        # lets the dashboard's one-key Reconnect redial immediately instead
        # of waiting for the next scheduled retry or a fresh discovery scan.
        self.last_connected: tuple[str, int] | None = None
        self._wake = asyncio.Event()
        # Consecutive connection drops since the last successful connect --
        # drives _drop_backoff_seconds(); reset to 0 as soon as a connect
        # succeeds again.
        self.consecutive_drops = 0
        # Manual-disconnect latch: request_disconnect() sets this False and
        # closes the live socket; the tick loop then idles instead of
        # auto-reconnecting until an explicit connect action re-enables it.
        self._enabled = True
        self._active_ws = None

    def paired_ids(self) -> list[str]:
        return self._store.peer_ids()

    def is_paired(self, robot_id: str) -> bool:
        return self._store.is_paired(robot_id)

    def request_manual_connect(self, robot_id: str) -> None:
        """The "Connect" action from the Connect Robots tab, for a robot
        that discovery actually found. One-shot: consumed by the very next
        _tick(), whether it succeeds or not."""
        self._enabled = True
        self._manual_target = robot_id
        self._wake.set()

    def request_manual_ip_connect(self, host: str, port: int = DEFAULT_ROBOT_PORT) -> None:
        """Bypass discovery entirely and dial host:port directly on the
        next tick -- for networks where mDNS multicast doesn't reach
        between devices (some routers don't forward it between WiFi
        clients) but plain unicast still works. The robot's identity isn't
        known until the handshake's T_HELLO, so this always offers
        request_pin (harmless if it turns out to already be paired --
        brain_handshake() only calls it on a reactive T_PAIR_BEGIN, which a
        paired robot never sends). One-shot, same as request_manual_connect."""
        self._enabled = True
        self._manual_host_target = (host, port)
        self._wake.set()

    def request_reconnect(self) -> bool:
        """Redial the last robot this process actually connected to, right
        now -- the dashboard's one-key Reconnect action. Skips discovery
        and cuts short whatever reconnect_seconds wait _tick() is
        currently sitting in. Returns False (no-op) if nothing has
        connected yet this run, e.g. right after startup."""
        self._enabled = True
        if self.last_connected is None:
            return False
        self._manual_host_target = self.last_connected
        self._wake.set()
        return True

    def request_disconnect(self) -> bool:
        """Close the current robot connection and stop auto-reconnecting
        until an explicit connect/reconnect action. Returns False (no-op)
        if nothing is connected right now."""
        if self.connected_robot is None or self._active_ws is None:
            return False
        self._enabled = False
        self.link_state = "disconnected"
        ws, self._active_ws = self._active_ws, None
        asyncio.create_task(ws.close())
        self._wake.set()
        return True

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
        if not self._enabled:
            self.link_state = "disconnected"
            self.link_target = None
            await self._wait_before_retry(3600)  # wake on any connect action
            return
        manual_host_target, self._manual_host_target = self._manual_host_target, None
        if manual_host_target is not None:
            host, port = manual_host_target
            await self._connect_and_run(f"ws://{host}:{port}", offer_pairing=True)
            return

        manual_target, self._manual_target = self._manual_target, None
        choice = select_robot(self.discovery.snapshot(), self._store, manual_target=manual_target)
        if choice is None:
            self.link_state = "idle"
            self.link_target = None
            await self._wait_before_retry(self._cfg.reconnect_seconds)
            return
        record, needs_pairing = choice
        await self._connect_and_run(record.url, offer_pairing=needs_pairing)

    async def _wait_before_retry(self, seconds: float) -> None:
        """Like asyncio.sleep(seconds), but request_reconnect()/
        request_manual_connect()/request_manual_ip_connect() can cut it
        short -- otherwise a manual reconnect request would just sit queued
        behind whatever wait _tick() happened to already be in."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass
        self._wake.clear()

    async def _connect_and_run(self, url: str, *, offer_pairing: bool) -> None:
        self.link_state = "connecting"
        self.link_target = _parse_host_port(url)
        self.last_error = None
        try:
            async with self._connect(url) as ws:
                self._active_ws = ws
                sock = MiloSocket(ws)
                self.link_state = "handshaking"
                peer = await brain_handshake(
                    sock,
                    self._cfg.brain_id,
                    self._cfg.name,
                    self._cfg.tier,
                    self._store,
                    request_pin=self._request_pin if offer_pairing else None,
                )
                if peer.mcp_port:
                    host = ws.remote_address[0]  # websockets client connections expose this too
                    peer = replace(peer, mcp_url=f"http://{host}:{peer.mcp_port}")
                log.info("connected to robot %s (%s)", peer.name, peer.id)
                self.connected_robot = peer
                self.link_state = "connected"
                self.last_connected = _parse_host_port(url)
                self.consecutive_drops = 0
                try:
                    await self._session_handler(sock, peer)
                finally:
                    self.connected_robot = None
                    self._active_ws = None
                    if self._enabled:
                        self.link_state = "idle"
        except HandshakeError as exc:
            self.link_state = "idle"
            self.last_error = f"handshake failed: {exc}"
            log.warning("handshake with %s failed: %s", url, exc)
            await self._wait_before_retry(self._cfg.reconnect_seconds)
        except Exception as exc:  # connection drop -> fail over on next tick
            self._active_ws = None
            if not self._enabled:
                # Our own request_disconnect() closed the socket -- go idle,
                # don't treat it as a drop to rescan/retry. Clear _wake
                # ourselves since we're skipping _wait_before_retry (which
                # would normally do it): request_disconnect() set it to
                # cut short any in-progress wait, and leaving it set would
                # make the very next tick's idle-wait in _tick() return
                # immediately instead of actually idling.
                self._wake.clear()
                return
            self.consecutive_drops += 1
            backoff = _drop_backoff_seconds(self.consecutive_drops)
            self.link_state = "retrying"
            self.retry_at = time.monotonic() + backoff
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.info(
                "robot link lost (%s: %s), rescanning in %.0fs",
                type(exc).__name__, exc, backoff,
            )
            await self._wait_before_retry(backoff)
            self.retry_at = None
