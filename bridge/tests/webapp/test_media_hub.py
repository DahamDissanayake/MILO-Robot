import asyncio

import pytest

from milo_bridge.webapp.media_hub import Fanout, MediaHub
from .fakes import FakeAudio, FakeCamera


async def _drain(q, n):
    out = []
    for _ in range(n):
        out.append(await asyncio.wait_for(q.get(), 1.0))
    return out


async def test_two_subscribers_both_get_frames():
    async def gen():
        for i in range(3):
            yield f"f{i}".encode()
            await asyncio.sleep(0)

    fan = Fanout(gen, "video")
    q1, q2 = fan.subscribe(), fan.subscribe()
    # Drain concurrently: with maxsize=2 and 3 frames produced, a *sequential*
    # drain (fully read q1, then start on q2) is not just slow but
    # mathematically guaranteed to lose q2's oldest frame — every production
    # offers to both queues in the same step regardless of which is being
    # read, so an untouched q2 always overflows once a 3rd item lands. Both
    # subscribers only "get frames" together when read concurrently, which is
    # the real-world shape (two simultaneous viewers of the same stream).
    r1, r2 = await asyncio.gather(_drain(q1, 3), _drain(q2, 3))
    assert r1 == [b"f0", b"f1", b"f2"]
    assert r2 == [b"f0", b"f1", b"f2"]
    fan.unsubscribe(q1)
    fan.unsubscribe(q2)
    await asyncio.sleep(0)
    assert fan.active is False


async def test_slow_subscriber_drops_oldest_not_blocks():
    async def gen():
        for i in range(10):
            yield bytes([i])
            await asyncio.sleep(0)

    fan = Fanout(gen, "video")
    q = fan.subscribe()          # never drained while producing
    await asyncio.sleep(0.05)    # let the producer finish
    got = []
    while not q.empty():
        got.append(q.get_nowait())
    assert len(got) <= 2                     # maxsize=2: only newest kept
    assert got[-1] == bytes([9])             # newest frame survived
    fan.unsubscribe(q)


async def test_reader_stops_when_last_unsubscribes():
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
    q = fan.subscribe()
    await asyncio.wait_for(started.wait(), 1.0)
    fan.unsubscribe(q)
    await asyncio.wait_for(cancelled.wait(), 1.0)


async def test_media_hub_audio_level_callback():
    levels = []
    hub = MediaHub(camera=FakeCamera(), audio=FakeAudio(), on_audio_level=levels.append)
    q = hub.audio.subscribe()
    await asyncio.wait_for(q.get(), 1.0)
    assert levels, "on_audio_level should fire for every captured chunk"
    hub.audio.unsubscribe(q)


async def test_hub_none_drivers():
    hub = MediaHub(camera=None, audio=None)
    assert hub.video is None and hub.audio is None
