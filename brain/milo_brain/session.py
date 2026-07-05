"""Per-robot cognition session: routes stream frames through the pipelines.

    audio frames -> VAD -> (direction, ASR) ┐
    video frames -> vision -> match_face ───┼-> CognitionAgent -> tts + cmd + graph
    graph_result frames -> GraphClient ─────┘

All pipeline objects are injectable; production wiring lives in
CognitionSessionFactory, tests pass fakes.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import numpy as np

from milo_common import protocol
from milo_common.handshake import Peer
from milo_common.protocol import MiloSocket

from .config import BrainConfig
from .llm.agent import AgentResult, CognitionAgent, OllamaClient
from .pipelines import direction as direction_mod
from .pipelines.tts import chunk_pcm
from .pipelines.vad import VadSegmenter

log = logging.getLogger(__name__)


class GraphClient:
    """Async request/response over the graph frames, correlated by id."""

    def __init__(self, sock: MiloSocket):
        self._sock = sock
        self._next_id = 0
        self._pending: dict[int, asyncio.Future] = {}

    async def call(self, op: str, timeout: float = 10.0, **kwargs) -> dict:
        self._next_id += 1
        req_id = self._next_id
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await self._sock.send(protocol.T_GRAPH, op=op, id=req_id, **kwargs)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(req_id, None)

    def deliver(self, header: dict) -> None:
        future = self._pending.get(header.get("id"))
        if future is not None and not future.done():
            future.set_result(header)


class RobotCognitionSession:
    def __init__(
        self,
        sock: MiloSocket,
        peer: Peer,
        *,
        vad: VadSegmenter,
        asr,
        vision,
        tts,
        agent: CognitionAgent,
        graph: GraphClient,
        face_match_threshold: float = 0.45,
    ):
        self._sock = sock
        self._peer = peer
        self._vad = vad
        self._asr = asr
        self._vision = vision
        self._tts = tts
        self._agent = agent
        self._graph = graph
        self._threshold = face_match_threshold
        self._current_person: dict | None = None
        self._current_embedding_b64: str | None = None
        self._video_task: asyncio.Task | None = None
        self._segment_task: asyncio.Task | None = None

    async def run(self) -> None:
        """Recv loop. Handlers that call back into the graph run as background
        tasks — awaiting them here would deadlock the graph_result routing."""
        log.info("cognition session started for %s", self._peer.name or self._peer.id)
        while True:
            msg = await self._sock.recv()
            if msg.t == protocol.T_AUDIO and msg.payload:
                segment = self._vad.push(msg.payload, msg.get("ts", 0.0))
                if segment is not None and _idle(self._segment_task):
                    self._segment_task = asyncio.create_task(self._segment_guarded(segment))
            elif msg.t == protocol.T_VIDEO and msg.payload:
                if _idle(self._video_task):
                    self._video_task = asyncio.create_task(self._on_video(msg))
            elif msg.t == protocol.T_GRAPH_RESULT:
                self._graph.deliver(dict(msg.header))

    # -- video --------------------------------------------------------------
    async def _on_video(self, msg: protocol.Message) -> None:
        # Runs as a background task (see run()); errors must not kill the session.
        faces = await asyncio.to_thread(self._vision.process_jpeg, msg.payload)
        if not faces:
            return
        embedding = faces[0].embedding  # largest/first face is the speaker heuristic
        embedding_b64 = base64.b64encode(embedding.astype(np.float32).tobytes()).decode()
        self._current_embedding_b64 = embedding_b64
        try:
            result = await self._graph.call(
                "match_face", embedding=embedding_b64, threshold=self._threshold
            )
        except Exception:
            log.exception("match_face failed")
            return
        self._current_person = result.get("match")

    # -- audio --------------------------------------------------------------
    async def _segment_guarded(self, segment) -> None:
        try:
            await self._handle_segment(segment)
        except Exception:
            log.exception("segment handling failed")

    async def _handle_segment(self, segment) -> None:
        bearing = direction_mod.estimate_bearing(segment.stereo)
        if direction_mod.classify(bearing) != "center":
            await self._sock.send(protocol.T_CMD, move={"turn": bearing})

        transcript = await asyncio.to_thread(self._asr.transcribe, segment.mono)
        log.info("heard (%.2f): %s", transcript.confidence, transcript.text)
        if not transcript.text or transcript.confidence < 0.3:
            return

        result = await self._agent.on_utterance(
            transcript.text, self._current_person, self._current_embedding_b64
        )
        await self._respond(result)

    async def _respond(self, result: AgentResult) -> None:
        if not result.reply:
            return
        cmd: dict = {"face": f"talk_{result.face}" if result.face != "idle" else "idle"}
        if result.move != "none":
            cmd["move"] = {"pose": result.move}
        await self._sock.send(protocol.T_CMD, **cmd)

        pcm = await asyncio.to_thread(self._tts.synthesize, result.reply)
        for chunk in chunk_pcm(pcm):
            await self._sock.send(protocol.T_TTS, payload=chunk)
        # Talking done: settle the face back to the non-talk variant.
        await self._sock.send(protocol.T_CMD, face=result.face)


def _idle(task: asyncio.Task | None) -> bool:
    return task is None or task.done()


class CognitionSessionFactory:
    """Builds the production pipeline stack once and a session per robot."""

    def __init__(self, cfg: BrainConfig):
        from .llm.agent import OllamaClient
        from .pipelines.asr import WhisperAsr
        from .pipelines.tts import PiperTts
        from .pipelines.vision import FaceVision

        self._cfg = cfg
        self._asr = WhisperAsr(cfg.whisper_model)
        self._vision = FaceVision(analysis_fps=cfg.vision_fps)
        self._tts = PiperTts(cfg.piper_voice)
        self._llm = OllamaClient(cfg.ollama_url, cfg.llm_model)

    async def handle(self, sock: MiloSocket, peer: Peer) -> None:
        graph = GraphClient(sock)
        session = RobotCognitionSession(
            sock,
            peer,
            vad=VadSegmenter(),
            asr=self._asr,
            vision=self._vision,
            tts=self._tts,
            agent=CognitionAgent(self._llm, graph),
            graph=graph,
            face_match_threshold=self._cfg.face_match_threshold,
        )
        await session.run()
