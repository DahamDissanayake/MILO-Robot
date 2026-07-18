"""End-to-end over real websockets on localhost: robot server accepts,
pairs, tracks connection state, refuses. Mirrors the old
brain/tests/test_server_integration.py's structure, role-swapped."""

import asyncio

import pytest
import websockets

from milo_common.auth import PairedStore, derive_token
from milo_common.handshake import HandshakeError, brain_handshake
from milo_common.protocol import MiloSocket

from milo_bridge.config import BridgeConfig
from milo_bridge.net.server import RobotServer


class NullAdvertiser:
    busy = False
    pairing = False

    def start(self):
        pass

    def update(self, **kw):
        for key, value in kw.items():
            if value is not None:
                setattr(self, key, value)

    def stop(self):
        pass


class FakeDisplay:
    def __init__(self):
        self.shown_pins: list[str] = []
        self.idle = False

    async def show_pin(self, pin):
        self.shown_pins.append(pin)

    def stop_idle(self):
        self.idle = False

    def start_idle(self):
        self.idle = True


def make_server(tmp_path, *, robot_id="milo-1", display=None) -> RobotServer:
    cfg = BridgeConfig(
        robot_id=robot_id, robot_name="milo", data_dir=str(tmp_path / "robot"), robot_ws_port=0,
    )
    return RobotServer(cfg, display=display or FakeDisplay(), runner=None, advertiser=NullAdvertiser())


async def serve(server: RobotServer):
    """Start on an ephemeral port; returns (ws_server, port)."""
    ws_server = await websockets.serve(server._on_connection, "127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    return ws_server, port


async def wait_until(predicate, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.01)):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition never became true")


@pytest.fixture()
def paired_stores(tmp_path):
    token = derive_token("123456", "milo-1", "brain-test")
    robot_store = PairedStore(tmp_path / "robot" / "paired.json")
    robot_store.add("brain-test", token)
    brain_store = PairedStore(tmp_path / "brain" / "paired.json")
    brain_store.add("milo-1", token)
    return robot_store, brain_store


def test_paired_brain_connects_and_is_tracked(tmp_path, paired_stores):
    _, brain_store = paired_stores

    async def main():
        server = make_server(tmp_path)
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                peer = await brain_handshake(sock, "brain-test", "desk", "large", brain_store)
                assert peer.id == "milo-1"
                await wait_until(lambda: server.connected_brain is not None)
                assert server.connected_brain.id == "brain-test"
                assert server.link_state == "connected"
            await wait_until(lambda: server.connected_brain is None)
            assert server.link_state == "disconnected"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())


def test_unpaired_brain_is_refused_when_not_pairing(tmp_path):
    async def main():
        server = make_server(tmp_path)  # pairing mode off
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                fresh_store = PairedStore(tmp_path / "fresh" / "paired.json")
                with pytest.raises((HandshakeError, websockets.ConnectionClosed)):
                    await brain_handshake(sock, "stranger", "x", "small", fresh_store)
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())


def test_full_pairing_flow_shows_pin_before_connection_and_persists_token(tmp_path):
    async def main():
        display = FakeDisplay()
        server = make_server(tmp_path, display=display)
        ws_server, port = await serve(server)
        try:
            # The key new-architecture assertion: the PIN exists and is
            # "shown on the OLED" *before* any brain connects (the old flow
            # generated it reactively mid-handshake instead).
            pin = await server.pairing.enter_pairing_mode()
            assert display.shown_pins == [pin]
            assert server.pairing.current_pin == pin

            brain_store = PairedStore(tmp_path / "brain" / "paired.json")

            async def request_pin(_robot_name):
                return pin

            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                peer = await brain_handshake(
                    sock, "brain-test", "desk", "large", brain_store, request_pin=request_pin
                )
                assert peer.id == "milo-1"

            # Pairing mode auto-closes once a brain successfully connects.
            await wait_until(lambda: server.pairing.current_pin is None)

            # Both sides persisted the token -> a plain reconnect authenticates
            # with no PIN prompt at all.
            robot_store = PairedStore(server._cfg.paired_path)
            assert robot_store.token_for("brain-test") == brain_store.token_for("milo-1")

            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                peer = await brain_handshake(sock, "brain-test", "desk", "large", brain_store)
                assert peer.id == "milo-1"
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())


def test_second_brain_is_rejected_while_one_is_connected(tmp_path):
    async def main():
        # PairedStore reads its file once at construction time -- tokens
        # must land on disk *before* the server (and its own PairedStore)
        # is built, not after.
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        token_a = derive_token("111111", "milo-1", "brain-a")
        robot_store.add("brain-a", token_a)
        token_b = derive_token("222222", "milo-1", "brain-b")
        robot_store.add("brain-b", token_b)
        brain_a_store = PairedStore(tmp_path / "a" / "paired.json")
        brain_a_store.add("milo-1", token_a)
        brain_b_store = PairedStore(tmp_path / "b" / "paired.json")
        brain_b_store.add("milo-1", token_b)

        server = make_server(tmp_path)
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws_a:
                sock_a = MiloSocket(ws_a)
                await brain_handshake(sock_a, "brain-a", "a", "small", brain_a_store)
                await wait_until(lambda: server.connected_brain is not None)

                with pytest.raises((HandshakeError, websockets.ConnectionClosed, OSError)):
                    async with websockets.connect(f"ws://127.0.0.1:{port}") as ws_b:
                        sock_b = MiloSocket(ws_b)
                        await brain_handshake(sock_b, "brain-b", "b", "small", brain_b_store)
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())


def test_configured_mcp_port_travels_to_the_brain(tmp_path, paired_stores):
    # mcp_url itself (host + this port) is computed brain-side from the
    # connection's remote address -- see brain/milo_brain/net/connector.py --
    # since the brain is now the one dialing out and can read the robot's
    # address off its own client socket. RobotServer only needs to get the
    # raw port into the handshake correctly.
    _, brain_store = paired_stores

    async def main():
        cfg = BridgeConfig(
            robot_id="milo-1", robot_name="milo", data_dir=str(tmp_path / "robot"),
            robot_ws_port=0, mcp_port=8766,
        )
        server = RobotServer(cfg, display=FakeDisplay(), runner=None, advertiser=NullAdvertiser())
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                peer = await brain_handshake(sock, "brain-test", "desk", "large", brain_store)
                assert peer.mcp_port == 8766
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())
