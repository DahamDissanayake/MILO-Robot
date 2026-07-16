# Camera FOV Fix, SD/HD Toggle, Video Recording, Fullscreen Piloting, Emote Dropdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the camera's cropped field of view, add an SD/HD resolution toggle, add client-side video recording, add a fullscreen piloting mode with on-screen + keyboard controls, and collapse the always-visible Poses & Emotes panel into an icon-triggered dropdown.

**Architecture:** Backend changes are confined to `bridge/milo_bridge/drivers/camera.py` (pin full-FOV raw sensor mode, add a resolution switch applied on the capture worker thread), `telemetry.py` (broadcast current resolution), and `ws.py` (a new unbrokered websocket message, since observation is never control-gated in this codebase). Frontend changes extract the Move panel's hold-state piloting logic into a shared `pilot.js` module so the camera panel's new fullscreen overlay can reuse it verbatim instead of duplicating it, and split `poses.js`'s popover rendering into an exported `mountEmotePopover()` function usable from both the normal cockpit layout and the fullscreen overlay.

**Tech Stack:** Python 3.11+ / aiohttp / picamera2 (Pi-only, apt-installed) on the backend; vanilla ES modules (no build step, no framework) on the frontend; pytest + pytest-asyncio for backend tests. This repo has no JS test suite — frontend tasks are verified manually via `python bridge/tools/webdev.py` (a fakes-backed dev server), consistent with how every prior frontend change in this project has been done.

## Global Constraints

- Full spec: `docs/superpowers/specs/2026-07-16-camera-fov-recording-fullscreen-emotes-design.md`.
- No server-side video recording/storage — recording is entirely client-side.
- No control-gating on camera resolution switching — observation is never brokered in this codebase (only motion is), matching the existing camera panel's lack of `needsControl`.
- No new JS test infrastructure — verify frontend changes manually via `webdev.py`.
- Backend single-file/`-k`-filtered test commands in this plan run from the `bridge/` directory (e.g. `python -m pytest tests/test_camera.py -v`). **Full-suite runs and `test_static_integrity.py` are CWD-sensitive** (pre-existing: that test resolves paths like `Path("bridge/milo_bridge/webapp/static")` relative to CWD) and must be run from the **repo root** instead, as `python -m pytest bridge/tests/ -q` — every "full suite" step below is written that way; do not shortcut it to `tests/ -q` from inside `bridge/`, which spuriously fails 4 unrelated tests.
- Every commit is a real git commit on the current branch, one per task, following this repo's existing commit style (no AI co-author trailer — short, present-tense, prefixed `feat:`/`fix:`/`test:`/`docs:` as appropriate to match recent history).

---

## Task 1: `CameraStreamer` — pin full-FOV raw mode, add SD/HD resolution switch

**Files:**
- Modify: `bridge/milo_bridge/drivers/camera.py` (full file, 51 lines today)
- Test: `bridge/tests/test_camera.py` (new)

**Interfaces:**
- Produces: `CameraStreamer.RESOLUTIONS: dict[str, tuple[int,int]]` (module-level, `{"sd": (640,480), "hd": (1640,1232)}`), `CameraStreamer(frame_source, fps=15, resolution="sd")`, `.resolution` (str, current), `.set_resolution(name: str) -> None` (raises `ValueError` on unknown name), `.frames()` (async generator, unchanged signature/behavior). Later tasks (ws.py, telemetry.py) call `.set_resolution()` and read `.resolution`.

- [ ] **Step 1: Write the failing tests**

Create `bridge/tests/test_camera.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `bridge/`): `python -m pytest tests/test_camera.py -v`
Expected: FAIL — `ImportError: cannot import name 'RESOLUTIONS'` (or `AttributeError` on `.resolution`/`.set_resolution`), since none of this exists yet.

- [ ] **Step 3: Implement the resolution switch and full-FOV raw pin**

Replace the full contents of `bridge/milo_bridge/drivers/camera.py`:

```python
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
        if name not in RESOLUTIONS:
            raise ValueError(f"unknown resolution {name!r}")
        self._pending_resolution = name

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_camera.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run the full backend suite to check for regressions**

Run (from the repo root, not `bridge/` — see Global Constraints): `python -m pytest bridge/tests/ -q`
Expected: all pass (same count as before plus the 4 new tests).

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/drivers/camera.py bridge/tests/test_camera.py
git commit -m "fix(bridge): pin camera to full-FOV raw mode, add SD/HD resolution switch"
```

---

## Task 2: `FakeCamera` — mirror the new resolution interface

**Files:**
- Modify: `bridge/tests/webapp/fakes.py:101-108`

**Interfaces:**
- Consumes: nothing new from Task 1 (this is a hand-written test double, not a subclass).
- Produces: `FakeCamera(frames=..., resolution="sd")`, `.resolution`, `.set_resolution(name)` (raises `ValueError` on unknown name) — same public shape as `CameraStreamer`, so `webapp/ws.py` and `webapp/telemetry.py` code under test can't tell the difference. `bridge/tools/webdev.py` imports this same class, so it picks up the new default `resolution="sd"` automatically (its one call site, `FakeCamera(frames=_DEV_FRAMES)`, doesn't pass `resolution`, so behavior there is unchanged).

- [ ] **Step 1: Update `FakeCamera`**

In `bridge/tests/webapp/fakes.py`, replace:

```python
class FakeCamera:
    def __init__(self, frames=(b"jpeg-a", b"jpeg-b")):
        self._frames = list(frames)

    async def frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)
