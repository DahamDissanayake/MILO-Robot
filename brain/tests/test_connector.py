import asyncio

from milo_common.auth import PairedStore, derive_token
from milo_common.handshake import robot_handshake
from milo_common.protocol import MiloSocket
from milo_common.testing import FakeWebSocket

from milo_brain.config import BrainConfig
from milo_brain.net.connector import RobotConnectorManager, _drop_backoff_seconds
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


class _RaisingConnectCM:
    """Mimics websockets.connect(url) failing before a socket is ever
    obtained -- e.g. a DNS lookup (gaierror) or refused connection."""

    def __init__(self, exc: Exception):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *exc):
        return False


def test_drop_backoff_seconds_grows_and_caps():
    assert _drop_backoff_seconds(1) == 1
    assert _drop_backoff_seconds(2) == 2
    assert _drop_backoff_seconds(3) == 4
    assert _drop_backoff_seconds(4) == 8
    assert _drop_backoff_seconds(6) == 30  # 2**5=32, capped at 30
    assert _drop_backoff_seconds(10) == 30


def test_tick_waits_when_nothing_discovered(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)

    async def handler(sock, peer):
        raise AssertionError("must never be reached -- nothing was discovered")

    connector = RobotConnectorManager(
        cfg, session_handler=handler, discovery=FakeDiscoveryEmpty(),
    )
    asyncio.run(connector._tick())
    assert connector.connected_robot is None
    assert connector.link_state == "idle"


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
        assert connector.link_state == "idle"
        # last_connected survives the session ending -- it's what
        # request_reconnect() redials, not "currently connected".
        assert connector.last_connected == ("10.0.0.9", 8765)

    asyncio.run(main())


def test_consecutive_drops_counts_up_on_repeated_connect_failures(tmp_path):
    # A gaierror (DNS blip) or any other failure to even obtain a socket
    # should count as a drop and back off -- not hot-loop retrying.
    cfg = BrainConfig(data_dir=str(tmp_path))
    discovery = FakeDiscoveryWith(
        [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765, pairing=True)]
    )
    connector = RobotConnectorManager(
        cfg, request_pin=lambda name: None, session_handler=lambda sock, peer: None,
        discovery=discovery,
        connect=lambda url: _RaisingConnectCM(OSError("[Errno 11001] getaddrinfo failed")),
    )
    assert connector.consecutive_drops == 0
    asyncio.run(connector._tick())  # backoff is 1s on the first drop
    assert connector.consecutive_drops == 1
    assert connector.link_state == "retrying"
    assert "getaddrinfo failed" in connector.last_error


def test_consecutive_drops_resets_after_a_successful_connect(tmp_path):
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        token = derive_token("123456", "milo-1", "brain-1")
        PairedStore(cfg.paired_path).add("milo-1", token)
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        robot_store.add("brain-1", token)

        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot

        async def handler(sock, peer):
            pass

        discovery = FakeDiscoveryWith(
            [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765)]
        )
        connector = RobotConnectorManager(
            cfg, session_handler=handler, discovery=discovery,
            connect=lambda url: _ConnectCM(raw_brain),
        )
        connector.consecutive_drops = 3  # simulate a prior run of failures

        robot_task = asyncio.create_task(
            robot_handshake(MiloSocket(raw_robot), "milo-1", "milo", robot_store, mcp_port=0)
        )
        await connector._tick()
        await robot_task

        assert connector.consecutive_drops == 0
        assert connector.link_state == "idle"

    asyncio.run(main())


def test_tick_shows_connecting_then_handshaking_before_the_session_starts(tmp_path):
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        token = derive_token("123456", "milo-1", "brain-1")
        PairedStore(cfg.paired_path).add("milo-1", token)
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        robot_store.add("brain-1", token)

        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot

        async def handler(sock, peer):
            pass

        discovery = FakeDiscoveryWith(
            [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765)]
        )
        connector = RobotConnectorManager(
            cfg, session_handler=handler, discovery=discovery,
            connect=lambda url: _ConnectCM(raw_brain),
        )

        assert connector.link_state == "idle"
        tick_task = asyncio.create_task(connector._tick())
        await asyncio.sleep(0)  # let _tick() start; handshake hasn't completed yet
        assert connector.link_state in ("connecting", "handshaking")
        assert connector.link_target == ("10.0.0.9", 8765)

        robot_task = asyncio.create_task(
            robot_handshake(MiloSocket(raw_robot), "milo-1", "milo", robot_store, mcp_port=0)
        )
        await tick_task
        await robot_task

        assert connector.link_state == "idle"  # handler returned immediately -> session ended

    asyncio.run(main())


def test_tick_sets_idle_and_clears_target_when_nothing_is_discovered(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)

    async def handler(sock, peer):
        raise AssertionError("must never be reached -- nothing was discovered")

    connector = RobotConnectorManager(
        cfg, session_handler=handler, discovery=FakeDiscoveryEmpty(),
    )
    connector.link_state = "retrying"
    connector.link_target = ("10.0.0.9", 8765)

    asyncio.run(connector._tick())

    assert connector.link_state == "idle"
    assert connector.link_target is None


