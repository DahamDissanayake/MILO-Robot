# EVE-Style Face Eyes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace MILO's kaomoji-style OLED face art with procedurally generated, EVE/Vector-style geometric eyes (rounded-rectangle "eye blocks" whose size/tilt/position convey emotion), driven by a small parameter table instead of hand-drawn bitmaps.

**Architecture:** A pure-rendering module (`bridge/assets/faces/eyes.py`) defines an `Eye` (per-eye shape params) and `EyeFrame` (a pair of eyes) dataclass, a `render_frame()` function that draws one 128x64 monochrome frame, and an `EMOTIONS` dict mapping face name -> list of frames (length >1 = animation). A CLI generator (`bridge/tools/generate_faces.py`) renders every entry to `bridge/assets/faces/<name>.png` / `<name>_<n>.png`, replacing the old kaomoji PNGs. `FaceDisplay` (`bridge/milo_bridge/drivers/display.py`) is untouched — it already loads PNGs by name and falls back to `idle` for names with no art.

**Tech Stack:** Python 3, Pillow (`PIL.Image`, `PIL.ImageDraw`), pytest.

## Global Constraints

- Canvas is 128x64, PIL mode `"1"` (1-bit monochrome) — matches the SSD1306 and `FaceDisplay`'s `.convert("1")` (display.py:56, 65).
- No pupils, eyebrows, or mouth — expression comes only from each eye's `w`, `h`, `radius`, `tilt`, `dx`, `dy`, and the pair's `gap`.
- Multi-frame animations follow the existing `<name>_<n>.png` convention (1-indexed) consumed by `load_face_frames` (display.py:52-65) — do not introduce a new naming scheme.
- `display.py`, `FaceDisplay`, and its tests are not modified.
- Emotion set is exactly: `idle`, `idle_blink`, `happy`, `sad`, `angry`, `love`, `sleepy`, `surprised`, `confused`, `thinking`, `excited`, and `talk_happy`, `talk_sad`, `talk_angry`, `talk_confused`, `talk_love`, `talk_sleepy`, `talk_surprised`, `talk_thinking`, `talk_excited`.
- Pose-linked art (`wave`, `dance_1/2`, `walk`, `crab`, `worm`, `swim`, `point_1-3`, `pushup`, `bow`, `shake`, `shrug`, `cute`, `freaky`, `dead_1-3`, `rest_1-3`) is deleted, not redesigned — those names have no generator entry and fall back to `idle` via the existing `FALLBACK_FACE` path.

---

### Task 1: Eye rendering engine

**Files:**
- Create: `bridge/assets/faces/eyes.py`
- Test: `bridge/tests/test_eyes.py`

**Interfaces:**
- Produces: `Eye` (frozen dataclass: `w: int = 32, h: int = 32, radius: int = 10, tilt: float = 0.0, dx: int = 0, dy: int = 0`), `EyeFrame` (frozen dataclass: `left: Eye, right: Eye, gap: int = 16`), `render_frame(frame: EyeFrame) -> PIL.Image.Image`, `WIDTH = 128`, `HEIGHT = 64` — all consumed by Task 2 (EMOTIONS table) and Task 3 (generator CLI).

- [ ] **Step 1: Write the failing tests**

