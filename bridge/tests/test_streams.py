import asyncio

from milo_bridge.net import streams
from milo_bridge.webapp.media_hub import Fanout


class FakeSock:
    def __init__(self):
        self.sent: list[tuple[str, bytes]] = []

    async def send(self, t, payload=None, **fields):
        self.sent.append((t, payload))


async def test_pump_video_sends_frames_from_fanout():
    async def gen():
        for i in range(3):
            yield f"f{i}".encode()
            await asyncio.sleep(0)

    fan = Fanout(gen, "video")
    sock = FakeSock()
    task = asyncio.create_task(streams.pump_video(sock, fan))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert [p for _, p in sock.sent] == [b"f0", b"f1", b"f2"]


async def test_pump_audio_sends_chunks_from_fanout():
    async def gen():
        for i in range(3):
            yield bytes([i])
            await asyncio.sleep(0)

    fan = Fanout(gen, "audio")
    sock = FakeSock()
    task = asyncio.create_task(streams.pump_audio(sock, fan))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert [p for _, p in sock.sent] == [bytes([0]), bytes([1]), bytes([2])]


async def test_pump_video_does_not_send_while_suspended():
    async def gen():
        for i in range(3):
            yield f"f{i}".encode()
            await asyncio.sleep(0)

    fan = Fanout(gen, "video")
    sock = FakeSock()
    active = {"on": False}
    task = asyncio.create_task(
        streams.pump_video(sock, fan, should_stream=lambda: active["on"])
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert sock.sent == []


async def test_pump_audio_does_not_send_while_suspended():
    async def gen():
        for i in range(3):
            yield bytes([i])
            await asyncio.sleep(0)

    fan = Fanout(gen, "audio")
    sock = FakeSock()
    active = {"on": False}
    task = asyncio.create_task(
        streams.pump_audio(sock, fan, should_stream=lambda: active["on"])
    )
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert sock.sent == []


async def test_pump_video_unsubscribes_when_cancelled():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def gen():
        started.set()
        try:
            while True:
                yield b"x"
                await asyncio.sleep(0.01)
        finally:
            cancelled.set()

    fan = Fanout(gen, "video")
    sock = FakeSock()
    task = asyncio.create_task(streams.pump_video(sock, fan))
    await asyncio.wait_for(started.wait(), 1.0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.wait_for(cancelled.wait(), 1.0)
    assert fan.active is False
