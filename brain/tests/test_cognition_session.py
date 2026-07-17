"""End-to-end cognition session against fake pipelines and an in-memory socket."""

import asyncio

import numpy as np
import pytest

from milo_common import protocol
from milo_common.handshake import Peer
from milo_common.testing import socket_pair

from milo_brain.llm.agent import CognitionAgent
from milo_brain.pipelines.asr import Transcript
from milo_brain.pipelines.vad import VadSegmenter
from milo_brain.pipelines.vision import FaceObservation
from milo_brain.session import GraphClient, RobotCognitionSession

FS = 16_000
FRAME = FS * 20 // 1000


class FakeAsr:
    def transcribe(self, mono):
        return Transcript(text="hello milo", confidence=0.9)


class FakeVision:
    def __init__(self, match=True):
        self.match = match

    def process_jpeg(self, jpeg):
        return [FaceObservation(bbox=(0, 0, 1, 1), embedding=np.ones(512, np.float32))]


class FakeTts:
    def synthesize(self, text):
        return b"\x00\x01" * FRAME * 2  # two frames of "speech"


class FakeLlm:
    async def chat(self, system, messages, tools=None):
        return {"role": "assistant", "content": '{"reply": "Hey Daham!", "facts": []}'}


class FakeMcp:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.status = {"ok": True, "current_face": "happy"}

    async def list_tools(self):
        return []

    async def call_tool(self, tool_name, **arguments):
        # Parameter named tool_name, not name -- set_face takes a kwarg
        # literally called `name`, which would collide.
        self.calls.append((tool_name, arguments))
        if tool_name == "get_status":
            return self.status
        return {"ok": True}


def loud_frame(seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 8000, (FRAME, 2)).astype(np.int16).tobytes()


def quiet_frame():
    return np.zeros((FRAME, 2), dtype=np.int16).tobytes()


def energy_detector(mono):
    return float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) > 1000


def build_session(robot_side_answers, mcp=None):
    """Wires a session whose graph calls are answered by a scripted robot."""
    brain_sock, robot_sock = socket_pair()
    graph = GraphClient(brain_sock)
    mcp = mcp if mcp is not None else FakeMcp()
    session = RobotCognitionSession(
        brain_sock,
        Peer(id="milo-1", name="milo"),
        vad=VadSegmenter(is_speech=energy_detector, min_silence_ms=60, pre_roll_frames=2),
        asr=FakeAsr(),
        vision=FakeVision(),
        tts=FakeTts(),
        agent=CognitionAgent(FakeLlm(), graph, mcp),
        graph=graph,
        mcp=mcp,
    )

    async def robot(collected):
        """Answers graph calls like the bridge's GraphApi would; records cmd/tts."""
        while True:
            msg = await robot_sock.recv()
            if msg.t == protocol.T_GRAPH:
                op = msg.get("op")
                reply = robot_side_answers(op, dict(msg.header))
                await robot_sock.send(protocol.T_GRAPH_RESULT, id=msg.get("id"), **reply)
            else:
                collected.append(msg)

    return session, robot_sock, robot, mcp


def test_full_hearing_to_speaking_loop():
    def answers(op, header):
        if op == "match_face":
            return {"match": {"id": 1, "type": "person", "props": {"name": "Daham"}},
                    "similarity": 0.98}
        if op == "neighbors":
            return {"neighbors": []}
        if op == "recent_events":
            return {"nodes": []}
        return {}

    async def main():
        session, robot_sock, robot, mcp = build_session(answers)
        collected: list = []
        session_task = asyncio.create_task(session.run())
        robot_task = asyncio.create_task(robot(collected))
        try:
            await robot_sock.send(protocol.T_VIDEO, payload=b"jpegbytes", ts=0.0)
            await asyncio.sleep(0.05)
            t = 0.0
            for loud in [True] * 10 + [False] * 5:
                await robot_sock.send(
                    protocol.T_AUDIO, payload=loud_frame() if loud else quiet_frame(), ts=t
                )
                t += 0.02
            for _ in range(300):
                if any(name == "set_face" and kwargs.get("name") == "happy" for name, kwargs in mcp.calls):
                    break
                await asyncio.sleep(0.02)
        finally:
            session_task.cancel()
            robot_task.cancel()
        return mcp.calls

    calls = asyncio.run(main())
    assert protocol.T_TTS  # sanity: module import still valid
    set_face_calls = [kwargs["name"] for name, kwargs in calls if name == "set_face"]
    assert "talk_happy" in set_face_calls
    assert set_face_calls[-1] == "happy"  # settles back to the non-talk variant after speaking


