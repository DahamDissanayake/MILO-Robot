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
    async def chat(self, system, messages):
        return '{"reply": "Hey Daham!", "face": "happy", "move": "none", "facts": []}'


def loud_frame(seed=0):
    rng = np.random.default_rng(seed)
    return rng.normal(0, 8000, (FRAME, 2)).astype(np.int16).tobytes()


def quiet_frame():
    return np.zeros((FRAME, 2), dtype=np.int16).tobytes()


def energy_detector(mono):
    return float(np.sqrt(np.mean(mono.astype(np.float64) ** 2))) > 1000


def build_session(robot_side_answers):
    """Wires a session whose graph calls are answered by a scripted robot."""
    brain_sock, robot_sock = socket_pair()
    graph = GraphClient(brain_sock)
    session = RobotCognitionSession(
        brain_sock,
        Peer(id="milo-1", name="milo"),
        vad=VadSegmenter(is_speech=energy_detector, min_silence_ms=60, pre_roll_frames=2),
        asr=FakeAsr(),
        vision=FakeVision(),
        tts=FakeTts(),
        agent=CognitionAgent(FakeLlm(), graph),
        graph=graph,
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

    return session, robot_sock, robot


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
        session, robot_sock, robot = build_session(answers)
        collected: list = []
        session_task = asyncio.create_task(session.run())
        robot_task = asyncio.create_task(robot(collected))
        try:
            # Robot sends a video frame (identity) then a speech burst.
            await robot_sock.send(protocol.T_VIDEO, payload=b"jpegbytes", ts=0.0)
            await asyncio.sleep(0.05)
            t = 0.0
            for loud in [True] * 10 + [False] * 5:
                await robot_sock.send(
                    protocol.T_AUDIO, payload=loud_frame() if loud else quiet_frame(), ts=t
                )
                t += 0.02
            for _ in range(300):
                # Wait for the final settle command (face without talk_ prefix).
                if any(
                    m.t == protocol.T_CMD and m.get("face") == "happy" for m in collected
                ):
                    break
                await asyncio.sleep(0.02)
        finally:
            session_task.cancel()
            robot_task.cancel()
        return collected

    collected = asyncio.run(main())
    types = [m.t for m in collected]
    assert protocol.T_CMD in types, f"no cmd sent: {types}"
    assert protocol.T_TTS in types, f"no tts sent: {types}"
    talk_cmd = next(m for m in collected if m.t == protocol.T_CMD and m.get("face"))
    assert talk_cmd["face"].startswith("talk_")
    # After speaking, the face settles to the non-talk variant.
    faces = [m.get("face") for m in collected if m.t == protocol.T_CMD and m.get("face")]
    assert faces[-1] == "happy"


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
