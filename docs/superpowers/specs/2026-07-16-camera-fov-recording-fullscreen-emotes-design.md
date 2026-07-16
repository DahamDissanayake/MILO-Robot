# Camera FOV Fix, SD/HD Toggle, Video Recording, Fullscreen Piloting, Emote Dropdown

**Date:** 2026-07-16
**Status:** Approved for planning

## Problem

1. The camera feed in the webapp looks zoomed in — narrower field of view than
   the real Raspberry Pi Camera Module v2 (IMX219) actually sees. The driver
   requests `main={"size": (640, 480)}` from picamera2 without pinning a raw/
   sensor mode, which is the documented failure mode for landing on a cropped
   sensor window instead of a full-frame binned one.
2. There's no way to record video from the feed — only a snapshot (single-frame
   JPEG download) exists.
3. There's no fullscreen view for piloting the robot by camera feed alone, with
   on-screen movement controls.
4. The "Poses & Emotes" panel is an always-visible button grid taking up fixed
   cockpit space; it should collapse behind an icon/dropdown.

## Goals

- Pin the picamera2 raw stream to the IMX219's native 2×2-binned full-FOV mode
  (1640×1232) on every configuration, so the ISP always scales down from the
  complete sensor image rather than a cropped subset.
- Support switching the streamed (`main`) resolution between two full-FOV
  presets — `sd` (640×480, current default) and `hd` (1640×1232, native,
  no extra ISP downscale) — via a websocket message, broadcast to all clients
  through existing telemetry.
- Add client-side video recording (canvas + `MediaRecorder` → downloaded
  `.webm`), matching the existing client-side snapshot pattern.
- Add a fullscreen piloting mode: fullscreen the camera feed with an overlay
  of d-pad/stop/control-toggle/emote controls, sharing the exact hold-state
  button-binding logic the Move panel already has (extracted into a shared
  module) rather than duplicating it.
- Collapse the Poses & Emotes panel into an icon-triggered dropdown, reachable
  from both the normal cockpit layout and the fullscreen overlay.

## Non-goals

- No server-side video recording/storage — recording is entirely client-side,
  like the existing snapshot feature.
- No change to camera bandwidth/fps tuning beyond the resolution the user
  explicitly picks — no adaptive/automatic quality switching.
- No cross-module shared "current speed" store — the fullscreen overlay's
  d-pad uses a fixed speed; the Move panel's slider-scaled speed remains
  reachable via keyboard, which already works in fullscreen with no changes.
- No guarantee that pinning `raw` fully resolves the "zoomed in" perception —
  this can't be verified without the real Pi. If the FOV still looks tight
  afterward, that is very likely the Camera Module v2's real ~62° lens FOV
  (a hardware property), not a remaining software crop.
- No new JS test infrastructure — this repo has no functional JS test suite
  today (confirmed: no `package.json`, no `*.test.js`/`*.spec.js` anywhere),
  so frontend changes stay manually verified, consistent with all prior
  frontend work in this project.

## Design

### 1. `bridge/milo_bridge/drivers/camera.py`: pin full-FOV raw mode + resolution switch

```python
RESOLUTIONS: dict[str, tuple[int, int]] = {
    "sd": (640, 480),
    "hd": (1640, 1232),
}
DEFAULT_RESOLUTION = "sd"
FULL_FOV_RAW_SIZE = (1640, 1232)  # IMX219 native 2x2-binned full-FOV sensor mode
DEFAULT_FPS = 15


class CameraStreamer:
    def __init__(self, frame_source, fps=DEFAULT_FPS, resolution=DEFAULT_RESOLUTION):
        self._frame_source = frame_source
        self.fps = fps
        self.resolution = resolution
        self._pending_resolution: str | None = None

    def set_resolution(self, name: str) -> None:
        if name not in RESOLUTIONS:
            raise ValueError(f"unknown resolution {name!r}")
        self._pending_resolution = name

    @classmethod
    def from_hardware(cls, fps: int = DEFAULT_FPS, resolution: str = DEFAULT_RESOLUTION) -> "CameraStreamer":
        import io
        from picamera2 import Picamera2

        cam = Picamera2()

        def _configure(name: str) -> None:
            w, h = RESOLUTIONS[name]
            cam.stop()
            cam.configure(cam.create_video_configuration(
                main={"size": (w, h), "format": "RGB888"},
                # Pinning raw to the sensor's full-FOV binned mode stops
                # picamera2's automatic mode selection from ever landing on
                # a cropped sensor window for a small `main` size -- the ISP
                # always scales `main` down from the complete image instead.
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
```