def test_off_center_speech_calls_turn_via_mcp():
    def answers(op, header):
        if op == "match_face":
            return {"match": {"id": 1, "type": "person", "props": {"name": "Daham"}}, "similarity": 0.98}
        if op == "neighbors":
            return {"neighbors": []}
        if op == "recent_events":
            return {"nodes": []}
        return {}

    async def main():
        session, robot_sock, robot, mcp = build_session(answers)
        collected: list = []
        session_task = asyncio.create_task(session.run())
        robot_task = asyncio.create_task(robot(collected))
        try:
            await robot_sock.send(protocol.T_VIDEO, payload=b"jpegbytes", ts=0.0)
            await asyncio.sleep(0.05)
            # A hard-panned stereo burst -- clearly off-center -- drives the
            # direction-of-arrival reflex regardless of the exact bearing math.
            t = 0.0
            rng_frames = []
            import numpy as np
            for i in range(10):
                left = np.random.default_rng(i).normal(0, 8000, FRAME).astype(np.int16)
                right = np.zeros(FRAME, dtype=np.int16)  # all energy on the left channel
                rng_frames.append(np.stack([left, right], axis=1).astype(np.int16).tobytes())
            for i, frame in enumerate(rng_frames + [quiet_frame()] * 5):
                await robot_sock.send(protocol.T_AUDIO, payload=frame, ts=t)
                t += 0.02
            for _ in range(300):
                if any(name == "run_pose" for name, _ in mcp.calls):
                    break
                await asyncio.sleep(0.02)
        finally:
            session_task.cancel()
            robot_task.cancel()
        return mcp.calls

    calls = asyncio.run(main())
    turn_calls = [kwargs for name, kwargs in calls if name == "run_pose"]
    assert turn_calls, f"no run_pose call made: {calls}"
    assert turn_calls[0]["name"] in ("turn_left", "turn_right")
    assert "turn" not in [name for name, _ in calls], (
        "reflex must not call the open-ended turn tool -- it never calls stop()"
    )


def test_graph_client_correlates_concurrent_calls():
    async def main():
        brain_sock, robot_sock = socket_pair()
        client = GraphClient(brain_sock)

        async def responder():
            # Answer out of order on purpose.
            first = await robot_sock.recv()
            second = await robot_sock.recv()
            await robot_sock.send(protocol.T_GRAPH_RESULT, id=second.get("id"), value="B")
            await robot_sock.send(protocol.T_GRAPH_RESULT, id=first.get("id"), value="A")

        async def deliver():
            while True:
                msg = await brain_sock.recv()
                client.deliver(dict(msg.header))

        responder_task = asyncio.create_task(responder())
        deliver_task = asyncio.create_task(deliver())
        try:
            a, b = await asyncio.gather(client.call("query"), client.call("neighbors", node_id=1))
            assert a["value"] == "A" and b["value"] == "B"
        finally:
            responder_task.cancel()
            deliver_task.cancel()

    asyncio.run(main())


def test_graph_client_times_out_cleanly():
    async def main():
        brain_sock, _robot_sock = socket_pair()
        client = GraphClient(brain_sock)
        with pytest.raises(asyncio.TimeoutError):
            await client.call("query", timeout=0.05)
        assert client._pending == {}

    asyncio.run(main())


def test_factory_handle_closes_mcp_client_when_connect_fails(tmp_path, monkeypatch):
    """A MiloMcpClient whose connect() raises partway through must still be
    close()'d -- otherwise its AsyncExitStack (partially opened transport)
    leaks. handle() must guard connect() with the same try/finally that
    guards session.run()."""
    import milo_brain.mcp_client as mcp_client_mod
    from milo_common.auth import PairedStore
    from milo_brain.config import BrainConfig
    from milo_brain.session import CognitionSessionFactory

    class FakeFailingMcpClient:
        instances: list["FakeFailingMcpClient"] = []

        def __init__(self, base_url, token, peer_id):
            self.closed = False
            FakeFailingMcpClient.instances.append(self)

        async def connect(self):
            raise RuntimeError("transport opened but initialize() failed")

        async def close(self):
            self.closed = True

    monkeypatch.setattr(mcp_client_mod, "MiloMcpClient", FakeFailingMcpClient)

    cfg = BrainConfig(data_dir=str(tmp_path))
    store = PairedStore(cfg.paired_path)
    store.add("milo-1", token=b"\x01" * 16, name="milo")

    factory = CognitionSessionFactory.__new__(CognitionSessionFactory)
    factory._cfg = cfg
    factory._store = store
    # asr/vision/tts/llm are never touched on the connect-failure path
    # (it raises before agent/session construction), so leave them unset.

    peer = Peer(id="milo-1", name="milo", mcp_url="http://127.0.0.1:9/mcp")

    async def main():
        with pytest.raises(RuntimeError, match="initialize"):
            await factory.handle(sock=None, peer=peer)

    asyncio.run(main())

    assert len(FakeFailingMcpClient.instances) == 1
    assert FakeFailingMcpClient.instances[0].closed is True


def test_factory_wires_rate_tracker_into_the_ollama_client(tmp_path, monkeypatch):
    import milo_brain.pipelines.asr as asr_mod
    import milo_brain.pipelines.tts as tts_mod
    import milo_brain.pipelines.vision as vision_mod
    from milo_brain.config import BrainConfig
    from milo_brain.llm.token_rate import TokenRateTracker
    from milo_brain.session import CognitionSessionFactory

    monkeypatch.setattr(asr_mod, "WhisperAsr", lambda *a, **kw: object())
    monkeypatch.setattr(vision_mod, "FaceVision", lambda *a, **kw: object())
    monkeypatch.setattr(tts_mod, "PiperTts", lambda *a, **kw: object())

    cfg = BrainConfig(brain_id="b", name="n", tier="small", data_dir=str(tmp_path))
    tracker = TokenRateTracker()
    factory = CognitionSessionFactory(cfg, rate_tracker=tracker)
    assert factory._llm._rate_tracker is tracker