Create `bridge/tests/test_eyes.py` with this content:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/test_eyes.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'eyes'` or similar — the file doesn't exist yet).

- [ ] **Step 3: Implement `bridge/assets/faces/eyes.py`**

```python
"""Procedural EVE/Vector-style eyes for MILO's face display.

Every emotion is two rounded-rectangle "eye blocks" on a 128x64 1-bit
canvas. No pupils, eyebrows, or mouth: expression comes only from each
eye's size, tilt, and position. See README.md in this directory for the
full design rationale and parameter reference.
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageDraw

WIDTH, HEIGHT = 128, 64


@dataclass(frozen=True)
class Eye:
    w: int = 32
    h: int = 32
    radius: int = 10
    tilt: float = 0.0  # degrees, positive = counter-clockwise (PIL convention)
    dx: int = 0  # offset from resting horizontal position
    dy: int = 0  # offset from resting vertical position


@dataclass(frozen=True)
class EyeFrame:
    left: Eye
    right: Eye
    gap: int = 16  # px between the two eyes' resting edges


def _rounded_rect(eye: Eye) -> Image.Image:
    pad = 2
    canvas = Image.new("1", (eye.w + pad * 2, eye.h + pad * 2), 0)
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(
        (pad, pad, pad + eye.w, pad + eye.h), radius=eye.radius, fill=1
    )
    if eye.tilt:
        canvas = canvas.rotate(eye.tilt, expand=True, resample=Image.BICUBIC)
    return canvas


def render_frame(frame: EyeFrame) -> Image.Image:
    canvas = Image.new("1", (WIDTH, HEIGHT), 0)
    left_img = _rounded_rect(frame.left)
    right_img = _rounded_rect(frame.right)
    total_w = left_img.width + frame.gap + right_img.width
    start_x = (WIDTH - total_w) // 2
    cy = HEIGHT // 2

    left_x = start_x + frame.left.dx
    left_y = cy - left_img.height // 2 + frame.left.dy
    canvas.paste(left_img, (left_x, left_y))

    right_x = start_x + left_img.width + frame.gap + frame.right.dx
    right_y = cy - right_img.height // 2 + frame.right.dy
    canvas.paste(right_img, (right_x, right_y))

    return canvas
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/test_eyes.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add bridge/assets/faces/eyes.py bridge/tests/test_eyes.py
git commit -m "feat(faces): procedural rounded-rect eye rendering engine"
```

---

### Task 2: Emotion parameter table

**Files:**
- Modify: `bridge/assets/faces/eyes.py` (append `EMOTIONS`)
- Modify: `bridge/tests/test_eyes.py` (append tests)

**Interfaces:**
- Consumes: `Eye`, `EyeFrame`, `render_frame` from Task 1.
- Produces: `EMOTIONS: dict[str, list[EyeFrame]]` — the exact 20 keys listed in Global Constraints, each mapping to a list of 1+ `EyeFrame`s in display order. Consumed by Task 3's generator.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_eyes.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest bridge/tests/test_eyes.py -v`
Expected: FAIL (`ImportError: cannot import name 'EMOTIONS'`)

- [ ] **Step 3: Append `EMOTIONS` to `bridge/assets/faces/eyes.py`**

```python
def _pulse(frame: EyeFrame, factor: float) -> EyeFrame:
    """Scale both eyes' height by `factor`, for a subtle talk/love pulse."""
    def scaled(eye: Eye) -> Eye:
        return Eye(w=eye.w, h=round(eye.h * factor), radius=eye.radius,
                    tilt=eye.tilt, dx=eye.dx, dy=eye.dy)
    return EyeFrame(scaled(frame.left), scaled(frame.right), gap=frame.gap)


def _talk(base: EyeFrame) -> list[EyeFrame]:
    """Generic 2-frame speaking pulse layered on any base emotion frame."""
    return [base, _pulse(base, 0.85)]


_IDLE = EyeFrame(Eye(), Eye())
_HAPPY = EyeFrame(Eye(h=16, tilt=-14, dy=-2), Eye(h=16, tilt=14, dy=-2))
_SAD = EyeFrame(Eye(h=20, tilt=16, dy=6), Eye(h=20, tilt=-16, dy=6))
_ANGRY = EyeFrame(
    Eye(h=20, tilt=16, dy=2), Eye(h=20, tilt=-16, dy=2), gap=10,
)
_SLEEPY = EyeFrame(Eye(h=8, dy=4), Eye(h=8, dy=4))
_SURPRISED = EyeFrame(Eye(w=40, h=40, dy=-4), Eye(w=40, h=40, dy=-4), gap=18)
_CONFUSED = EyeFrame(Eye(), Eye(w=28, h=28, tilt=18, dy=-10))
_LOVE = EyeFrame(Eye(h=18, tilt=-10, dy=-2), Eye(h=18, tilt=10, dy=-2))
_THINKING_1 = EyeFrame(Eye(h=28, dx=-4, dy=-2), Eye(w=28, h=26, tilt=8, dx=-4, dy=-6))
_THINKING_2 = EyeFrame(Eye(h=28, dx=4, dy=-2), Eye(w=28, h=26, tilt=8, dx=4, dy=-6))
_EXCITED_1 = EyeFrame(Eye(w=36, h=36, dy=-6), Eye(w=36, h=36, dy=-6), gap=18)
_EXCITED_2 = EyeFrame(Eye(w=36, h=36, dy=4), Eye(w=36, h=36, dy=4), gap=18)