`frames()` itself is unchanged from the current implementation — it still
just calls `await asyncio.to_thread(self._frame_source)` in a loop. That's
what makes this safe: `grab()` (the thing running in the worker thread) is
where a pending resolution switch gets applied, on the same thread and
strictly before/after a capture, never concurrently with one.

Note on the `frame_source` injection pattern: today's constructor takes a
plain `frame_source` callable for testability with fakes. The resolution
switch is applied *inside* the injected callable (`grab`), not by the
`CameraStreamer` class reaching into hardware directly — so a test double can
exercise `set_resolution()` / `.resolution` end-to-end by supplying its own
`grab`-like closure that mimics the same pending-switch pattern, without
touching picamera2.

### 2. Telemetry: expose current resolution

`bridge/milo_bridge/webapp/telemetry.py`, `collect_telemetry()`:

```python
"camera_resolution": getattr(deps.camera, "resolution", None),
```

Added alongside the existing `gait_mode` field — same "read live state at
broadcast time" pattern, so any tab (including one opened after another tab
switched resolution) self-syncs within one telemetry tick (2s).

### 3. `bridge/milo_bridge/webapp/ws.py`: new message

In `_handle_text`, alongside the existing `if t == "mode":` etc. blocks:

```python
    if t == "camera_resolution":
        camera = app["deps"].camera
        if camera is None:
            await ws.send_json({"t": "err", "for": "camera_resolution", "error": "camera unavailable"})
            return
        try:
            camera.set_resolution(data.get("value", ""))
        except ValueError as exc:
            await ws.send_json({"t": "err", "for": "camera_resolution", "error": str(exc)})
            return
        await ws.send_json({"t": "ack", "for": "camera_resolution"})
        return
```

No control check — matches this codebase's existing rule that *observation*
is never brokered (only motion is), which is why the camera panel today has
no `needsControl` either.

### 4. `bridge/milo_bridge/webapp/static/js/panels/camera.js`: SD/HD, snapshot, record, fullscreen trigger

- Add a small "SD | HD" segmented control; sends
  `bus.send({t: "camera_resolution", value: "sd"|"hd"})`, and syncs its active
  state from `bus.on("telemetry", m => ...)` (mirrors how `move.js` syncs its
  mode buttons from `telemetry.gait_mode` today).
- Add a "Record" button next to "Snapshot":
  - On click: create an offscreen `<canvas>` sized to the image's natural
    dimensions, start a `setInterval`/`requestAnimationFrame` loop redrawing
    the `<img>` onto it, call `canvas.captureStream()`, feed it into
    `new MediaRecorder(stream, {mimeType: "video/webm"})`, collect chunks via
    `ondataavailable`.
  - Button relabels to "Stop & Save" with a recording indicator while active.
  - On click again: `recorder.stop()`, assemble the collected chunks into a
    `Blob`, and trigger a download via the same anchor-click pattern the
    snapshot button already uses (`a.href = URL.createObjectURL(blob); a.download = milo-${Date.now()}.webm`).
- Add a "Fullscreen" button: calls `.requestFullscreen()` on a wrapper `<div>`
  containing the `<img>` plus the overlay controls described below.

### 5. `bridge/milo_bridge/webapp/static/js/pilot.js` (new): shared piloting logic

