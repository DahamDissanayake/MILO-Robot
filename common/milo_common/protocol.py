"""Wire protocol: one WebSocket, multiplexed JSON control frames + binary payloads.

Every logical message is a JSON text frame. Messages that carry bulk data
(video/audio/tts) set ``"bin": true`` in the header; the binary payload is sent
as the immediately following bytes frame. Headers carry ``seq`` so either side
can detect a lost pairing between header and payload and re-sync.

Message types (``"t"`` field):
    robot -> brain:  video, audio, graph_result, pair_begin, challenge, auth_ok
    brain -> robot:  tts, cmd, graph, pair_pin, auth
    either:          hello, error, ping, pong
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1

# Robot -> brain
T_VIDEO = "video"
T_AUDIO = "audio"
T_GRAPH_RESULT = "graph_result"
T_PAIR_BEGIN = "pair_begin"
T_CHALLENGE = "challenge"
T_AUTH_OK = "auth_ok"

# Brain -> robot
T_TTS = "tts"
T_CMD = "cmd"
T_GRAPH = "graph"
T_PAIR_PIN = "pair_pin"
T_AUTH = "auth"

# Either direction
T_HELLO = "hello"
T_ERROR = "error"

BINARY_TYPES = frozenset({T_VIDEO, T_AUDIO, T_TTS})

# Stream formats (locked by the design spec)
VIDEO_FORMAT = {"codec": "mjpeg", "width": 640, "height": 480, "fps": 15}
AUDIO_FORMAT = {"codec": "pcm_s16le", "rate": 16000, "channels": 2, "frame_ms": 20}
TTS_FORMAT = {"codec": "pcm_s16le", "rate": 16000, "channels": 1}


class ProtocolError(Exception):
    """Malformed or out-of-sequence frame."""


@dataclass(frozen=True)
class Message:
    """A decoded protocol message: JSON header plus optional binary payload."""

    header: dict[str, Any]
    payload: bytes | None = None

    @property
    def t(self) -> str:
        return self.header["t"]

    def __getitem__(self, key: str) -> Any:
        return self.header[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.header.get(key, default)


def encode_header(t: str, *, seq: int | None = None, **fields: Any) -> str:
    header: dict[str, Any] = {"t": t, **fields}
    if seq is not None:
        header["seq"] = seq
    if t in BINARY_TYPES:
        header["bin"] = True
    return json.dumps(header, separators=(",", ":"))


def decode_header(text: str) -> dict[str, Any]:
    try:
        header = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON frame: {exc}") from exc
    if not isinstance(header, dict) or "t" not in header:
        raise ProtocolError("frame is not a message header (missing 't')")
    return header


class MiloSocket:
    """Pairs JSON headers with their binary payloads over a websocket-like object.

    Works with any object exposing async ``send(str | bytes)`` and ``recv()``
    (both `websockets` client and server connections do).
    """

    def __init__(self, ws: Any):
        self._ws = ws
        self._seq = 0

    async def send(self, t: str, payload: bytes | None = None, **fields: Any) -> None:
        self._seq += 1
        wants_payload = t in BINARY_TYPES
        if wants_payload != (payload is not None):
            raise ProtocolError(
                f"message type {t!r} {'requires' if wants_payload else 'does not take'} a binary payload"
            )
        await self._ws.send(encode_header(t, seq=self._seq, **fields))
        if payload is not None:
            await self._ws.send(payload)

    async def recv(self) -> Message:
        frame = await self._ws.recv()
        if isinstance(frame, (bytes, bytearray)):
            # A bytes frame with no preceding header means we lost sync.
            raise ProtocolError("unexpected binary frame without a JSON header")
        header = decode_header(frame)
        payload = None
        if header.get("bin"):
            payload = await self._ws.recv()
            if not isinstance(payload, (bytes, bytearray)):
                raise ProtocolError(
                    f"header {header.get('t')!r} promised a binary payload, got a text frame"
                )
            payload = bytes(payload)
        return Message(header=header, payload=payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._ws.close(code, reason)
