import asyncio

from milo_common import protocol
from milo_common.protocol import Message

from milo_bridge.net.session import RobotSession


class FakeRunner:
    def __init__(self):
        self.ran: list[tuple[str, int | None]] = []
        self.aborted = 0

    def abort(self):
        self.aborted += 1

    async def run(self, name, cycles=None):
        self.ran.append((name, cycles))
        return True


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


class FakeGait:
    def __init__(self):
        self.commands: list[tuple[float, float, float]] = []

    def set_velocity_command(self, vx, vy, yaw):
        self.commands.append((vx, vy, yaw))


class FakeSock:
    def __init__(self):
        self.sent: list[tuple[str, dict]] = []

    async def send(self, t, payload=None, **fields):
        self.sent.append((t, fields))


def msg(t, payload=None, **fields):
    return Message(header={"t": t, **fields}, payload=payload)


def make_session(**overrides):
    deps = dict(
        runner=FakeRunner(),
        display=FakeDisplay(),
        audio=FakeAudio(),
        gait=FakeGait(),
        graph_api=None,
    )
    deps.update(overrides)
    return RobotSession(FakeSock(), **deps), deps


def test_tts_plays_audio():
    session, deps = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_TTS, payload=b"pcmdata")))
    assert deps["audio"].played == [b"pcmdata"]


def test_cmd_face_talk_faces_loop():
    session, deps = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_CMD, face="talk_happy")))
    assert deps["display"].faces == ["talk_happy"]


def test_cmd_pose_runs_via_runner():
    async def run():
        session, deps = make_session()
        await session.dispatch(msg(protocol.T_CMD, move={"pose": "wave"}))
        await asyncio.sleep(0)
        return deps

    deps = asyncio.run(run())
    assert deps["runner"].ran == [("wave", None)]


def test_cmd_velocity_goes_to_gait_engine():
    session, deps = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"velocity": [0.1, 0.0, 15.0]})))
    assert deps["gait"].commands == [(0.1, 0.0, 15.0)]


def test_cmd_stop_aborts_pose_and_zeroes_gait():
    session, deps = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"stop": True})))
    assert deps["runner"].aborted == 1
    assert deps["gait"].commands == [(0.0, 0.0, 0.0)]


def test_cmd_turn_uses_gait_yaw():
    session, deps = make_session()
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"turn": -45})))
    assert deps["gait"].commands == [(0.0, 0.0, -30.0)]
    # Small bearings are ignored (already facing the speaker).
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"turn": 5})))
    assert len(deps["gait"].commands) == 1


def test_cmd_turn_without_gait_falls_back_to_pose():
    async def run():
        session, deps = make_session(gait=None)
        await session.dispatch(msg(protocol.T_CMD, move={"turn": 45}))
        await asyncio.sleep(0)
        return deps

    deps = asyncio.run(run())
    assert deps["runner"].ran == [("turn_right", 1)]


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


class FakeBroker:
    def __init__(self, allow: bool):
        self._allow = allow

    def allow_brain_motion(self):
        return self._allow


def test_brain_motion_dropped_while_broker_denies():
    session, deps = make_session(broker=FakeBroker(allow=False))
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"velocity": [0.1, 0.0, 15.0]})))
    assert deps["gait"].commands == []


def test_brain_pose_and_turn_dropped_while_broker_denies():
    async def run():
        session, deps = make_session(broker=FakeBroker(allow=False))
        await session.dispatch(msg(protocol.T_CMD, move={"pose": "wave"}))
        await session.dispatch(msg(protocol.T_CMD, move={"turn": -45}))
        await asyncio.sleep(0)
        return deps

    deps = asyncio.run(run())
    assert deps["runner"].ran == []
    assert deps["gait"].commands == []


def test_brain_motion_allowed_when_broker_permits():
    session, deps = make_session(broker=FakeBroker(allow=True))
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"velocity": [0.1, 0.0, 15.0]})))
    assert deps["gait"].commands == [(0.1, 0.0, 15.0)]


def test_stop_always_allowed_even_while_broker_denies():
    session, deps = make_session(broker=FakeBroker(allow=False))
    asyncio.run(session.dispatch(msg(protocol.T_CMD, move={"stop": True})))
    assert deps["runner"].aborted == 1
    assert deps["gait"].commands == [(0.0, 0.0, 0.0)]


def test_face_updates_are_not_gated_by_broker():
    session, deps = make_session(broker=FakeBroker(allow=False))
    asyncio.run(session.dispatch(msg(protocol.T_CMD, face="talk_happy")))
    assert deps["display"].faces == ["talk_happy"]