Extracted from `move.js`'s existing button-binding helpers (`bindGaitButton`,
`bindScripted`, `sending`/`scaled`, `lookPress`/`lookRelease`,
`bindLookButton`) into a factory both `move.js` and the fullscreen overlay
call:

```js
export function createPilotController(bus, getSpeed) {
  // returns { bindGaitButton(el, dir), bindScripted(el, dir, msg),
  //           bindLookButton(el, dir), stop(), destroy() }
  // — identical hold-state machinery (continuous-gait send timer, scripted
  // turn press/release, manual-mode look-pose hold) as move.js has today,
  // just parameterized by the bus and a speed getter instead of closing
  // over module-local DOM.
}
```

`move.js` keeps its own keyboard bindings (`window` keydown/keyup) exactly as
they are today — those already work regardless of what's fullscreened, since
they're bound globally, not to any panel element. `move.js` and the fullscreen
overlay each construct their own `createPilotController` instance bound to
their own buttons; both drive the same bus/gait/pose messages, so concurrent
use (e.g. keyboard held while a fullscreen button is also pressed) is
harmless — gait/pose commands are idempotent, last-write-wins, not additive.

### 6. Fullscreen overlay markup (in `camera.js`)

Rendered inside the fullscreen wrapper, shown only while `document.fullscreenElement` is set:

- D-pad (forward/back/turn-left/turn-right) + look-up/look-down buttons,
  bound via `createPilotController` from `pilot.js`.
- STOP button (`bus.send({t: "stop"})`).
- Control toggle (`bus.send({t: "control", take: !bus.controlled})`) — needed
  because native Fullscreen hides everything outside the fullscreened
  element, including the statusbar's own Take Control button.
- Emote dropdown icon (see below), so posing doesn't require exiting
  fullscreen.
- Buttons disabled/dimmed when `!bus.controlled`, mirroring `.panel.locked`.

### 7. `bridge/milo_bridge/webapp/static/js/panels/poses.js`: collapse to dropdown

Same data source (`/api/poses`, `/api/faces`) and same `bus.send({t: "pose"|"face", name})`
actions — only the presentation changes:

```js
el.innerHTML = `
  <button class="btn" id="emote-toggle">🎭 Emotes</button>
  <div id="emote-popover" class="hidden">
    <div class="muted">Poses</div><div id="pose-btns" ...></div>
    <div class="muted">Faces</div><div id="face-btns" ...></div>
  </div>`;
```

`#emote-toggle` toggles the popover open/closed; click-outside or a second
toggle click closes it. To avoid two copies of the pose/face fetch-and-render
logic, `poses.js` exports a named `mountEmotePopover(container, { bus })`
(the actual toggle+popover+fetch implementation) in addition to its default
registry-panel export, which just calls `mountEmotePopover` on its own panel
body. The fullscreen overlay imports and calls `mountEmotePopover` directly
against its own overlay container — same function, two mount points.

## Testing

- `bridge/tests/test_camera.py` (new): `CameraStreamer.set_resolution()` /
  `.resolution` against a fake `frame_source`-style double that mimics the
  pending-switch-applied-on-next-grab pattern — no real hardware/picamera2
  needed, matches how `drivers/servos.py` etc. are tested against fakes
  elsewhere in this suite.
- `bridge/tests/webapp/test_ws.py`: add a `"camera_resolution"` dispatch test
  (success + unknown-value error + camera-unavailable error), mirroring the
  existing `"mode"` test shape.
- `bridge/tests/webapp/test_status.py` or wherever telemetry is covered:
  assert `camera_resolution` appears in `collect_telemetry()` output.
- No frontend test changes — this repo has no JS test suite (see Non-goals).
  `camera.js`, `pilot.js`, `move.js`, and `poses.js` changes are verified
  manually: resolution toggle, snapshot, record-and-download, fullscreen
  entry/exit with keyboard + on-screen piloting, and the emote dropdown from
  both the normal layout and the fullscreen overlay.
- Real-hardware verification (out of reach here) needed to confirm the FOV
  fix visually matches the Camera Module v2's actual field of view.