```

with:

```python
class FakeCamera:
    def __init__(self, frames=(b"jpeg-a", b"jpeg-b"), resolution="sd"):
        self._frames = list(frames)
        self.resolution = resolution

    async def frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)

    def set_resolution(self, name):
        if name not in ("sd", "hd"):
            raise ValueError(f"unknown resolution {name!r}")
        self.resolution = name
```

- [ ] **Step 2: Verify nothing broke**

Run (from `bridge/`): `python -m pytest tests/webapp/ -q`
Expected: all pass — this is a pure additive change to a test double, no existing test asserts on `FakeCamera`'s constructor signature.

- [ ] **Step 3: Commit**

```bash
git add bridge/tests/webapp/fakes.py
git commit -m "test(bridge): add resolution state to FakeCamera"
```

---

## Task 3: Telemetry — broadcast current camera resolution

**Files:**
- Modify: `bridge/milo_bridge/webapp/telemetry.py:68-80`
- Test: `bridge/tests/webapp/test_status.py:8-22`

**Interfaces:**
- Consumes: `deps.camera.resolution` (from Task 1/2, `None` if `deps.camera` is `None`).
- Produces: `collect_telemetry(deps)["camera_resolution"]` — read by `webapp/ws.py`'s existing telemetry broadcast (no ws.py change needed, it already forwards whatever `collect_telemetry` returns) and, in a later task, by `camera.js`.

- [ ] **Step 1: Write the failing test**

In `bridge/tests/webapp/test_status.py`, extend `test_status_reports_identity_and_hardware` (do not add a new test — this mirrors how `gait_mode` is already asserted alongside the other telemetry fields in this same test):

```python
async def test_status_reports_identity_and_hardware():
    deps = make_deps()
    client = await _client(deps)
    try:
        resp = await client.get("/api/status")
        assert resp.status == 200
        data = await resp.json()
        assert data["robot_id"] == "milo-test"
        assert data["hardware"]["camera"] is True
        assert data["hardware"]["audio"] is True
        assert data["link"] == "disconnected"
        assert data["gait_backend"] == "cpg"
        assert data["gait_mode"] == "raw"
        assert data["camera_resolution"] == "sd"
    finally:
        await client.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `bridge/`): `python -m pytest tests/webapp/test_status.py::test_status_reports_identity_and_hardware -v`
Expected: FAIL — `KeyError: 'camera_resolution'`.

- [ ] **Step 3: Add the field**

In `bridge/milo_bridge/webapp/telemetry.py`, in `collect_telemetry`, add one line after `"gait_mode"`:

```python
def collect_telemetry(deps) -> dict:
    return {
        "t": "telemetry",
        "cpu_percent": _cpu_percent(),
        "temp_c": _cpu_temp_c(),
        "mem_percent": _mem_percent(),
        "uptime_s": round(time.monotonic() - _START, 1),
        "link": deps.get_link_state(),
        "owner": deps.broker.owner if deps.broker else "none",
        "gait_backend": getattr(deps.gait, "backend", None),
        "gait_mode": getattr(deps.gait, "mode", None),
        "camera_resolution": getattr(deps.camera, "resolution", None),
        "imu": imu_snapshot(deps),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/webapp/test_status.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/telemetry.py bridge/tests/webapp/test_status.py
git commit -m "feat(bridge): include camera_resolution in telemetry"
```

---

## Task 4: Websocket — `camera_resolution` message

**Files:**
- Modify: `bridge/milo_bridge/webapp/ws.py:37-56` (inside `_handle_text`, alongside the existing `if t == "control":`/`if t == "stop":` blocks)
- Test: `bridge/tests/webapp/test_ws.py` (new tests appended)

**Interfaces:**
- Consumes: `app["deps"].camera` (may be `None`), `camera.set_resolution(name)` (raises `ValueError`) from Task 1/2.
- Produces: client → server `{"t": "camera_resolution", "value": "sd"|"hd"}`; server → client `{"t": "ack", "for": "camera_resolution"}` on success, `{"t": "err", "for": "camera_resolution", "error": ...}` on failure. No control check (matches `camera` panel having no `needsControl` — observation is never brokered).

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/webapp/test_ws.py`:

```python
async def test_camera_resolution_accepted_without_control():
    """No control gate on camera_resolution -- observation is never
    brokered in this codebase, only motion is."""
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "camera_resolution", "value": "hd"})
        await _recv_json_until(ws, "ack")
        assert deps.camera.resolution == "hd"
    finally:
        await client.close()


async def test_camera_resolution_rejects_unknown_value():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "camera_resolution", "value": "4k"})
        data = await _recv_json_until(ws, "err")
        assert data["for"] == "camera_resolution"
        assert deps.camera.resolution == "sd"
    finally:
        await client.close()


