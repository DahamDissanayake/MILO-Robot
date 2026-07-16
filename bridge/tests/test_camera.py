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
    as the grab itself, not applied out-of-band. set_resolution() also
    updates .resolution optimistically (see test_set_resolution_updates_state),
    so this test asserts on _pending_resolution instead -- the flag that
    actually drives hardware reconfiguration -- to prove grab() is the thing
    consuming it, not some other path."""
    pending_seen_at_grab_time = []

    def frame_source():
        pending_seen_at_grab_time.append(streamer._pending_resolution)
        if streamer._pending_resolution is not None:
            name, streamer._pending_resolution = streamer._pending_resolution, None
            streamer.resolution = name
        return b"frame"

    streamer = CameraStreamer(frame_source, fps=1000)
    streamer.set_resolution("hd")
    assert streamer._pending_resolution == "hd"  # not yet consumed
    gen = streamer.frames()
    frame = await gen.__anext__()
    assert frame == b"frame"
    assert pending_seen_at_grab_time == ["hd"]  # was still pending when grab() ran
    assert streamer._pending_resolution is None  # consumed by grab()
    await gen.aclose()
