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
