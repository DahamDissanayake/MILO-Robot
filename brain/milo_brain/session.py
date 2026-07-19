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
from .llm.token_rate import TokenRateTracker
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
        mcp=None,
        face_match_threshold: float = 0.45,
        conversation=None,
    ):
        self._sock = sock
        self._peer = peer
        self._vad = vad
        self._asr = asr
        self._vision = vision
        self._tts = tts
        self._agent = agent
        self._graph = graph
        self._mcp = mcp
        self._threshold = face_match_threshold
        self._conversation = conversation
        self._current_person: dict | None = None
        self._current_embedding_b64: str | None = None
        self._video_task: asyncio.Task | None = None
        self._segment_task: asyncio.Task | None = None

    def pipeline_status(self) -> dict[str, tuple[str, str | None]]:
        status: dict[str, tuple[str, str | None]] = {"vad": (self._vad.status, self._vad.error)}
        if self._mcp is not None:
            status["mcp"] = ("ready" if self._mcp.connected else "not_loaded", None)
        return status

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
        direction = direction_mod.classify(bearing)
        if direction != "center" and self._mcp is not None:
            await self._mcp.call_tool("run_pose", name=f"turn_{direction}")

        transcript = await asyncio.to_thread(self._asr.transcribe, segment.mono)
        log.info("heard (%.2f): %s", transcript.confidence, transcript.text)
        if not transcript.text or transcript.confidence < 0.3:
            return

        result = await self._agent.on_utterance(
            transcript.text, self._current_person, self._current_embedding_b64
        )
        if self._conversation is not None and result.reply:
            self._conversation.add(transcript.text, result.reply)
        await self._respond(result)

    async def _respond(self, result: AgentResult) -> None:
        if not result.reply:
            return
        current_face = "idle"
        if self._mcp is not None:
            status = await self._mcp.call_tool("get_status")
            current_face = status.get("current_face") or "idle"
            await self._mcp.call_tool("set_face", name=f"talk_{current_face}")

        pcm = await asyncio.to_thread(self._tts.synthesize, result.reply)
        for chunk in chunk_pcm(pcm):
            await self._sock.send(protocol.T_TTS, payload=chunk)

        if self._mcp is not None:
            await self._mcp.call_tool("set_face", name=current_face)


def _idle(task: asyncio.Task | None) -> bool:
    return task is None or task.done()


class CognitionSessionFactory:
    """Builds the production pipeline stack once and a session per robot."""

    def __init__(self, cfg: BrainConfig, rate_tracker: TokenRateTracker | None = None):
        from milo_common.auth import PairedStore

        from .llm.agent import OllamaClient
        from .pipelines.asr import WhisperAsr
        from .pipelines.tts import PiperTts
        from .pipelines.vision import FaceVision

        self._cfg = cfg
        self._store = PairedStore(cfg.paired_path)
        self._asr = WhisperAsr(cfg.whisper_model)
        self._vision = FaceVision(analysis_fps=cfg.vision_fps)
        from pathlib import Path
        self._tts = PiperTts(cfg.piper_voice, voices_dir=Path(cfg.data_dir) / "piper-voices")
        self._llm = OllamaClient(cfg.ollama_url, cfg.llm_model, rate_tracker=rate_tracker)
        self.current_session: RobotCognitionSession | None = None

        from .conversation import ConversationLog
        self.conversation = ConversationLog()

    def pipeline_status(self) -> dict[str, tuple[str, str | None]]:
        status: dict[str, tuple[str, str | None]] = {
            "asr": (self._asr.status, self._asr.error),
            "tts": (self._tts.status, self._tts.error),
            "vision": (self._vision.status, self._vision.error),
        }
        if self.current_session is not None:
            status.update(self.current_session.pipeline_status())
        return status

    def llm_status(self) -> tuple[str, str | None]:
        return (getattr(self._llm, "status", "unknown"), getattr(self._llm, "error", None))

    async def handle(self, sock: MiloSocket, peer: Peer) -> None:
        from .mcp_client import MiloMcpClient

        graph = GraphClient(sock)
        mcp = None
        if peer.mcp_url:
            token = self._store.token_for(peer.id)
            if token is not None:
                mcp = MiloMcpClient(peer.mcp_url, token=token.hex(), peer_id=self._cfg.brain_id)
        try:
            if mcp is not None:
                await mcp.connect()
            agent = CognitionAgent(self._llm, graph, mcp)
            session = RobotCognitionSession(
                sock,
                peer,
                vad=VadSegmenter(),
                asr=self._asr,
                vision=self._vision,
                tts=self._tts,
                agent=agent,
                graph=graph,
                mcp=mcp,
                face_match_threshold=self._cfg.face_match_threshold,
                conversation=self.conversation,
            )
            self.current_session = session
            try:
                await session.run()
            finally:
                self.current_session = None
        finally:
            if mcp is not None:
                await mcp.close()