EMOTIONS: dict[str, list[EyeFrame]] = {
    "idle": [_IDLE],
    "idle_blink": [
        EyeFrame(Eye(h=18), Eye(h=18)),
        EyeFrame(Eye(h=6), Eye(h=6)),
        EyeFrame(Eye(h=18), Eye(h=18)),
        EyeFrame(Eye(h=32), Eye(h=32)),
    ],
    "happy": [_HAPPY],
    "sad": [_SAD],
    "angry": [_ANGRY],
    "love": [_LOVE, _pulse(_LOVE, 1.2)],
    "sleepy": [_SLEEPY],
    "surprised": [_SURPRISED],
    "confused": [_CONFUSED],
    "thinking": [_THINKING_1, _THINKING_2],
    "excited": [_EXCITED_1, _EXCITED_2],
    "talk_happy": _talk(_HAPPY),
    "talk_sad": _talk(_SAD),
    "talk_angry": _talk(_ANGRY),
    "talk_confused": _talk(_CONFUSED),
    "talk_love": _talk(_LOVE),
    "talk_sleepy": _talk(_SLEEPY),
    "talk_surprised": _talk(_SURPRISED),
    "talk_thinking": _talk(_THINKING_1),
    "talk_excited": _talk(_EXCITED_1),
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest bridge/tests/test_eyes.py -v`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git add bridge/assets/faces/eyes.py bridge/tests/test_eyes.py
git commit -m "feat(faces): add EMOTIONS parameter table for all 20 face states"
```

---

### Task 3: Generator CLI, asset regeneration, and pose-art cleanup

**Files:**
- Create: `bridge/tools/generate_faces.py`
- Test: `bridge/tests/test_generate_faces.py`
- Modify (delete): pose-linked PNGs in `bridge/assets/faces/` listed in Global Constraints
- Modify (overwrite): the 20 emotion PNGs in `bridge/assets/faces/` (generator output, not hand-edited)

**Interfaces:**
- Consumes: `EMOTIONS` from Task 2 (`bridge/assets/faces/eyes.py`).
- Produces: a `generate(out_dir: Path) -> int` function (returns count of files written), used by the CLI `__main__` block and by Task 4's regeneration step.

- [ ] **Step 1: Write the failing test**

Create `bridge/tests/test_generate_faces.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))
from generate_faces import generate  # noqa: E402


def test_generate_writes_one_file_per_single_frame_face(tmp_path):
    count = generate(tmp_path)
    assert (tmp_path / "idle.png").exists()
    assert (tmp_path / "happy.png").exists()
    assert count > 0


def test_generate_writes_indexed_files_for_multi_frame_faces(tmp_path):
    generate(tmp_path)
    assert (tmp_path / "idle_blink_1.png").exists()
    assert (tmp_path / "idle_blink_4.png").exists()
    assert not (tmp_path / "idle_blink.png").exists()
    assert (tmp_path / "love_1.png").exists()
    assert (tmp_path / "love_2.png").exists()


def test_generate_return_count_matches_total_frames():
    import eyes  # noqa: E402  (already on sys.path from the import above)
    expected = sum(len(frames) for frames in eyes.EMOTIONS.values())

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        assert generate(Path(d)) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest bridge/tests/test_generate_faces.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'generate_faces'`)

- [ ] **Step 3: Implement `bridge/tools/generate_faces.py`**

