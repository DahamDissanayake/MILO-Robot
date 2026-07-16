import pytest

from milo_bridge.drivers.camera import CameraStreamer, RESOLUTIONS


def test_default_resolution_is_sd():
    streamer = CameraStreamer(lambda: b"frame")
    assert streamer.resolution == "sd"
    assert RESOLUTIONS["sd"] == (640, 480)
    assert RESOLUTIONS["hd"] == (1640, 1232)


def test_set_resolution_updates_state():
    streamer = CameraStreamer(lambda: b"frame")
    streamer.set_resolution("hd")
    assert streamer.resolution == "hd"


def test_set_resolution_rejects_unknown_name():
    streamer = CameraStreamer(lambda: b"frame")
    with pytest.raises(ValueError):
        streamer.set_resolution("4k")
    assert streamer.resolution == "sd"  # unchanged after the rejected call


async def test_frames_applies_pending_resolution_before_next_grab():
    """Mimics from_hardware()'s grab() contract: a resolution switch must be
    picked up by the *next* frame_source call, on the same (worker) thread
    as the grab itself, not applied out-of-band."""
    calls = []

    def frame_source():
        if streamer._pending_resolution is not None:
            name, streamer._pending_resolution = streamer._pending_resolution, None
            streamer.resolution = name
        calls.append(streamer.resolution)
        return b"frame"

    streamer = CameraStreamer(frame_source, fps=1000)
    streamer.set_resolution("hd")
    gen = streamer.frames()
    frame = await gen.__anext__()
    assert frame == b"frame"
    assert calls == ["hd"]
    await gen.aclose()
