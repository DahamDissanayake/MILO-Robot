"""End-to-end over real websockets on localhost: pair, reconnect, stream, refuse."""

import asyncio
import contextlib

import pytest
import websockets

from milo_common import protocol
from milo_common.auth import PairedStore, derive_token
from milo_common.handshake import HandshakeError, Peer, robot_handshake
from milo_common.protocol import MiloSocket

from milo_brain.config import BrainConfig
from milo_brain.server import BrainServer


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


def make_server(tmp_path, handler, *, pairing=False, request_pin=None) -> BrainServer:
    cfg = BrainConfig(
        brain_id="brain-test", name="testbrain", tier="small", port=0,
        data_dir=str(tmp_path / "brain"),
    )
    server = BrainServer(cfg, handler=handler, request_pin=request_pin, advertiser=NullAdvertiser())
    if pairing:
        server.advertiser.pairing = True
    return server


async def serve(server: BrainServer):
    """Start on an ephemeral port; returns (ws_server, port)."""
    ws_server = await websockets.serve(server._on_connection, "127.0.0.1", 0)
    port = ws_server.sockets[0].getsockname()[1]
    return ws_server, port


@pytest.fixture()
def paired_stores(tmp_path):
    token = derive_token("123456", "milo-1", "brain-test")
    robot_store = PairedStore(tmp_path / "robot" / "paired.json")
    robot_store.add("brain-test", token)
    brain_store = PairedStore(tmp_path / "brain" / "paired.json")
    brain_store.add("milo-1", token)
    return robot_store, brain_store


def test_paired_robot_streams_video_to_brain(tmp_path, paired_stores):
    robot_store, _ = paired_stores
    received: list = []
    done = asyncio.Event()

    async def handler(sock: MiloSocket, peer: Peer):
        received.append(await sock.recv())
        done.set()

    async def main():
        server = make_server(tmp_path, handler)
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                peer = await robot_handshake(sock, "milo-1", "milo", robot_store)
                assert peer.id == "brain-test"
                await sock.send(protocol.T_VIDEO, payload=b"\xff\xd8jpeg\xff\xd9", ts=1.0)
                await asyncio.wait_for(done.wait(), timeout=5)
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())
    assert received[0].t == protocol.T_VIDEO
    assert received[0].payload == b"\xff\xd8jpeg\xff\xd9"


def test_unpaired_robot_is_refused(tmp_path):
    async def handler(sock, peer):  # must never be reached
        raise AssertionError("unpaired robot reached the handler")

    async def main():
        server = make_server(tmp_path, handler)  # pairing mode OFF
        ws_server, port = await serve(server)
        try:
            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                fresh_store = PairedStore(tmp_path / "fresh" / "paired.json")

                async def show_pin(pin):
                    pass

                with pytest.raises((HandshakeError, websockets.ConnectionClosed)):
                    await robot_handshake(sock, "stranger", "x", fresh_store, show_pin=show_pin)
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())


def test_full_pairing_flow_over_real_sockets(tmp_path):
    shown_pin: list[str] = []
    session_ok = asyncio.Event()

    async def handler(sock, peer):
        session_ok.set()
        with contextlib.suppress(Exception):
            await sock.recv()

    async def request_pin(robot_name: str):
        for _ in range(100):
            if shown_pin:
                return shown_pin[0]
            await asyncio.sleep(0.01)
        return None

    async def main():
        server = make_server(tmp_path, handler, pairing=True, request_pin=request_pin)
        ws_server, port = await serve(server)
        try:
            robot_store = PairedStore(tmp_path / "robot" / "paired.json")

            async def show_pin(pin: str):
                shown_pin.append(pin)

            async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
                sock = MiloSocket(ws)
                peer = await robot_handshake(sock, "milo-1", "milo", robot_store, show_pin=show_pin)
                assert peer.id == "brain-test"
                await asyncio.wait_for(session_ok.wait(), timeout=5)

            # Both sides persisted the token -> a plain reconnect authenticates.
            brain_store = PairedStore(tmp_path / "brain" / "paired.json")
            assert robot_store.token_for("brain-test") == brain_store.token_for("milo-1")
        finally:
            ws_server.close()
            await ws_server.wait_closed()

    asyncio.run(main())