def test_handshake_failure_sets_idle_with_last_error(tmp_path):
    from milo_common import protocol

    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)
    discovery = FakeDiscoveryWith(
        [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765, pairing=True)]
    )
    # brain_handshake() always reads the robot's hello first (_expect at the
    # top of handshake.py); pre-seeding an "error" frame instead makes
    # _expect raise HandshakeError immediately instead of hanging on recv().
    raw_brain = FakeWebSocket()
    raw_brain.outbox.put_nowait(protocol.encode_header(protocol.T_ERROR, code="bad_pin"))
    connector = RobotConnectorManager(
        cfg, request_pin=lambda name: None, session_handler=lambda sock, peer: None,
        discovery=discovery, connect=lambda url: _ConnectCM(raw_brain),
    )

    asyncio.run(connector._tick())

    assert connector.link_state == "idle"
    assert connector.last_error is not None and "handshake failed" in connector.last_error


def test_request_reconnect_is_a_noop_before_any_connection(tmp_path):
    cfg = BrainConfig(data_dir=str(tmp_path))
    connector = RobotConnectorManager(
        cfg, session_handler=lambda sock, peer: None, discovery=FakeDiscoveryEmpty(),
    )
    assert connector.request_reconnect() is False
    assert connector._manual_host_target is None


def test_request_reconnect_redials_the_last_connected_robot(tmp_path):
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        token = derive_token("123456", "milo-1", "brain-1")
        PairedStore(cfg.paired_path).add("milo-1", token)
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")
        robot_store.add("brain-1", token)

        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot

        async def handler(sock, peer):
            pass

        discovery = FakeDiscoveryWith(
            [RobotRecord(robot_id="milo-1", name="milo", host="10.0.0.9", port=8765)]
        )
        connector = RobotConnectorManager(
            cfg, session_handler=handler, discovery=discovery,
            connect=lambda url: _ConnectCM(raw_brain),
        )
        robot_task = asyncio.create_task(
            robot_handshake(MiloSocket(raw_robot), "milo-1", "milo", robot_store, mcp_port=0)
        )
        await connector._tick()
        await robot_task

        assert connector.request_reconnect() is True
        assert connector._manual_host_target == ("10.0.0.9", 8765)

    asyncio.run(main())


def test_request_reconnect_wakes_a_tick_that_is_waiting_between_retries(tmp_path):
    # Prove the manual reconnect doesn't just get queued behind a long
    # reconnect_seconds wait -- it should cut it short instead.
    cfg = BrainConfig(data_dir=str(tmp_path), reconnect_seconds=30.0)

    async def handler(sock, peer):
        raise AssertionError("must never be reached -- discovery is empty")

    connector = RobotConnectorManager(
        cfg, session_handler=handler, discovery=FakeDiscoveryEmpty(),
    )
    connector.last_connected = ("10.0.0.9", 8765)

    async def main():
        tick_task = asyncio.create_task(connector._tick())
        await asyncio.sleep(0.05)  # let _tick() settle into its wait
        assert not tick_task.done()
        connector.request_reconnect()
        await asyncio.wait_for(tick_task, timeout=1.0)  # would time out at 30s if not woken

    asyncio.run(main())


def test_manual_ip_connect_bypasses_discovery_and_pairs(tmp_path):
    # The whole point: this must work with an *empty* discovery snapshot
    # (mDNS doesn't reach this network) and an unpaired robot -- pairing
    # is always offered for a manual IP target since we don't know the
    # robot's identity/paired status until the handshake's T_HELLO.
    async def main():
        cfg = BrainConfig(brain_id="brain-1", name="d", tier="large", data_dir=str(tmp_path))
        robot_store = PairedStore(tmp_path / "robot" / "paired.json")

        raw_robot, raw_brain = FakeWebSocket(), FakeWebSocket()
        raw_robot.peer, raw_brain.peer = raw_brain, raw_robot

        pin = "1234"

        async def request_pin(_robot_name: str) -> str:
            return pin

        received: dict = {}

        async def handler(sock, peer):
            received["peer"] = peer

        connector = RobotConnectorManager(
            cfg, request_pin=request_pin, session_handler=handler,
            discovery=FakeDiscoveryEmpty(), connect=lambda url: _ConnectCM(raw_brain),
        )
        connector.request_manual_ip_connect("10.0.0.9", 8765)
        assert connector._manual_host_target == ("10.0.0.9", 8765)

        robot_task = asyncio.create_task(
            robot_handshake(MiloSocket(raw_robot), "milo-1", "milo", robot_store, pending_pin=pin)
        )
        await connector._tick()
        await robot_task

        assert connector._manual_host_target is None  # one-shot, consumed
        assert received["peer"].id == "milo-1"
        assert robot_store.is_paired("brain-1")  # token persisted, like any other pairing

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
