import asyncio

from milo_common.auth import PairedStore, derive_token
from milo_common.handshake import robot_handshake
from milo_common.protocol import MiloSocket
from milo_common.testing import FakeWebSocket

from milo_brain.config import BrainConfig
from milo_brain.net.connector import RobotConnectorManager
from milo_brain.net.discovery import RobotRecord


class FakeDiscoveryEmpty:
    def snapshot(self):
        return []

    def start(self):
        pass

    def stop(self):
        pass


class FakeDiscoveryWith:
    def __init__(self, records):
        self._records = records

    def snapshot(self):
        return self._records

    def start(self):
        pass

    def stop(self):
        pass


class _ConnectCM:
    """Mimics websockets.connect(url)'s async-context-manager shape."""

    def __init__(self, ws):
        self._ws = ws

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *exc):
        return False


def test_tick_waits_when_nothing_discovered(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)

    async def handler(sock, peer):
        raise AssertionError("must never be reached -- nothing was discovered")

    connector = RobotConnectorManager(
        cfg, session_handler=handler, discovery=FakeDiscoveryEmpty(),
    )
    asyncio.run(connector._tick())
    assert connector.connected_robot is None
    assert connector.link_state == "disconnected"


def test_tick_connects_to_a_selected_robot_and_runs_the_session_handler(tmp_path):
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        token = derive_token("123456", "milo-1", "brain-1")
        PairedStore(cfg.paired_path).add("milo-1", token)
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        robot_store.add("brain-1", token)

        # Raw fake sockets (not pre-wrapped in MiloSocket) since _tick()
        # does its own MiloSocket(ws) wrapping around whatever _connect()
        # hands it, exactly like the real websockets.connect() client.
        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot
        # Deliberately different from the discovery record's host, to prove
        # mcp_url is computed from the *socket's* remote address, not the
        # discovery record.
        raw_brain.remote_address = ("10.0.0.42", 8765)

        received: dict = {}

        async def handler(sock, peer):
            received["peer"] = peer
            received["sock"] = sock

        discovery = FakeDiscoveryWith(
            [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765)]
        )
        connector = RobotConnectorManager(
            cfg, session_handler=handler, discovery=discovery,
            connect=lambda url: _ConnectCM(raw_brain),
        )

        robot_task = asyncio.create_task(
            robot_handshake(
                MiloSocket(raw_robot), "milo-1", "milo", robot_store, mcp_port=9001
            )
        )
        await connector._tick()
        await robot_task

        assert received["peer"].id == "milo-1"
        assert received["peer"].mcp_port == 9001
        assert received["peer"].mcp_url == "http://10.0.0.42:9001"
        # The handler already returned by the time _tick() moves on, so the
        # connection is cleaned up.
        assert connector.connected_robot is None
        assert connector.link_state == "disconnected"

    asyncio.run(main())


def test_manual_target_is_consumed_after_one_tick(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)

    async def handler(sock, peer):
        raise AssertionError("must never be reached -- discovery is empty")

    connector = RobotConnectorManager(
        cfg, session_handler=handler, discovery=FakeDiscoveryEmpty(),
    )
    connector.request_manual_connect("milo-1")
    assert connector._manual_target == "milo-1"

    asyncio.run(connector._tick())

    # Consumed regardless of whether it actually found/connected to anything.
    assert connector._manual_target is None


def test_paired_ids_and_is_paired_reflect_the_store(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path))
    PairedStore(cfg.paired_path).add("milo-1", derive_token("123456", "milo-1", "b"))

    connector = RobotConnectorManager(
        cfg, session_handler=lambda sock, peer: None, discovery=FakeDiscoveryEmpty(),
    )
    assert connector.paired_ids() == ["milo-1"]
    assert connector.is_paired("milo-1") is True
    assert connector.is_paired("stranger") is False
