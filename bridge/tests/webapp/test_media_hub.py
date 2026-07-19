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


async def test_reader_death_auto_recovers_for_existing_subscribers():
    """When the driver generator dies mid-stream, the fanout retries with a
    fresh generator (bounded by restart_delay) so an ALREADY-connected
    subscriber's stream self-heals -- it keeps receiving frames without a new
    subscribe(). Previously a natural death stranded existing subscribers
    until some future subscribe() happened to restart the reader (the
    /stream/camera blank-forever bug); the reader now recovers on its own."""

    calls = 0

    async def gen():
        nonlocal calls
        calls += 1
        if calls == 1:
            yield b"f0"
            raise RuntimeError("driver exploded")  # first reader dies mid-stream
        while True:                                 # the restarted reader keeps going
            yield b"post"
            await asyncio.sleep(0.01)

    fan = Fanout(gen, "video", restart_delay=0)
    q = fan.subscribe()
    assert await asyncio.wait_for(q.get(), 1.0) == b"f0"
    # No new subscribe() -- the SAME queue keeps receiving after the death.
    assert await asyncio.wait_for(q.get(), 1.0) == b"post"
    assert calls >= 2            # the reader was restarted with a fresh generator
    assert fan.active is True    # a reader is running again, not stranded
    fan.unsubscribe(q)


async def test_rapid_unsubscribe_resubscribe_never_runs_two_readers():
    """Finding 2: cancel() only *schedules* cancellation. If a new
    subscribe() arrives while the old reader is still unwinding (e.g.
    blocked mid-await inside the driver), it must NOT spin up a second
    concurrent reader of the same physical device. This forces the exact
    interleaving deterministically via an asyncio.Event, no sleep-based
    timing guesses."""

    hold = asyncio.Event()
    calls = 0
    concurrent = 0
    max_concurrent = 0

    async def gen():
        nonlocal calls, concurrent, max_concurrent
        calls += 1
        concurrent += 1
        max_concurrent = max(max_concurrent, concurrent)
        try:
            await hold.wait()  # simulates being blocked mid-open
            while True:
                yield b"x"
                await asyncio.sleep(0)
        finally:
            concurrent -= 1

    fan = Fanout(gen, "video")
    q1 = fan.subscribe()
    # Let reader task A start and reach `await hold.wait()`.
    await asyncio.sleep(0)
    assert calls == 1

    fan.unsubscribe(q1)          # empties _subs -> schedules task A.cancel()
    q2 = fan.subscribe()         # arrives before task A has unwound

    # Task A's cancellation hasn't been delivered yet (no await happened
    # between cancel() and here), so the fanout must still consider itself
    # active and must NOT have started a second reader.
    assert fan.active is True
    assert calls == 1

    # Now let the event loop actually deliver the cancellation to task A
    # and, once it has genuinely finished, let the done-callback start a
    # replacement reader for q2.
    for _ in range(50):
        if calls >= 2:
            break
        await asyncio.sleep(0)
    assert calls == 2
    # The critical assertion: task A's generator must have fully unwound
    # (decrementing `concurrent`) before task B's generator started, so
    # at no point were two readers open at once.
    assert max_concurrent == 1

    hold.set()
    assert await asyncio.wait_for(q2.get(), 1.0) == b"x"
    fan.unsubscribe(q2)
