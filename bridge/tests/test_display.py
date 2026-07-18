import asyncio
import random
import threading
from pathlib import Path

import pytest
from PIL import Image

from milo_bridge.drivers import display as disp
from milo_bridge.drivers.display import AnimMode, FaceDisplay


class RecordingDevice:
    def __init__(self):
        self.shown: list[Image.Image] = []

    def display(self, image):
        self.shown.append(image)


class ThreadRecordingDevice:
    """Records which thread each display() write actually ran on -- the
    real luma.oled device.display() is a blocking I2C call (~100ms measured
    on hardware); this catches a regression back to calling it directly on
    the event loop thread, which would stall every other coroutine
    (servo ticks, IMU reads, web requests) for that entire duration."""

    def __init__(self):
        self.threads: list[str] = []

    def display(self, image):
        self.threads.append(threading.current_thread().name)


@pytest.fixture()
def assets(tmp_path: Path) -> Path:
    def save(name: str, shade: int):
        img = Image.new("1", (128, 64), 0)
        img.putpixel((shade, 0), 1)  # make frames distinguishable
        img.save(tmp_path / name)

    save("happy.png", 1)
    save("idle_1.png", 2)
    save("idle_2.png", 3)
    save("idle_blink.png", 4)
    return tmp_path


def test_blink_timing_matches_firmware_behavior():
    rng = random.Random(42)
    delays = [disp.next_blink_delay(rng) for _ in range(200)]
    assert all(3.0 <= d <= 7.0 for d in delays)
    doubles = sum(disp.should_double_blink(rng) for _ in range(2000))
    assert 0.25 < doubles / 2000 < 0.35


def test_load_face_frames_single_and_multi(assets: Path):
    assert len(disp.load_face_frames(assets, "happy")) == 1
    assert len(disp.load_face_frames(assets, "idle")) == 2
    assert disp.load_face_frames(assets, "missing") == []


def test_set_face_shows_first_frame(assets: Path):
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        await face.set_face("happy", AnimMode.ONCE)

    asyncio.run(run())
    assert face.current_face == "happy"
    assert len(device.shown) == 1


def test_show_runs_the_blocking_device_write_off_the_event_loop_thread(assets: Path):
    device = ThreadRecordingDevice()
    face = FaceDisplay(device, assets)
    caller_thread = threading.current_thread().name

    async def run():
        await face.set_face("happy", AnimMode.ONCE)

    asyncio.run(run())
    assert device.threads == [device.threads[0]]  # exactly one write, single-frame face
    assert device.threads[0] != caller_thread


def test_unknown_face_falls_back_to_idle(assets: Path):
    face = FaceDisplay(RecordingDevice(), assets)

    async def run():
        await face.set_face("stand")  # no art in the Sesame library

    asyncio.run(run())
    assert face.current_face == disp.FALLBACK_FACE


def test_unknown_face_without_fallback_raises(tmp_path: Path):
    face = FaceDisplay(RecordingDevice(), tmp_path)  # empty assets dir
    with pytest.raises(KeyError):
        asyncio.run(face.set_face("idle"))


def test_animation_advances_frames(assets: Path):
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        await face.set_face("idle", AnimMode.ONCE, fps=100)
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert len(device.shown) >= 2


def test_pin_render_fits_display():
    image = disp.render_pin_image("123456")
    assert image.size == (128, 64)
    assert image.getbbox() is not None  # something actually drawn


def test_show_pin_displays(assets: Path):
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        await face.show_pin("424242")

    asyncio.run(run())
    assert len(device.shown) == 1
    assert face.current_face is None


def test_idle_loop_blinks(assets: Path):
    device = RecordingDevice()
    rng = random.Random(1)
    face = FaceDisplay(device, assets, rng=rng)

    async def run():
        # Shrink blink delays so the test is fast.
        orig = disp.next_blink_delay
        disp.next_blink_delay = lambda r: 0.01
        try:
            face.start_idle()
            await asyncio.sleep(0.4)  # several blink cycles at the shrunken delay
            face.stop_idle()
        finally:
            disp.next_blink_delay = orig

    asyncio.run(run())
    assert len(device.shown) >= 3  # idle frames plus at least one blink


def test_render_status_image_fits_display():
    image = disp.render_status_image({"servos": True, "display": False})
    assert image.size == (128, 64)
    assert image.getbbox() is not None


def test_show_status_displays_then_holds(assets: Path):
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        await face.show_status({"servos": True}, seconds=0.01)

    asyncio.run(run())
    assert len(device.shown) == 1
    assert face.current_face is None


def test_start_idle_uses_custom_base_face(assets: Path):
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        face.start_idle(base_face="happy")
        await asyncio.sleep(0.05)
        face.stop_idle()

    asyncio.run(run())
    assert face.current_face == "happy"


def test_start_idle_is_a_no_op_while_already_running(assets: Path):
    """Regression guard for a real boot bug: PoseRunner.run() already calls
    start_idle() (default face) whenever a completed pose ends in stand, so
    a caller's own start_idle(base_face=...) called right after is silently
    a no-op unless the caller stops the already-running idle loop first."""
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        face.start_idle()  # simulates PoseRunner's own post-pose call
        await asyncio.sleep(0.01)
        face.start_idle(base_face="happy")  # a caller's own call, no stop_idle() first
        await asyncio.sleep(0.01)
        face.stop_idle()

    asyncio.run(run())
    assert face.current_face == "idle"  # proves the second call was a no-op


def test_stop_idle_then_start_idle_actually_overrides_base_face(assets: Path):
    device = RecordingDevice()
    face = FaceDisplay(device, assets)

    async def run():
        face.start_idle()
        await asyncio.sleep(0.01)
        face.stop_idle()
        face.start_idle(base_face="happy")
        await asyncio.sleep(0.01)
        face.stop_idle()

    asyncio.run(run())
    assert face.current_face == "happy"