async def test_camera_resolution_errors_when_camera_unavailable():
    deps = make_deps(broker=ControlBroker(), camera=None)
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "camera_resolution", "value": "hd"})
        data = await _recv_json_until(ws, "err")
        assert data["for"] == "camera_resolution"
        assert data["error"] == "camera unavailable"
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `bridge/`): `python -m pytest tests/webapp/test_ws.py -k camera_resolution -v`
Expected: FAIL — all three time out waiting for `ack`/`err` (the server currently replies `{"t": "err", "for": "camera_resolution", "error": "unknown-type"}` only for the third; the first two never receive `ack`/matching `err`, since `"camera_resolution"` isn't dispatched at all yet — the `test_camera_resolution_errors_when_camera_unavailable` case incidentally already sees an `err`, but with `error: "unknown-type"` not `"camera unavailable"`, so its assertion still fails).

- [ ] **Step 3: Add the dispatch**

In `bridge/milo_bridge/webapp/ws.py`, inside `_handle_text`, insert a new block right after the existing `if t == "stop":` block (before `if t == "mode":`):

```python
    if t == "camera_resolution":
        camera = deps.camera
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

(`deps` is already bound at the top of `_handle_text` via `deps = app["deps"]`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/webapp/test_ws.py -v`
Expected: all pass, including the 3 new tests.

- [ ] **Step 5: Run the full backend suite**

Run (from the repo root, not `bridge/` — see Global Constraints): `python -m pytest bridge/tests/ -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/test_ws.py
git commit -m "feat(bridge): add camera_resolution websocket message"
```

---

## Task 5: `pilot.js` — extract shared piloting control logic (new module)

**Files:**
- Create: `bridge/milo_bridge/webapp/static/js/pilot.js`

**Interfaces:**
- Consumes: a `bus` (from `bus.js`, has `.send(obj)`), a `getSpeed: () => number` (0-100).
- Produces: `createPilotController(bus, getSpeed)` returning `{ bindGaitButton(el, token, sign), bindTurnButton(el, dir), bindLookButton(el, dir), gaitPress(token, sign), gaitRelease(token), turnPress(dir), turnRelease(), lookPress(dir), lookRelease(), stop() }`. Consumed by Task 6 (`move.js`) and Task 10 (`camera.js`'s fullscreen overlay) — both will construct their own independent instance.

No automated test exists for this file (this repo has no JS test suite — see Global Constraints). It's verified indirectly in Task 6, where it drives the Move panel's existing manually-verified behavior.

- [ ] **Step 1: Create the file**

```js
// Shared piloting control logic: continuous-gait hold (forward/backward),
// scripted turn hold, and manual-mode look-pose hold. Used by both the
// Move panel and the camera fullscreen overlay so on-screen buttons in
// either place drive the exact same hold-state machinery and bus messages
// instead of two independently maintained copies of it.
const SEND_MS = 100;

export function createPilotController(bus, getSpeed) {
  // -- continuous gait: forward/backward only. `gaitState` maps an
  // arbitrary caller-chosen token (a raw keyboard key, or a button id) to
  // its direction sign, so multiple tokens mapped to the same direction
  // (e.g. the "w" key and a d-pad button both meaning forward) can be held
  // together and only fully release once every token holding that
  // direction has released -- this matches keyboard semantics where
  // holding both W and ArrowUp and releasing only one must keep moving. --
  let vec = { vx: 0 }, timer = null;
  const gaitState = new Map(); // token -> sign (1 forward, -1 backward)

  function scaled() {
    return { vx: vec.vx * (getSpeed() / 100), vy: 0, yaw: 0 };
  }
  function sending(active) {
    if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
    if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
  }
  function gaitSync() {
    let vx = 0;
    gaitState.forEach((sign) => { vx += sign; });
    vec = { vx: Math.sign(vx) };
    sending(gaitState.size > 0);
  }
  function gaitPress(token, sign) { gaitState.set(token, sign); gaitSync(); }
  function gaitRelease(token) { gaitState.delete(token); gaitSync(); }

  // -- turn: scripted gait, held via a large cycle count on the server and
  // stopped with the universal {t:"stop"} message. --
  function turnPress(dir) { bus.send({ t: "turn", dir }); }
  function turnRelease() { bus.send({ t: "stop" }); }

  // -- look up/down: held, not toggled. manual:true (sent first, so its
  // own abort() doesn't cut the pose off mid-flight) freezes the gait
  // engine's writes for as long as the button/key stays down; release
  // returns to standby and un-freezes. --
  function lookPress(dir) {
    bus.send({ t: "manual", on: true });
    bus.send({ t: "pose", name: `look_${dir}` });
  }
  function lookRelease() {
    bus.send({ t: "standby" });
    bus.send({ t: "manual", on: false });
  }

  function bindPointerHold(el, press, release) {
    el.addEventListener("pointerdown", press);
    el.addEventListener("pointerup", release);
    el.addEventListener("pointerleave", release);
    el.addEventListener("pointercancel", release);
    return () => {
      el.removeEventListener("pointerdown", press);
      el.removeEventListener("pointerup", release);
      el.removeEventListener("pointerleave", release);
      el.removeEventListener("pointercancel", release);
    };
  }

  function bindGaitButton(el, token, sign) {
    return bindPointerHold(el, (e) => { e.preventDefault(); gaitPress(token, sign); }, () => gaitRelease(token));
  }
  function bindTurnButton(el, dir) {
    return bindPointerHold(el, (e) => { e.preventDefault(); turnPress(dir); }, turnRelease);
  }
  function bindLookButton(el, dir) {
    return bindPointerHold(el, (e) => { e.preventDefault(); lookPress(dir); }, lookRelease);
  }

  function stop() {
    sending(false);
    gaitState.clear();
  }

  return {
    bindGaitButton, bindTurnButton, bindLookButton,
    gaitPress, gaitRelease, turnPress, turnRelease, lookPress, lookRelease,
    stop,
  };
}
```

- [ ] **Step 2: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/pilot.js
git commit -m "refactor(webapp): add shared pilot controller module"
```

---

## Task 6: `move.js` — refactor to use `pilot.js`

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/move.js` (full file, 168 lines today)

**Interfaces:**
- Consumes: `createPilotController` from Task 5 (`../pilot.js`, relative to `panels/`).
- Produces: no change to this panel's external contract — same `id`, `title`, `needsControl`, same DOM structure/ids, same keyboard bindings. Purely an internal refactor: the Move panel's own d-pad/keyboard handlers now delegate to one shared `pilot` instance instead of maintaining their own `down`/`timer`/`vec` state directly.

No automated test exists for this file (no JS test suite). Verified manually per Step 3 below.

- [ ] **Step 1: Replace the full file contents**

```js
import { createPilotController } from "../pilot.js";

const MODES = ["raw", "balanced", "angled"];
const MODE_LABEL = { raw: "Raw", balanced: "Balanced", angled: "Angled" };

export default {
  id: "move", title: "Move", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;align-items:center">
        <div style="display:flex;gap:6px;width:100%;max-width:220px" id="mode-row">
          ${MODES.map((m) => `<button class="btn" data-mode="${m}" style="flex:1">${MODE_LABEL[m]}</button>`).join("")}
        </div>
        <div class="muted" id="mode-status">Mode: Raw</div>
        <div style="display:grid;grid-template-columns:56px 56px 56px;gap:6px">
          <div></div><button class="btn" data-dpad="up" style="font-size:20px">↑</button><div></div>
          <button class="btn" data-dpad="left" style="font-size:20px">←</button><div></div><button class="btn" data-dpad="right" style="font-size:20px">→</button>
          <div></div><button class="btn" data-dpad="down" style="font-size:20px">↓</button><div></div>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn" data-dpad="lookup" style="width:56px">Up</button>
          <button class="btn" data-dpad="lookdown" style="width:56px">Down</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:220px">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, A/D to turn, hold Q/E to look up/down</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const speed = el.querySelector("#speed");
    const modeStatus = el.querySelector("#mode-status");
    const pilot = createPilotController(bus, () => speed.value);

    function setModeButtons(name) {
      el.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
      modeStatus.textContent = name === "raw" ? "Mode: Raw" : `Mode: ${MODE_LABEL[name]} — enabled`;
    }
    // Balanced is the robot's actual default (set in GaitEngine), not "raw" --
    // this is just the best guess until the first telemetry tick confirms the
    // real mode, which also covers a tab opened after someone else changed it.
    setModeButtons("balanced");
    const offMode = bus.on("mode", (m) => setModeButtons(m.name));
    const offTelemetry = bus.on("telemetry", (m) => { if (m.gait_mode) setModeButtons(m.gait_mode); });
    el.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });

    // -- continuous gait: forward/backward only (turning uses the scripted
    // turn_left/turn_right gait below; look up/down are held poses, not
    // part of this velocity-command path) --
    pilot.bindGaitButton(el.querySelector('[data-dpad="up"]'), "btn-up", 1);
    pilot.bindGaitButton(el.querySelector('[data-dpad="down"]'), "btn-down", -1);

    const gaitKeys = { w: 1, s: -1, ArrowUp: 1, ArrowDown: -1 };
    const kd = (e) => { if (gaitKeys[e.key] !== undefined && !e.repeat && e.target.tagName !== "INPUT") pilot.gaitPress(e.key, gaitKeys[e.key]); };
    const ku = (e) => { if (gaitKeys[e.key] !== undefined) pilot.gaitRelease(e.key); };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    // -- turn: scripted gait, held via a large cycle count on the server
    // and stopped with the existing universal {t:"stop"} message --
    pilot.bindTurnButton(el.querySelector('[data-dpad="left"]'), "left");
    pilot.bindTurnButton(el.querySelector('[data-dpad="right"]'), "right");

    const turnKeys = { a: "left", d: "right", ArrowLeft: "left", ArrowRight: "right" };
    const scriptedDown = new Set();
    const skd = (e) => {
      if (e.repeat || e.target.tagName === "INPUT" || scriptedDown.has(e.key) || !turnKeys[e.key]) return;
      scriptedDown.add(e.key);
      pilot.turnPress(turnKeys[e.key]);
    };
    const sku = (e) => {
      if (turnKeys[e.key]) { scriptedDown.delete(e.key); pilot.turnRelease(); }
    };
    window.addEventListener("keydown", skd);
    window.addEventListener("keyup", sku);

    // -- look up/down: held, not toggled. Press and hold to move to the
    // tilted pose and stay there; release to return to stand. pilot.js
    // owns the actual bus messages -- this block only owns the .active
    // highlight, which is specific to this panel's own buttons. --
    function setLookButtons(dir) {
      el.querySelector('[data-dpad="lookup"]').classList.toggle("active", dir === "up");
      el.querySelector('[data-dpad="lookdown"]').classList.toggle("active", dir === "down");
    }
    pilot.bindLookButton(el.querySelector('[data-dpad="lookup"]'), "up");
    pilot.bindLookButton(el.querySelector('[data-dpad="lookdown"]'), "down");
    ["lookup", "lookdown"].forEach((id) => {
      const btn = el.querySelector(`[data-dpad="${id}"]`);
      const dir = id === "lookup" ? "up" : "down";
      const on = () => setLookButtons(dir);
      const off = () => setLookButtons(null);
      btn.addEventListener("pointerdown", on);
      btn.addEventListener("pointerup", off);
      btn.addEventListener("pointerleave", off);
      btn.addEventListener("pointercancel", off);
    });

    const lookKeys = { q: "up", e: "down" };
    const lookKeyDown = new Set();
    const lkd = (e) => {
      if (e.repeat || e.target.tagName === "INPUT" || !lookKeys[e.key] || lookKeyDown.has(e.key)) return;
      lookKeyDown.add(e.key);
      pilot.lookPress(lookKeys[e.key]);
      setLookButtons(lookKeys[e.key]);
    };
    const lku = (e) => {
      if (lookKeys[e.key]) { lookKeyDown.delete(e.key); pilot.lookRelease(); setLookButtons(null); }
    };
    window.addEventListener("keydown", lkd);
    window.addEventListener("keyup", lku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => {
      pilot.stop();
      offMode();
      offTelemetry();
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("keydown", skd);
      window.removeEventListener("keyup", sku);
      window.removeEventListener("keydown", lkd);
      window.removeEventListener("keyup", lku);
    };
  },
};
```

- [ ] **Step 2: Run the backend static-integrity test**

`move.js` doesn't change its imports of anything `registry.js`-visible, but `pilot.js` is a new file referenced only from `move.js`/`camera.js`, not `registry.js` — confirm this doesn't break the existing static-integrity guard:

Run (from the repo root, not `bridge/` — this test resolves paths relative to CWD, see Global Constraints): `python -m pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all pass (this test only walks `index.html` and `registry.js`'s own imports, not transitive imports, so `pilot.js` isn't required to appear there).

- [ ] **Step 3: Manual verification**

```bash
python bridge/tools/webdev.py
```

Open `http://localhost:8080`, click **Take Control**, then verify every one of these still behaves exactly as before the refactor:

- Mouse-hold the Move panel's ↑/↓ d-pad buttons → robot gait forward/back; release → stops.
- Hold `W`/`S` and separately `ArrowUp`/`ArrowDown` → same forward/back; holding two keys that both mean the same direction (e.g. `W` and `ArrowUp` together) and releasing only one keeps moving until the last one releases.
- Mouse-hold ←/→ d-pad buttons → turn left/right; release → stop.
- Hold `A`/`D` and `ArrowLeft`/`ArrowRight` → same turn behavior.
- Mouse-hold "Up"/"Down" look buttons → robot tilts and holds; the button shows its `.active` highlight while held; release → returns to stand, highlight clears.
- Hold `Q`/`E` → same look up/down behavior with the same highlight.
- Speed slider still scales gait velocity (compare a low vs. high slider value while holding forward).
- Mode buttons (Raw/Balanced/Angled) still switch and reflect telemetry.
- STOP button still halts motion immediately.

- [ ] **Step 4: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/move.js
git commit -m "refactor(webapp): move panel delegates piloting to shared pilot controller"
```

---

## Task 7: `camera.js` — SD/HD resolution toggle

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/camera.js` (full file, 21 lines today)

**Interfaces:**
- Consumes: `telemetry.camera_resolution` (from Task 3), sends `{t: "camera_resolution", value: "sd"|"hd"}` (Task 4).
- Produces: no new exports — this task only adds UI to the existing default export. (`camera.js`'s default export signature is unchanged; a later task, Task 9, will add the `mountEmotePopover` import once Task 8 exists.)

No automated test exists for this file. Verified manually per Step 3.

- [ ] **Step 1: Replace the full file contents**

```js
export default {
  id: "camera", title: "Camera",
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <img id="cam" src="/stream/camera" alt="camera offline"
             onerror="this.dataset.err=1">
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn" id="snap">Snapshot</button>
          <div style="display:flex;gap:2px;margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
      </div>`;
    const img = el.querySelector("#cam");
    el.querySelector("#snap").onclick = () => {
      const c = document.createElement("canvas");
      c.width = img.naturalWidth || 640; c.height = img.naturalHeight || 480;
      c.getContext("2d").drawImage(img, 0, 0);
      const a = document.createElement("a");
      a.href = c.toDataURL("image/jpeg");
      a.download = `milo-${Date.now()}.jpg`;
      a.click();
    };

    const resRow = el.querySelector("#res-row");
    function setResButtons(name) {
      resRow.querySelectorAll("[data-res]").forEach((b) => b.classList.toggle("active", b.dataset.res === name));
    }
    setResButtons("sd");
    resRow.querySelectorAll("[data-res]").forEach((b) => {
      b.onclick = () => bus.send({ t: "camera_resolution", value: b.dataset.res });
    });
    const offTelemetry = bus.on("telemetry", (m) => { if (m.camera_resolution) setResButtons(m.camera_resolution); });

    return () => {
      offTelemetry();
    };
  },
};
```

- [ ] **Step 2: Manual verification**

```bash
python bridge/tools/webdev.py
```

Open `http://localhost:8080`. Click "HD" — it should highlight as active within ~2s (one telemetry tick). Click "SD" — same, switches back. (The dev server's `FakeCamera` doesn't actually change frame content on resolution switch, since it always yields the same tiny fake JPEG — this step only confirms the round-trip: click → `camera_resolution` ack → telemetry reflects it → button highlight updates. The actual picture-quality change only happens on real hardware.)

Confirm the Snapshot button still downloads a `.jpg` as before.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/camera.js
git commit -m "feat(webapp): add SD/HD camera resolution toggle"
```

---

## Task 8: `camera.js` — client-side video recording

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/camera.js`

**Interfaces:**
- Consumes: nothing new from other tasks — pure client-side, mirrors the existing Snapshot button's canvas-grab pattern.
- Produces: no new exports.

No automated test exists for this file. Verified manually per Step 3.

- [ ] **Step 1: Add the Record button and recording logic**

In `bridge/milo_bridge/webapp/static/js/panels/camera.js`, change the button row's `innerHTML` to add a Record button next to Snapshot:

```html
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn" id="snap">Snapshot</button>
          <button class="btn" id="rec">Record</button>
          <div style="display:flex;gap:2px;margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
```

Then, after the existing `#snap` click handler block, add:

```js
    const recBtn = el.querySelector("#rec");
    let recorder = null, recChunks = [], recTimer = null;
    function startRecording() {
      const canvas = document.createElement("canvas");
      canvas.width = img.naturalWidth || 640;
      canvas.height = img.naturalHeight || 480;
      const ctx = canvas.getContext("2d");
      recTimer = setInterval(() => ctx.drawImage(img, 0, 0, canvas.width, canvas.height), 66);
      recChunks = [];
      recorder = new MediaRecorder(canvas.captureStream(15), { mimeType: "video/webm" });
      recorder.ondataavailable = (e) => { if (e.data.size > 0) recChunks.push(e.data); };
      recorder.onstop = () => {
        clearInterval(recTimer);
        const blob = new Blob(recChunks, { type: "video/webm" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `milo-${Date.now()}.webm`;
        a.click();
        URL.revokeObjectURL(a.href);
      };
      recorder.start();
      recBtn.textContent = "⏺ Stop & Save";
      recBtn.classList.add("active");
    }
    function stopRecording() {
      recorder?.stop();
      recBtn.textContent = "Record";
      recBtn.classList.remove("active");
    }
    recBtn.onclick = () => (recorder && recorder.state === "recording" ? stopRecording() : startRecording());
```

And extend the returned cleanup function to stop any in-flight recording on unmount:

```js
    return () => {
      offTelemetry();
      if (recorder && recorder.state === "recording") stopRecording();
    };
```

- [ ] **Step 2: Manual verification**

```bash
python bridge/tools/webdev.py
```

Open `http://localhost:8080` in a browser that supports `MediaRecorder` (Chrome/Firefox/Edge). Click "Record" — button relabels to "Stop & Save" and highlights. Wait a couple seconds, click it again — a `.webm` file downloads. Play the downloaded file in a media player or browser tab and confirm it opens and plays back (content will just be the dev server's static fake-JPEG placeholder repeated, but the file must be valid and playable — that's what's being verified here, not picture content).

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/camera.js
git commit -m "feat(webapp): add client-side video recording to camera panel"
```

---

## Task 9: `poses.js` — collapse into an emote dropdown, export `mountEmotePopover`

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/poses.js` (full file, 18 lines today)

**Interfaces:**
- Produces: `export function mountEmotePopover(el, { bus })` — renders a self-contained toggle icon + popover (pose/face buttons) into whatever container element it's given. Consumed by this file's own default panel export AND, in Task 10, by `camera.js`'s fullscreen overlay (same function, two mount points, per the spec's explicit reuse requirement — no duplicated fetch/render logic).
- The default export (`id: "poses", title: "Poses & Emotes", needsControl: true`) is unchanged in shape — only what it mounts internally changes.

No automated test exists for this file. Verified manually per Step 2.

- [ ] **Step 1: Replace the full file contents**

```js
function fillButtons(box, names, type, bus) {
  names.forEach((name) => {
    const b = document.createElement("button");
    b.className = "btn"; b.textContent = name;
    b.onclick = () => bus.send({ t: type, name });
    box.appendChild(b);
  });
}

// Self-contained toggle icon + popover: fetches /api/poses and /api/faces
// and renders them behind a collapsed icon button instead of an
// always-visible grid. Exported so both the normal cockpit layout (default
// export below) and the camera panel's fullscreen overlay can mount the
// exact same implementation into their own container, rather than each
// keeping its own copy of this fetch-and-render logic.
export function mountEmotePopover(el, { bus }) {
  el.innerHTML = `
    <button class="btn" id="emote-toggle">🎭 Emotes</button>
    <div id="emote-popover" class="emote-popover">
      <div class="muted">Poses</div><div id="pose-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 12px"></div>
      <div class="muted">Faces</div><div id="face-btns" style="display:flex;flex-wrap:wrap;gap:6px;margin-top:6px"></div>
    </div>`;
  const popover = el.querySelector("#emote-popover");
  popover.style.display = "none";
  el.querySelector("#emote-toggle").onclick = () => {
    popover.style.display = popover.style.display === "none" ? "block" : "none";
  };
  fetch("/api/poses").then((r) => r.json()).then((d) => fillButtons(el.querySelector("#pose-btns"), d.poses, "pose", bus));
  fetch("/api/faces").then((r) => r.json()).then((d) => fillButtons(el.querySelector("#face-btns"), d.faces, "face", bus));
}

export default {
  id: "poses", title: "Poses & Emotes", needsControl: true,
  mount(el, { bus }) {
    mountEmotePopover(el, { bus });
  },
};
```

- [ ] **Step 2: Manual verification**

```bash
python bridge/tools/webdev.py
```

Open `http://localhost:8080`, take control. Confirm the "Poses & Emotes" panel now shows a single "🎭 Emotes" button instead of the full grid. Click it — the pose/face button grid appears below it (fetched from `/api/poses`/`/api/faces`). Click a pose button — same `{t:"pose",name}` message goes out as before (watch the Bridge Log panel or Network tab for the ack). Click the toggle again — the grid collapses.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/poses.js
git commit -m "feat(webapp): collapse Poses & Emotes into an icon dropdown"
```

---

## Task 10: `camera.js` — fullscreen piloting overlay + CSS

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/camera.js`
- Modify: `bridge/milo_bridge/webapp/static/css/console.css` (append; existing `#cam` rule at lines 30-35 is untouched)

**Interfaces:**
- Consumes: `createPilotController` from Task 5 (`../pilot.js`, relative to `panels/`), `mountEmotePopover` from Task 9 (`./poses.js`).
- Produces: no new exports.

No automated test exists for these files. Verified manually per Step 3.

- [ ] **Step 1: Wrap the video in a fullscreen container with an overlay, wire up piloting**

In `bridge/milo_bridge/webapp/static/js/panels/camera.js`, add the two imports at the top:

```js
import { createPilotController } from "../pilot.js";
import { mountEmotePopover } from "./poses.js";
```

Replace the `innerHTML` template (the `<img id="cam" ...>` line and everything above the button row) with:

```html
      <div style="display:flex;flex-direction:column;gap:8px;height:100%">
        <div id="cam-wrap" class="cam-wrap">
          <img id="cam" src="/stream/camera" alt="camera offline" onerror="this.dataset.err=1">
          <div id="cam-overlay" class="cam-overlay">
            <div class="cam-overlay-row">
              <button class="btn" id="ov-control">Take Control</button>
              <button class="btn danger" id="ov-stop">STOP</button>
              <div id="ov-emote-mount"></div>
              <button class="btn ghost" id="ov-exit">✕ Exit Fullscreen</button>
            </div>
            <div class="cam-dpad">
              <div></div><button class="btn" data-dpad="up" style="font-size:20px">↑</button><div></div>
              <button class="btn" data-dpad="left" style="font-size:20px">←</button><div></div><button class="btn" data-dpad="right" style="font-size:20px">→</button>
              <div></div><button class="btn" data-dpad="down" style="font-size:20px">↓</button><div></div>
            </div>
            <div class="cam-look-row">
              <button class="btn" data-dpad="lookup">Look Up</button>
              <button class="btn" data-dpad="lookdown">Look Down</button>
            </div>
          </div>
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <button class="btn" id="snap">Snapshot</button>
          <button class="btn" id="rec">Record</button>
          <button class="btn" id="fullscreen">Fullscreen</button>
          <div style="display:flex;gap:2px;margin-left:auto" id="res-row">
            <button class="btn" data-res="sd">SD</button>
            <button class="btn" data-res="hd">HD</button>
          </div>
        </div>
      </div>
```

Right after `const img = el.querySelector("#cam");`, add the fullscreen/overlay wiring:

```js
    const camWrap = el.querySelector("#cam-wrap");
    const overlay = el.querySelector("#cam-overlay");

    const pilot = createPilotController(bus, () => 70);
    pilot.bindGaitButton(overlay.querySelector('[data-dpad="up"]'), "ov-up", 1);
    pilot.bindGaitButton(overlay.querySelector('[data-dpad="down"]'), "ov-down", -1);
    pilot.bindTurnButton(overlay.querySelector('[data-dpad="left"]'), "left");
    pilot.bindTurnButton(overlay.querySelector('[data-dpad="right"]'), "right");
    pilot.bindLookButton(overlay.querySelector('[data-dpad="lookup"]'), "up");
    pilot.bindLookButton(overlay.querySelector('[data-dpad="lookdown"]'), "down");

    const ovControl = overlay.querySelector("#ov-control");
    ovControl.onclick = () => bus.send({ t: "control", take: !bus.controlled });
    const offControl = bus.on("control", (m) => {
      ovControl.textContent = m.you ? "Release Control" : "Take Control";
      ovControl.classList.toggle("active", m.you);
      overlay.classList.toggle("locked", !m.you);
    });
    overlay.querySelector("#ov-stop").onclick = () => bus.send({ t: "stop" });
    overlay.querySelector("#ov-exit").onclick = () => document.exitFullscreen();

    mountEmotePopover(overlay.querySelector("#ov-emote-mount"), { bus });

    el.querySelector("#fullscreen").onclick = () => camWrap.requestFullscreen();
```

Finally, extend the returned cleanup function:

```js
    return () => {
      offTelemetry();
      offControl();
      pilot.stop();
      if (recorder && recorder.state === "recording") stopRecording();
    };
```

- [ ] **Step 2: Add fullscreen/overlay CSS**

Append to `bridge/milo_bridge/webapp/static/css/console.css` (after the existing `#cam { ... }` rule, still inside the "camera" comment section):

```css
.cam-wrap { position: relative; }
.cam-wrap:fullscreen {
  background: #000; display: flex; align-items: center; justify-content: center;
}
.cam-wrap:fullscreen #cam {
  max-width: 100vw; max-height: 100vh; width: auto; height: auto; aspect-ratio: auto;
}
.cam-overlay {
  position: absolute; inset: 0; display: none; flex-direction: column;
  justify-content: space-between; align-items: center; padding: 20px;
  pointer-events: none;
}
.cam-wrap:fullscreen .cam-overlay { display: flex; }
.cam-overlay > * { pointer-events: auto; }
.cam-overlay.locked .cam-dpad,
.cam-overlay.locked .cam-look-row { opacity: 0.4; pointer-events: none; }
.cam-overlay-row { display: flex; gap: 8px; align-items: center; }
.cam-dpad { display: grid; grid-template-columns: 56px 56px 56px; gap: 6px; }
.cam-look-row { display: flex; gap: 8px; }
.emote-popover {
  background: var(--surface); border: 1px solid var(--line); border-radius: 8px;
  padding: 10px; margin-top: 6px;
}
```

- [ ] **Step 3: Manual verification**

```bash
python bridge/tools/webdev.py
```

Open `http://localhost:8080`, take control. Click "Fullscreen" — the video expands to fill the screen (browser fullscreen), with the overlay (Take Control/Release, STOP, 🎭 Emotes, Exit Fullscreen, d-pad, Look Up/Down) visible over it.

- With control already held: overlay's d-pad and look buttons are NOT dimmed; mouse-hold each and confirm the same gait/turn/look behavior as the Move panel (Task 6's checklist), driven by the fullscreen overlay's own buttons this time.
- Release control via the overlay's "Release Control" button — d-pad/look buttons visibly dim and stop responding to clicks (`.locked` opacity/pointer-events); "Take Control" retakes it and un-dims them.
- Press keyboard `W`/`A`/`S`/`D`/arrows/`Q`/`E` while still in fullscreen — robot piloting still responds (these are the Move panel's global `window` keyboard listeners from Task 6, unaffected by fullscreen).
- Click the 🎭 Emotes icon in the overlay — popover opens with the same pose/face buttons as Task 9's normal panel; click a pose — it fires.
- Click "STOP" in the overlay — motion halts.
- Click "✕ Exit Fullscreen" (or press `Escape`, the browser's native fullscreen exit) — returns to the normal cockpit layout.
- Re-enter fullscreen, then release control, then exit fullscreen from that locked state — confirm no leftover dimmed/broken state on the normal Move panel afterward.

- [ ] **Step 4: Run the full backend suite once more (final regression check)**

Run (from the repo root, not `bridge/` — see Global Constraints): `python -m pytest bridge/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/camera.js bridge/milo_bridge/webapp/static/css/console.css
git commit -m "feat(webapp): add fullscreen piloting overlay to camera panel"
```

---

## Task 11: Full-suite final verification

**Files:** none (verification only)

- [ ] **Step 1: Full backend test suite**

Run (from the repo root, not `bridge/` — see Global Constraints): `python -m pytest bridge/tests/ -q`
Expected: all pass (Task 1's 4 new tests, Task 3's updated test, Task 4's 3 new tests, plus every pre-existing test).

- [ ] **Step 2: Full manual walkthrough**

```bash
python bridge/tools/webdev.py
```

With `http://localhost:8080` open and control taken, run through, in order: Snapshot download, Record → Stop & Save → play the `.webm`, SD/HD toggle round-trip, Move panel keyboard + buttons (Task 6's checklist), Emotes dropdown open/close + pose fire (Task 9), Fullscreen entry → overlay piloting (buttons + keyboard) → Emotes-in-overlay → STOP → Exit Fullscreen (Task 10's checklist). Note anything that doesn't match its task's described behavior.

- [ ] **Step 3: Fix any regressions found, per the originating task's file**

If Step 1 or Step 2 surfaces an issue, fix it in the file(s) it belongs to and commit as its own small fix commit (`fix(webapp): ...` / `fix(bridge): ...`), re-running that task's own verification before moving on. Do not bundle unrelated fixes into one commit.

No commit is expected from this task if everything already passes — Tasks 1-10 each already committed their own work.
