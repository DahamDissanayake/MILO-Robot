# EVE-style procedural eyes for MILO's face display

## Problem

MILO's 128x64 SSD1306 face (`bridge/milo_bridge/drivers/display.py`) currently
shows kaomoji-style text emoticons (`( *ω*)`, `(To-oT)`, ...) converted
verbatim from a reference "Sesame" robot's firmware bitmap library
(`bridge/tools/convert_faces.py` -> `bridge/assets/faces/*.png`). This isn't
MILO's own design and doesn't match the intended look: simple, modern,
EVE/Vector-style geometric eyes (per reference photo, an SSD1306 showing two
rounded eye shapes, no mouth or pupils).

## Goal

Replace the kaomoji face art for MILO's emotional expressions with
procedurally generated, EVE-style eyes: two rounded-rectangle "eye blocks" per
frame, expression conveyed purely through each eye's size, tilt, and
position. Ship a generator (not hand-drawn bitmaps) so future emotions are a
parameter change, not new art. Document the design system in a README.

## Non-goals

- No changes to `display.py`, `FaceDisplay`, or its tests. The loader is
  already asset-agnostic (loads `<name>.png` or `<name>_<n>.png` from a
  directory) and already falls back to `idle` when a face has no art
  (`FALLBACK_FACE`, display.py:29,117-121). Nothing there needs to change.
- No redesign of movement/action-pose faces (wave, dance, walk, crab, worm,
  swim, point, pushup, bow, shake, shrug, cute, freaky, dead, rest). These are
  1:1 with physical servo `Pose`s in `poses.py` and are out of scope. Their
  existing art files are deleted so they fall through to the new `idle` face
  via the existing fallback path — no code change required.
- No pupils, eyebrows, or mouth. Pure shape-based expression, matching EVE
  (WALL-E) and Anki Vector/Cozmo.
- No color: the display is 1-bit monochrome (`Image.new("1", ...)`); the cyan
  tint in the reference photo is the physical OLED's phosphor color, not
  something we render.

## Design

### Shape language

Every eye is one rounded rectangle drawn on a 128x64 `"1"`-mode canvas. Each
eye is independently parameterized:

| Param | Meaning |
|---|---|
| `w`, `h` | eye block width/height in px |
| `radius` | corner radius in px |
| `tilt` | rotation in degrees (+ = clockwise); the two eyes typically mirror to create eyebrow-like effects |
| `dx`, `dy` | offset from the eye's resting center position |

A `gap` value (shared, canvas-level) sets the horizontal space between the
two eyes; both eyes are otherwise centered as a pair on the 128x64 canvas.

Rendering: build each rounded rect on its own small transparent-equivalent 1-bit
canvas at (0,0), rotate by `tilt` with expansion, then paste centered at its
final position on the 128x64 frame. This keeps rotation quality independent
of position.

### Module layout

- `bridge/assets/faces/eyes.py` — the rendering primitives (`draw_eye`,
  `render_frame`) plus the `EMOTIONS: dict[str, list[EyeFrame]]` parameter
  table. One entry per face name; a list of length >1 means a multi-frame
  animation (matches `load_face_frames`'s `<name>_<n>.png` convention).
- `bridge/tools/generate_faces.py` — CLI that imports `eyes.py` and writes
  `bridge/assets/faces/<name>.png` / `<name>_<n>.png` for every entry in
  `EMOTIONS`, replacing (only) the files this design owns. Mirrors the
  existing `convert_faces.py` CLI shape (`python bridge/tools/generate_faces.py
  [out_dir]`).
- `bridge/assets/faces/README.md` — design doc: the shape language, the full
  parameter table rendered as a reference sheet, and how to add a new
  emotion.

### Emotion set (~20 names, all currently-referenced non-pose faces)

`idle`, `idle_blink` (4 frames), `happy`, `sad`, `angry`, `love` (2-frame
pulse), `sleepy`, `surprised`, `confused`, `thinking` (2-frame glance),
`excited` (2-frame bounce), and `talk_<emotion>` for each of happy, sad,
angry, confused, love, sleepy, surprised, thinking, excited — each a 2-frame
subtle pulse loop of its base emotion (matches existing code: `talk_*` faces
are already driven with `AnimMode.LOOP`, `net/session.py:79`).

Representative parameter choices (exact numbers tuned visually while
building, but the shape logic is fixed):

- **idle**: level, symmetric, moderate size — the resting look from the
  reference photo.
- **idle_blink**: 4 frames, height animates open -> ~35% -> ~10% (closed
  line) -> ~35%, ending mid-open right before `_blink()` resets to `idle`.
- **happy**: reduced height (squint), eyes tilt outward-up at the outer
  edges (mirrored tilt).
- **sad**: reduced height, tilt outward-down, `dy` positive (drooping).
- **angry**: inner corners pulled down / outer corners up (mirrored tilt,
  opposite sign from happy), slightly narrower gap (furrowed look).
- **surprised**: both eyes enlarged (`w`/`h` up), `dy` negative (raised).
- **sleepy**: height collapsed to a thin band, `dy` slightly positive.
- **confused**: asymmetric — one eye neutral, the other raised (`dy`
  negative) and tilted, plus a slightly smaller `w`.
- **love**: happy-like squint, softer tilt, 2-frame gentle size pulse.
- **excited**: enlarged eyes, 2-frame `dy` bounce (up/down).
- **thinking**: 2-frame drift — eyes shift `dx` together left then right,
  one eye tilted slightly (glancing in thought).
- **talk_<emotion>**: the base emotion's frame 0, repeated as 2 frames with
  a small height pulse (+/-15%) to read as "actively speaking" without a
  mouth.

### File changes

- Add: `bridge/assets/faces/eyes.py`, `bridge/tools/generate_faces.py`,
  `bridge/assets/faces/README.md`.
- Regenerate: the ~20 emotion PNGs listed above (generator output).
- Delete: pose-linked art no longer referenced by any emotion —
  `angry`/`happy`/etc. are kept; `wave.png`, `dance_1/2.png`, `walk.png`,
  `crab.png`, `worm.png`, `swim.png`, `point_1-3.png`, `pushup.png`,
  `bow.png`, `shake.png`, `shrug.png`, `cute.png`, `freaky.png`,
  `dead_1-3.png`, `rest_1-3.png` are removed.
- `convert_faces.py` and `hardware/reference-sesame/face-bitmaps.h` are left
  alone — they're a historical record of the reference firmware's art, not
  something this design touches.

### Testing

`bridge/tests/test_convert_faces.py` tests `convert_faces.py` in isolation
(a synthetic header, not the real asset files) and is unaffected.
`bridge/tests/test_display.py` uses a synthetic `tmp_path` fixture for its
own assets and is unaffected. A new `bridge/tests/test_eyes.py` covers the
generator: every `EMOTIONS` entry renders a 128x64 `"1"`-mode image with a
non-empty bounding box, and multi-frame entries produce the frame count
implied by their list length.

## Open questions

None — approved by user.
