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
            self._start_task()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        if not self._subs and self._task is not None:
            self._task.cancel()
            # Do NOT null self._task here. cancel() only *schedules*
            # cancellation - the task may still be running (e.g. mid-await
            # in a blocking-in-thread camera/audio call) for a while after
            # this returns. If we nulled self._task now, `active` would
            # report False while a reader is still alive, and a rapid
            # subscribe() arriving in that window would spawn a *second*
            # concurrent reader of the same physical device. `_on_task_done`
            # clears self._task once the task has genuinely finished.

    def _start_task(self) -> None:
        self._task = asyncio.ensure_future(self._run())
        self._task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task: asyncio.Task) -> None:
        if self._task is task:
            self._task = None
        # If the task that just finished was cancelled (i.e. unsubscribe()
        # emptied `_subs` and requested cancellation) but a new subscriber
        # arrived *before* the cancellation actually unwound, subscribe()
        # saw `active` still True (the old task wasn't done yet) and only
        # re-added to `_subs` without starting a reader. Now that the old
        # reader has truly finished, start a fresh one for those pending
        # subscribers. This deliberately does NOT fire for a *natural*
        # death (an uncaught driver exception, or generator exhaustion) -
        # in that case `task.cancelled()` is False, so we leave existing
        # subscribers waiting until a fresh subscribe() call notices
        # `active is False` and starts a new reader (see `active`/`_run`).
        if task.cancelled() and self._subs and self._task is None:
            self._start_task()

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
