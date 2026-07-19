import asyncio

from milo_common import protocol
from milo_common.protocol import Message
from milo_common.testing import socket_pair

from milo_bridge.net.session import RobotSession


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


class FakeBroker:
    def __init__(self, allow: bool):
        self._allow = allow

    def allow_brain_motion(self):
        return self._allow


def test_tts_suspended_while_web_pilot_holds_control():
    session, deps = make_session(broker=FakeBroker(False))
    asyncio.run(session.dispatch(msg(protocol.T_TTS, payload=b"pcmdata")))
    assert deps["audio"].played == []


def test_tts_plays_when_broker_allows_brain_motion():
    session, deps = make_session(broker=FakeBroker(True))
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
