"""IMX219 camera: MJPEG frames via picamera2 (installed from apt on the Pi).

A ``frame_source`` callable can be injected for tests; on hardware,
``CameraStreamer.from_hardware()`` builds the picamera2 pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

WIDTH, HEIGHT = 640, 480
DEFAULT_FPS = 15


class CameraStreamer:
    def __init__(self, frame_source: Callable[[], bytes], fps: int = DEFAULT_FPS):
        self._frame_source = frame_source
        self.fps = fps

    @classmethod
    def from_hardware(cls, fps: int = DEFAULT_FPS) -> "CameraStreamer":
        import io

        from picamera2 import Picamera2  # type: ignore

        cam = Picamera2()
        cam.configure(
            cam.create_video_configuration(main={"size": (WIDTH, HEIGHT), "format": "RGB888"})
        )
        cam.start()

        def grab() -> bytes:
            buf = io.BytesIO()
            cam.capture_file(buf, format="jpeg")
            return buf.getvalue()

        return cls(grab, fps=fps)

    async def frames(self) -> AsyncIterator[bytes]:
        """Yields JPEG frames, paced to ``fps``; capture runs in a worker thread."""
        interval = 1.0 / self.fps
        loop = asyncio.get_running_loop()
        while True:
            started = loop.time()
            frame = await asyncio.to_thread(self._frame_source)
            yield frame
            elapsed = loop.time() - started
            if elapsed < interval:
                await asyncio.sleep(interval - elapsed)
