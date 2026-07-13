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
| `angry` | 1 | outer corners tilt up sharply, narrow gap (furrowed) |
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
