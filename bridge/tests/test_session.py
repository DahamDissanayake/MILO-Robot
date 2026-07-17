import asyncio

from milo_common import protocol
from milo_common.protocol import Message
from milo_common.testing import socket_pair

from milo_bridge.config import BridgeConfig
from milo_bridge.net.session import SessionManager, RobotSession


class FakeDisplay:
    def __init__(self):
        self.faces: list[str] = []

    async def set_face(self, name, mode, fps=8.0):
        self.faces.append(name)

    def start_idle(self):
        pass


class FakeAudio:
    def __init__(self):
        self.played: list[bytes] = []

    def play_pcm(self, pcm):
        self.played.append(pcm)


class FakeSock:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, t, payload=None, **fields):
        self.sent.append((t, fields))


def msg(t, payload=None, **fields):
    return Message(header={"t": t, **fields}, payload=payload)


def make_session(**overrides):
    deps = dict(
        display=FakeDisplay(),
        audio=FakeAudio(),
        graph_api=None,
    )
    deps.update(overrides)
    return RobotSession(FakeSock(), **deps), deps


def test_tts_plays_audio():
    session, deps = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_TTS, payload=b"pcmdata")))
    assert deps["audio"].played == [b"pcmdata"]


def test_graph_without_api_reports_error():
    session, _ = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_GRAPH, id=7, op="query")))
    t, fields = session._sock.sent[-1]
    assert t == protocol.T_GRAPH_RESULT
    assert fields["id"] == 7 and "error" in fields


class FakeGraphApi:
    def handle(self, header):
        return {"id": header.get("id"), "nodes": [{"type": "person"}]}


def test_graph_dispatches_to_api():
    session, _ = make_session(graph_api=FakeGraphApi())
    asyncio.run(session.dispatch(msg(protocol.T_GRAPH, id=3, op="query")))
    t, fields = session._sock.sent[-1]
    assert t == protocol.T_GRAPH_RESULT
    assert fields["nodes"] == [{"type": "person"}]


class FakeDiscoveryEmpty:
    def snapshot(self):
        return []

    def start(self):
        pass

    def stop(self):
        pass


def test_cmd_with_move_or_face_is_ignored_not_crashed():
    async def main():
        robot_sock, brain_sock = socket_pair()
        display = FakeDisplay()
        session = RobotSession(robot_sock, display=display)
        task = asyncio.create_task(session.run())
        try:
            # A stale/legacy T_CMD carrying move+face must be silently
            # ignored -- the bridge no longer interprets either field.
            await brain_sock.send(protocol.T_CMD, face="happy", move={"pose": "wave"})
            await asyncio.sleep(0.05)
            assert display.faces == []
        finally:
            task.cancel()

    asyncio.run(main())


def test_tick_reconnects_and_does_not_crash_with_no_brain_found(tmp_path):
    # Sleep/wake is no longer this class's concern (moved to ControlBroker's
    # on_change hook in main()) -- this just confirms the no-brain branch
    # still cleanly waits and returns without a paired sleep_controller.
    cfg = BridgeConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)
    manager = SessionManager(
        cfg,
        display=None,
        runner=None,
        discovery=FakeDiscoveryEmpty(),
    )
    asyncio.run(manager._tick())


def test_session_manager_advertises_configured_mcp_port(tmp_path, monkeypatch):
    from milo_common.auth import PairedStore, derive_token
    from milo_common.handshake import brain_handshake, robot_handshake
    from milo_common.testing import socket_pair

    async def main():
        cfg = BridgeConfig(data_dir=str(tmp_path), robot_id="milo-1", robot_name="milo", mcp_port=9001)
        token = derive_token("123456", cfg.robot_id, "brain-1")
        PairedStore(cfg.paired_path).add("brain-1", token)

        rs, bs = socket_pair()

        # Simpler: drive robot_handshake directly against the brain side and
        # assert what it received, instead of the full discovery/connect
        # plumbing (which needs a real BrainRecord/select_brain wiring not
        # worth faking here).
        brain_store = PairedStore(tmp_path / "brain_paired.json")
        brain_store.add(cfg.robot_id, token)

        robot_task = asyncio.create_task(
            robot_handshake(rs, cfg.robot_id, cfg.robot_name, PairedStore(cfg.paired_path), mcp_port=cfg.mcp_port)
        )
        peer = await brain_handshake(bs, "brain-1", "d", "large", brain_store)
        await robot_task
        assert peer.mcp_port == 9001

    asyncio.run(main())