```python
"""Render every face in eyes.EMOTIONS to PNGs.

Usage:
    python bridge/tools/generate_faces.py [out_dir]

Defaults to bridge/assets/faces, overwriting the 20 emotion PNGs that
belong to this generator. Pose-linked art (wave, dance, walk, ...) is not
touched by this script — see bridge/assets/faces/README.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "assets" / "faces"))
from eyes import EMOTIONS, render_frame  # noqa: E402

DEFAULT_OUT = Path(__file__).parent.parent / "assets" / "faces"


def generate(out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, frames in EMOTIONS.items():
        multi = len(frames) > 1
        for idx, frame in enumerate(frames, start=1):
            filename = f"{name}_{idx}.png" if multi else f"{name}.png"
            render_frame(frame).save(out_dir / filename)
            written += 1
    return written


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_OUT
    count = generate(target)
    print(f"wrote {count} face frames to {target}")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest bridge/tests/test_generate_faces.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Delete pose-linked art no longer produced by any generator**

```bash
git rm bridge/assets/faces/bow.png bridge/assets/faces/crab.png \
  bridge/assets/faces/cute.png bridge/assets/faces/dance_1.png \
  bridge/assets/faces/dance_2.png bridge/assets/faces/dead_1.png \
  bridge/assets/faces/dead_2.png bridge/assets/faces/dead_3.png \
  bridge/assets/faces/freaky.png bridge/assets/faces/point_1.png \
  bridge/assets/faces/point_2.png bridge/assets/faces/point_3.png \
  bridge/assets/faces/pushup.png bridge/assets/faces/rest_1.png \
  bridge/assets/faces/rest_2.png bridge/assets/faces/rest_3.png \
  bridge/assets/faces/shake.png bridge/assets/faces/shrug.png \
  bridge/assets/faces/swim.png bridge/assets/faces/walk.png \
  bridge/assets/faces/wave.png bridge/assets/faces/worm.png
```

Expected: 22 files staged for deletion.

- [ ] **Step 6: Regenerate the 20 emotion PNGs**

```bash
python bridge/tools/generate_faces.py
git status --short bridge/assets/faces/
```

Expected: the old kaomoji PNGs are replaced by new content. 35 files total:
7 single-frame faces (`idle`, `happy`, `sad`, `angry`, `sleepy`,
`surprised`, `confused`) + `idle_blink_1..4` (4) + `love_1/_2`,
`thinking_1/_2`, `excited_1/_2` (6) + 9 `talk_*_1/_2` pairs (18) = 35. All
should show as modified (they already existed under these names); no new
untracked filenames should appear.

- [ ] **Step 7: Visually inspect the output and tune constants**

Read a handful of the generated PNGs (`bridge/assets/faces/idle.png`,
`happy.png`, `angry.png`, `sad.png`, `surprised.png`, `confused.png`,
`idle_blink_2.png`) with the Read tool. Check: eyes are centered, don't clip
the canvas edges, and each emotion is visually distinct from its neighbors
(happy vs. love, angry vs. sad). If something looks wrong (eyes touching
the canvas edge, an emotion unreadable, a blink frame not fully closed),
adjust the specific `Eye(...)` values in `bridge/assets/faces/eyes.py`
(Task 2's `EMOTIONS` block) and re-run Step 6. This is a normal tuning
loop, not a new task — repeat until the set looks right.

- [ ] **Step 8: Run the full test suite**

Run: `python -m pytest bridge/tests/ -v`
Expected: PASS, all tests including `test_display.py` (uses its own
synthetic fixtures, unaffected) and `test_convert_faces.py` (tests the
unrelated historical converter, unaffected).

- [ ] **Step 9: Commit**

```bash
git add bridge/tools/generate_faces.py bridge/tests/test_generate_faces.py \
  bridge/assets/faces/
