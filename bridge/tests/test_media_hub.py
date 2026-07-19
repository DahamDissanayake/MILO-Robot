import asyncio

from milo_bridge.webapp.media_hub import Fanout


def test_fanout_restarts_its_reader_after_a_natural_death():
    async def main():
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("camera hiccup")  # first reader dies mid-stream
                yield b""  # unreachable — makes this an async generator
            for i in range(3):
                yield bytes([i])

        # queue_size=3 (> the 3 frames the restarted factory yields) avoids
        # the fanout's drop-oldest-when-full eviction: the restarted
        # factory produces all 3 frames in one synchronous burst (no real
        # await between yields), so with the default queue_size=2 the
        # subscriber's q.get() wakes only after byte 0 has already been
        # evicted -- a scheduling artifact of this synthetic generator, not
        # a real driver, which naturally has await points between frames.
        fan = Fanout(factory, "test", restart_delay=0, queue_size=3)
        q = fan.subscribe()
        try:
            got = await asyncio.wait_for(q.get(), timeout=2.0)  # survives the death, gets a frame
        finally:
            fan.unsubscribe(q)
        return got, calls["n"]

    got, n = asyncio.run(main())
    assert got == bytes([0])
    assert n >= 2  # the reader was restarted with a fresh generator
