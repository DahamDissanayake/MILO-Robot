import asyncio

import pytest

from milo_common import protocol
from milo_common.protocol import Message, MiloSocket, ProtocolError


class FakeWebSocket:
    """In-memory duplex: frames sent on one end appear on the peer's recv queue."""

    def __init__(self):
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.peer: "FakeWebSocket | None" = None

    async def send(self, frame):
        assert self.peer is not None
        await self.peer.outbox.put(frame)

    async def recv(self):
        return await self.outbox.get()

    async def close(self, code=1000, reason=""):
        pass


def make_pair():
    a, b = FakeWebSocket(), FakeWebSocket()
    a.peer, b.peer = b, a
    return MiloSocket(a), MiloSocket(b)


def test_header_roundtrip():
    text = protocol.encode_header(protocol.T_CMD, seq=7, face="happy")
    header = protocol.decode_header(text)
    assert header == {"t": "cmd", "face": "happy", "seq": 7}


def test_decode_rejects_garbage():
    with pytest.raises(ProtocolError):
        protocol.decode_header("not json")
    with pytest.raises(ProtocolError):
        protocol.decode_header('{"no_type": 1}')


def test_binary_types_get_bin_flag():
    header = protocol.decode_header(protocol.encode_header(protocol.T_VIDEO))
    assert header["bin"] is True


async def _roundtrip_json():
    tx, rx = make_pair()
    await tx.send(protocol.T_CMD, face="happy", move={"turn": 0.5})
    msg = await rx.recv()
    assert isinstance(msg, Message)
    assert msg.t == protocol.T_CMD
    assert msg["face"] == "happy"
    assert msg.payload is None


def test_json_message_roundtrip():
    asyncio.run(_roundtrip_json())


async def _roundtrip_binary():
    tx, rx = make_pair()
    frame = b"\xff\xd8fakejpeg\xff\xd9"
    await tx.send(protocol.T_VIDEO, payload=frame, ts=123.0)
    msg = await rx.recv()
    assert msg.t == protocol.T_VIDEO
    assert msg.payload == frame
    assert msg["ts"] == 123.0


def test_binary_message_roundtrip():
    asyncio.run(_roundtrip_binary())


async def _payload_rules():
    tx, _ = make_pair()
    with pytest.raises(ProtocolError):
        await tx.send(protocol.T_AUDIO)  # binary type without payload
    with pytest.raises(ProtocolError):
        await tx.send(protocol.T_CMD, payload=b"nope")  # payload on JSON-only type


def test_payload_rules_enforced():
    asyncio.run(_payload_rules())


async def _desync_detected():
    tx, rx = make_pair()
    await tx._ws.send(b"raw bytes with no header")
    with pytest.raises(ProtocolError):
        await rx.recv()


def test_desync_detected():
    asyncio.run(_desync_detected())


def test_sequence_numbers_increment():
    async def run():
        tx, rx = make_pair()
        await tx.send(protocol.T_HELLO, role="robot")
        await tx.send(protocol.T_HELLO, role="robot")
        first = await rx.recv()
        second = await rx.recv()
        assert second["seq"] == first["seq"] + 1

    asyncio.run(run())


class _YieldingRecorderWs:
    """Records every frame handed to send(), yielding control once per call
    (like a real socket write would) -- reproduces the interleaving a real
    websocket exhibits when two coroutines write to it concurrently, so an
    unlocked two-step send() (header, then payload) can be caught racing."""

    def __init__(self):
        self.frames: list = []

    async def send(self, frame):
        await asyncio.sleep(0)
        self.frames.append(frame)


async def _concurrent_sends_stay_atomic():
    ws = _YieldingRecorderWs()
    sock = MiloSocket(ws)
    # bridge/milo_bridge/net/session.py runs pump_video/pump_audio as
    # separate tasks sending on the same MiloSocket concurrently -- this
    # reproduces that shape directly (real bug: a robot audio session
    # crashed with "header 'audio' promised a binary payload, got a text
    # frame" because a video/graph_result header interleaved between an
    # audio header and its payload).
    await asyncio.gather(
        sock.send(protocol.T_AUDIO, payload=b"AUDIO-PAYLOAD", ts=1.0),
        sock.send(protocol.T_VIDEO, payload=b"VIDEO-PAYLOAD", ts=2.0),
    )
    assert len(ws.frames) == 4
    # Each header must be immediately followed by ITS OWN payload -- never
    # another message's header sandwiched in between.
    for i in (0, 2):
        header = protocol.decode_header(ws.frames[i])
        payload = ws.frames[i + 1]
        assert isinstance(payload, (bytes, bytearray)), (
            f"frame {i + 1} should be {header['t']!r}'s binary payload, "
            f"got a text frame instead -- send() let another message's "
            f"header interleave in between"
        )
        expected_payload = b"AUDIO-PAYLOAD" if header["t"] == protocol.T_AUDIO else b"VIDEO-PAYLOAD"
        assert payload == expected_payload


def test_concurrent_sends_on_the_same_socket_never_interleave():
    asyncio.run(_concurrent_sends_stay_atomic())