git commit -m "feat(faces): generate EVE-style eye art, drop pose-linked kaomoji art"
```

---

### Task 4: Design README

**Files:**
- Create: `bridge/assets/faces/README.md`

**Interfaces:**
- Consumes: final tuned values in `EMOTIONS` (Task 3) — the README's parameter table must match whatever shipped, so write it last, after tuning is done.

- [ ] **Step 1: Read the final `EMOTIONS` table**

Read `bridge/assets/faces/eyes.py` in full so the README's parameter
descriptions (tilt directions, which emotions are multi-frame, exact
gap/size choices) reflect the actually-committed values, not the plan's
initial draft.

- [ ] **Step 2: Write `bridge/assets/faces/README.md`**

```markdown
# MILO's face: EVE-style procedural eyes

MILO's 128x64 OLED face is two rounded-rectangle "eye blocks" — no pupils,
eyebrows, or mouth. Every expression comes from each eye's size, tilt, and
position, in the spirit of EVE (WALL-E) and Anki Vector/Cozmo.

## Why procedural, not hand-drawn

The old face art (`convert_faces.py`) was a kaomoji text library
(`( *w*)`) inherited from a reference firmware. It's been replaced with
`eyes.py` + `generate_faces.py`: a parameter table and a renderer. Adding
or adjusting an emotion means changing a handful of numbers and re-running
the generator, not redrawing a bitmap.

## Shape language

Each eye is one `Eye`:

| Param | Meaning |
|---|---|
| `w`, `h` | width / height in px |
| `radius` | corner radius in px |
| `tilt` | rotation in degrees (+ = counter-clockwise) |
| `dx`, `dy` | offset from the eye's resting position |

A pair of eyes is an `EyeFrame` (`left`, `right`, `gap` between them).
`render_frame()` draws one `EyeFrame` onto the 128x64 canvas; `EMOTIONS`
maps each face name to a list of `EyeFrame`s (more than one = animation,
shown at `AnimMode` / fps set by whatever calls `FaceDisplay.set_face`).

## Regenerating the art

    python bridge/tools/generate_faces.py

Writes `<name>.png` (single frame) or `<name>_<n>.png` (multi-frame, 1-indexed)
into `bridge/assets/faces/`, matching what `FaceDisplay.load_face_frames`
expects (`bridge/milo_bridge/drivers/display.py`).

## Emotion reference

| Face | Frames | Shape notes |
|---|---|---|
| `idle` | 1 | level, symmetric — the resting look |
| `idle_blink` | 4 | height animates open -> closed -> open |
| `happy` | 1 | squint (reduced height), outer corners tilt up |
| `sad` | 1 | outer corners tilt down, eyes drop |
| `angry` | 1 | outer corners tilt down further, narrow gap (furrowed) |
| `love` | 2 | soft happy-like squint, gentle size pulse |
| `sleepy` | 1 | eyes collapsed to a thin band, drooped |
| `surprised` | 1 | both eyes enlarged, raised |
| `confused` | 1 | asymmetric — one eye neutral, one raised and tilted |
| `thinking` | 2 | eyes drift sideways together, one tilted (glancing) |
| `excited` | 2 | enlarged eyes bouncing up/down |
| `talk_<emotion>` | 2 | the base emotion's eyes, pulsing subtly (speaking) |

## Not redesigned here

Movement/action faces (`wave`, `dance`, `walk`, `crab`, `worm`, `swim`,
`point`, `pushup`, `bow`, `shake`, `shrug`, `cute`, `freaky`, `dead`,
`rest`, `stand`) are tied 1:1 to physical servo poses in
`bridge/milo_bridge/poses.py`, not to emotional state. They have no art in
this library and fall back to `idle` via `FaceDisplay`'s existing
`FALLBACK_FACE` behavior.
```

- [ ] **Step 3: Commit**

```bash
git add bridge/assets/faces/README.md
git commit -m "docs(faces): document the EVE-style eye design system"
```

---

## Final Verification

- [ ] Run `python -m pytest bridge/tests/ -v` — full suite passes.
- [ ] Run `git log --oneline -6` — one commit per task, in order.
- [ ] Visually confirm (Read tool) `idle.png`, `happy.png`, `sad.png`,
      `angry.png`, `surprised.png`, `confused.png`, `sleepy.png` are all
      distinct and centered on the canvas.
