"""IMX219 camera: MJPEG frames via picamera2 (installed from apt on the Pi).

A ``frame_source`` callable can be injected for tests; on hardware,
``CameraStreamer.from_hardware()`` builds the picamera2 pipeline.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

# Both presets are scaled from the same pinned full-FOV raw stream (see
# from_hardware) -- "hd" is the sensor's native 2x2-binned resolution (no
# extra ISP downscale beyond the binning itself), "sd" is that same full
# frame scaled down further for lower bandwidth. Neither crops the sensor.
RESOLUTIONS: dict[str, tuple[int, int]] = {
    "sd": (640, 480),
    "hd": (1640, 1232),
}
DEFAULT_RESOLUTION = "sd"
# IMX219's native 2x2-binned full-FOV sensor mode. Pinning `raw` to this
# size stops picamera2's automatic mode selection from ever landing on a
# cropped sensor window when `main` asks for something smaller -- the ISP
# then always scales `main` down from the complete sensor image instead.
FULL_FOV_RAW_SIZE = (1640, 1232)
DEFAULT_FPS = 15


class CameraStreamer:
    def __init__(
        self,
        frame_source: Callable[[], bytes] | None,
        fps: int = DEFAULT_FPS,
        resolution: str = DEFAULT_RESOLUTION,
    ):
        self._frame_source = frame_source
        self.fps = fps
        self.resolution = resolution
        self._pending_resolution: str | None = None

    def set_resolution(self, name: str) -> None:
        # Resolution is a single shared-device setting, not a per-viewer
        # preference: this CameraStreamer wraps one physical camera whose
        # frames are fanned out (MediaHub's Fanout(camera.frames)) to every
        # connected web client AND the brain's own vision pipeline
        # (net/streams.py's pump_video) simultaneously. Switching it here
        # changes what every one of those consumers receives next, not just
        # the caller that requested the switch, and briefly stalls the
        # shared capture thread while picamera2 reconfigures.
        if name not in RESOLUTIONS:
            raise ValueError(f"unknown resolution {name!r}")
        self._pending_resolution = name
        # `.resolution` reflects the current/requested value immediately;
        # `_pending_resolution` separately drives the actual picamera2
        # reconfiguration, which must happen on the capture thread inside
        # grab()/frames() rather than here.
        self.resolution = name

    @classmethod
    def from_hardware(cls, fps: int = DEFAULT_FPS, resolution: str = DEFAULT_RESOLUTION) -> "CameraStreamer":
        import io

        from picamera2 import Picamera2  # type: ignore

        cam = Picamera2()

        def _configure(name: str) -> None:
            w, h = RESOLUTIONS[name]
            cam.stop()
            cam.configure(cam.create_video_configuration(
                main={"size": (w, h), "format": "RGB888"},
                raw={"size": FULL_FOV_RAW_SIZE},
            ))
            cam.start()

        _configure(resolution)

        # Two-phase construction: build the streamer first so `grab` can
        # close over it (to read/clear `_pending_resolution` and update
        # `.resolution`), then attach the real frame_source.
        streamer = cls(frame_source=None, fps=fps, resolution=resolution)

        def grab() -> bytes:
            if streamer._pending_resolution is not None:
                name, streamer._pending_resolution = streamer._pending_resolution, None
                _configure(name)
                streamer.resolution = name
            buf = io.BytesIO()
            cam.capture_file(buf, format="jpeg")
            return buf.getvalue()

        streamer._frame_source = grab
        return streamer

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
