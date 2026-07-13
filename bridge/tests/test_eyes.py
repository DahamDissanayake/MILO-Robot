import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent / "assets" / "faces"))
from eyes import Eye, EyeFrame, WIDTH, HEIGHT, render_frame  # noqa: E402


def test_render_frame_size_and_mode():
    frame = EyeFrame(Eye(), Eye())
    image = render_frame(frame)
    assert image.size == (WIDTH, HEIGHT)
    assert image.mode == "1"


def test_render_frame_draws_something():
    frame = EyeFrame(Eye(), Eye())
    image = render_frame(frame)
    assert image.getbbox() is not None


def test_symmetric_idle_eyes_mirror_left_right():
    frame = EyeFrame(Eye(), Eye())
    image = render_frame(frame)
    bbox = image.getbbox()
    left, top, right, bottom = bbox
    mid = WIDTH / 2
    # An unrotated, symmetric pair is centered on the canvas.
    assert abs((left + right) / 2 - mid) < 2


def test_larger_eye_produces_larger_bbox_area():
    small = render_frame(EyeFrame(Eye(w=20, h=20), Eye(w=20, h=20)))
    big = render_frame(EyeFrame(Eye(w=40, h=40), Eye(w=40, h=40)))

    def bbox_area(image):
        l, t, r, b = image.getbbox()
        return (r - l) * (b - t)

    assert bbox_area(big) > bbox_area(small)


def test_tilt_rotates_eye_bbox():
    level = render_frame(EyeFrame(Eye(), Eye(tilt=0)))
    tilted = render_frame(EyeFrame(Eye(), Eye(tilt=30)))
    assert level.getbbox() != tilted.getbbox()


def test_vertical_offset_moves_eye_down():
    base = render_frame(EyeFrame(Eye(), Eye()))
    dropped = render_frame(EyeFrame(Eye(), Eye(dy=10)))
    # Bottom edge of the bbox should move down (larger y) when dy increases.
    assert dropped.getbbox()[3] > base.getbbox()[3]
