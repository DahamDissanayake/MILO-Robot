"""Single-reader fanout for camera and mic streams.

Drivers expose single-consumer async generators; the hub owns the one
reader task per driver and feeds every subscriber a bounded queue. Slow
subscribers lose old frames instead of stalling the pipeline.
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Callable

log = logging.getLogger(__name__)

QUEUE_SIZE = 2


class Fanout:
    def __init__(self, gen_factory: Callable[[], AsyncIterator[bytes]], name: str,
                 on_item: Callable[[bytes], None] | None = None):
        self._factory = gen_factory
        self._name = name
        self._on_item = on_item
        self._subs: set[asyncio.Queue] = set()
        self._task: asyncio.Task | None = None

    @property
    def active(self) -> bool:
        return self._task is not None and not self._task.done()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.add(q)
        if not self.active:
            self._task = asyncio.ensure_future(self._run())
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        if not self._subs and self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        try:
            async for item in self._factory():
                if self._on_item is not None:
                    self._on_item(item)
                for q in list(self._subs):
                    if q.full():
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    q.put_nowait(item)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s fanout reader died", self._name)


class MediaHub:
    def __init__(self, camera=None, audio=None,
                 on_audio_level: Callable[[float], None] | None = None):
        self.video = Fanout(camera.frames, "video") if camera is not None else None
        if audio is not None:
            def _level(chunk: bytes) -> None:
                if on_audio_level is not None:
                    from ..drivers.audio import rms
                    on_audio_level(rms(chunk))
            self.audio = Fanout(audio.capture_frames, "audio", on_item=_level)
        else:
            self.audio = None
