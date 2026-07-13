import sys
from pathlib import Path

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


from eyes import EMOTIONS  # noqa: E402

EXPECTED_FACES = {
    "idle", "idle_blink", "happy", "sad", "angry", "love", "sleepy",
    "surprised", "confused", "thinking", "excited",
    "talk_happy", "talk_sad", "talk_angry", "talk_confused", "talk_love",
    "talk_sleepy", "talk_surprised", "talk_thinking", "talk_excited",
}


def test_emotions_has_exactly_the_expected_faces():
    assert set(EMOTIONS.keys()) == EXPECTED_FACES


def test_every_frame_list_is_nonempty_and_renders():
    for name, frames in EMOTIONS.items():
        assert len(frames) >= 1, name
        for frame in frames:
            image = render_frame(frame)
            assert image.size == (WIDTH, HEIGHT), name
            assert image.getbbox() is not None, name


def test_multi_frame_faces_have_more_than_one_frame():
    for name in ("idle_blink", "love", "thinking", "excited", "talk_happy"):
        assert len(EMOTIONS[name]) > 1, name


def test_single_frame_faces_have_exactly_one_frame():
    for name in ("idle", "happy", "sad", "angry", "sleepy", "surprised", "confused"):
        assert len(EMOTIONS[name]) == 1, name


def test_idle_blink_frames_close_then_reopen():
    heights = [max(f.left.h, f.right.h) for f in EMOTIONS["idle_blink"]]
    min_idx = heights.index(min(heights))
    assert 0 < min_idx < len(heights) - 1  # closes in the middle, not at an edge


def test_confused_is_asymmetric():
    frame = EMOTIONS["confused"][0]
    assert frame.left != frame.right
