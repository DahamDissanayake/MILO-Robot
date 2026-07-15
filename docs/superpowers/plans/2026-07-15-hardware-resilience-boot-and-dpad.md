# Hardware Resilience, I2C Reset, Servo Hold, Boot Choreography & D-pad Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A missing I2C peripheral no longer crashes the service; Tools gets an I2C-reset button and servo Release/Hold; boot performs a visible wake-up + hardware checklist with a fault-indicating face; Q/E and A/D drive the real scripted turn/crab gaits instead of the CPG; balance correction is strong enough to be a real physical reaction.

**Architecture:** Two new null-object drivers (`NullServos`/`NullDisplay`) let `main.py`'s hardware init degrade gracefully via the existing `_optional()` pattern, extended to return `(value, ok)`; a `hardware_status` dict becomes the single source of truth for `/api/status` and the boot-time fault face. `MotionService` gains `restart`/`relax`/`hold`/`turn`/`strafe`, all following the exact control-gated, never-raises pattern already established by `mode`/`reset`/`standby`. Two new scripted poses (`wake_up`, `crab_left`/`crab_right`) reuse the codebase's existing "large cycle count + `abort()`" idiom for hold-to-continue gaits. `BalanceCorrector` grows to move hip and knee together with larger gains.

**Tech Stack:** Python 3 (asyncio, aiohttp), pytest, vanilla JS, PIL (display rendering).

## Global Constraints

- All new/changed Python code must be testable off-hardware with injected fakes.
- Every existing test in `bridge/tests/` must keep passing after each task; run `python -m pytest bridge/tests` (from repo root) at the end of every task.
- Follow existing code style: relative imports, `from __future__ import annotations`, injected `clock`/`sleep`, control-gated + never-raising handler pattern in `MotionService`.
- Commit after each task with a plain, present-tense message; no Claude co-author trailer.
- A correction the spec found factually wrong during planning: `turn_right` is **not** a mechanical L/R-swap of `turn_left` — it's an independently firmware-ported sequence (verified by inspection: their steps don't match under any simple swap or rotation). `crab_right` in this plan is instead derived by a well-defined, explicit transform — swap every servo name's `R`/`L` prefix, keep the same value — which is a legitimate mirroring technique for a bilaterally symmetric body, just not the same relationship `turn_left`/`turn_right` happen to have. Its physical correctness (which direction it actually moves) is unverified off-hardware, same caveat as the balance correction's sign.

---

## Task 1: Null-object hardware drivers

**Files:**
- Create: `bridge/milo_bridge/drivers/null_hardware.py`
- Test: `bridge/tests/test_null_hardware.py`

**Interfaces:**
- Produces: `NullServos` (methods: `set_angle`, `set_pose` async, `last_angle` → `None`, `relax`, `hold` — all no-ops) and `NullDisplay` (`current_face = None`, `set_face`/`show_pin`/`show_status` async no-ops, `start_idle(base_face="idle")`/`stop_idle` no-ops) — drop-in stand-ins matching `ServoDriver`/`SmoothServos` and `FaceDisplay`'s interfaces respectively.

- [ ] **Step 1: Write the failing tests**

Create `bridge/tests/test_null_hardware.py`:

```python
"""Off-hardware tests: NullServos/NullDisplay are safe, silent stand-ins for
when the underlying I2C hardware isn't reachable at boot."""
import asyncio

from milo_bridge.drivers.null_hardware import NullDisplay, NullServos


def test_null_servos_every_call_is_a_safe_no_op():
    servos = NullServos()
    servos.set_angle("R1", 90)
    asyncio.run(servos.set_pose({"R1": 90, "R2": 45}))
    assert servos.last_angle("R1") is None
    servos.relax()
    servos.hold()


def test_null_display_every_call_is_a_safe_no_op():
    display = NullDisplay()
    assert display.current_face is None
    asyncio.run(display.set_face("idle"))
    asyncio.run(display.show_pin("123456"))
    asyncio.run(display.show_status({"servos": True}))
    display.start_idle()
    display.start_idle(base_face="confused")
    display.stop_idle()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_null_hardware.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.drivers.null_hardware'`.

- [ ] **Step 3: Create `null_hardware.py`**

Create `bridge/milo_bridge/drivers/null_hardware.py`:

```python
"""Null-object stand-ins for ServoDriver/SmoothServos and FaceDisplay when
the underlying I2C hardware isn't reachable at boot -- every call is a
silent no-op so GaitEngine/PoseRunner/MotionService/etc. don't need
special-casing, and the rest of the service (including the web dashboard)
stays up with that one peripheral simply absent.
"""

from __future__ import annotations


class NullServos:
    def set_angle(self, servo: str, angle: float) -> None:
        pass

    async def set_pose(self, angles, stagger: bool = True) -> None:
        pass

    def last_angle(self, servo: str) -> float | None:
        return None

    def relax(self) -> None:
        pass

    def hold(self) -> None:
        pass


class NullDisplay:
    current_face: str | None = None

    async def set_face(self, name: str, mode=None, fps: float = 8.0) -> None:
        pass

    async def show_pin(self, pin: str) -> None:
        pass

    async def show_status(self, status: dict[str, bool], seconds: float = 3.0) -> None:
        pass

    def start_idle(self, base_face: str = "idle") -> None:
        pass

    def stop_idle(self) -> None:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_null_hardware.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/drivers/null_hardware.py bridge/tests/test_null_hardware.py
git commit -m "feat(bridge): add NullServos/NullDisplay hardware stand-ins"
```

---

## Task 2: `main.py` — servos/display degrade gracefully

**Files:**
- Modify: `bridge/milo_bridge/main.py`

**Interfaces:**
- Consumes: `NullServos`, `NullDisplay` (Task 1).
- Produces: `_optional(factory, what) -> tuple[object | None, bool]` (changed from returning just the value); a `hardware_status: dict[str, bool]` local (keys `servos`, `display`, `imu`, `camera`, `audio`) that Task 3 wires into `WebDeps`.

No test for this task — `main()` itself (the composition root) is exercised only by the real service, matching existing precedent (no `test_main.py` covers `main()` today). `_optional()`'s own correctness is covered by Task 3's test (see below), since Task 3 is where it becomes independently importable-and-testable in isolation without touching hardware factories.

- [ ] **Step 1: Change `_optional` to return `(value, ok)`**

In `bridge/milo_bridge/main.py`, replace:

```python
def _optional(factory, what: str):
    try:
        return factory()
    except Exception as exc:
        log.warning("%s unavailable (%s: %s) — continuing without it", what, type(exc).__name__, exc)
        return None
```

with:

```python
def _optional(factory, what: str) -> tuple[object | None, bool]:
    try:
        return factory(), True
    except Exception as exc:
        log.warning("%s unavailable (%s: %s) — continuing without it", what, type(exc).__name__, exc)
        return None, False
```

- [ ] **Step 2: Add the null-hardware import**

Replace:

```python
from .drivers.servos import ServoDriver
from .drivers.smooth_servos import SmoothServos
```

with:

```python
from .drivers.null_hardware import NullDisplay, NullServos
from .drivers.servos import ServoDriver
from .drivers.smooth_servos import SmoothServos
```

- [ ] **Step 3: Move servos/display into the optional pattern; build `hardware_status`**

Replace:

```python
    # Required hardware.
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
    motion_servos = SmoothServos(servos, stagger_ms=cfg.servo_stagger_ms)
    motion_servos.start()
    display = FaceDisplay.from_hardware(ASSETS_DIR)
    runner = PoseRunner(motion_servos, display)

    # Optional hardware/components.
    imu = _optional(Mpu6050.from_hardware, "IMU")
    if imu is not None:
        log.info("calibrating IMU gyro bias — keep the robot still")
        await asyncio.to_thread(imu.calibrate_gyro)
        log.info("IMU gyro calibration complete")
    camera = _optional(lambda: CameraStreamer.from_hardware(fps=cfg.video_fps), "camera")
    audio = _optional(AudioIO, "audio")
```

with:

```python
    # Hardware -- every peripheral degrades gracefully to a null stand-in
    # on failure, so one missing/unplugged I2C device never takes the
    # whole service (including the web dashboard) down with it.
    servos, servos_ok = _optional(
        lambda: ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms),
        "servos",
    )
    servos = servos or NullServos()
    motion_servos = SmoothServos(servos, stagger_ms=cfg.servo_stagger_ms)
    motion_servos.start()
    display, display_ok = _optional(lambda: FaceDisplay.from_hardware(ASSETS_DIR), "display")
    display = display or NullDisplay()
    runner = PoseRunner(motion_servos, display)

    imu, imu_ok = _optional(Mpu6050.from_hardware, "IMU")
    if imu is not None:
        log.info("calibrating IMU gyro bias — keep the robot still")
        await asyncio.to_thread(imu.calibrate_gyro)
        log.info("IMU gyro calibration complete")
    camera, camera_ok = _optional(lambda: CameraStreamer.from_hardware(fps=cfg.video_fps), "camera")
    audio, audio_ok = _optional(AudioIO, "audio")
    hardware_status = {
        "servos": servos_ok, "display": display_ok, "imu": imu_ok,
        "camera": camera_ok, "audio": audio_ok,
    }
```

- [ ] **Step 4: Run the full suite to confirm nothing broke**

Run: `pytest bridge/tests -v`
Expected: all tests PASS (`main.py` isn't unit-tested directly; this confirms nothing it imports/constructs broke any existing test).

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/main.py
git commit -m "feat(bridge): degrade servos/display gracefully instead of crashing at boot"
```

---

## Task 3: `hardware_status` plumbing — WebDeps, status API, fakes, and the display-gated `face()` handler

**Files:**
- Modify: `bridge/milo_bridge/webapp/deps.py`
- Modify: `bridge/milo_bridge/webapp/api/status.py`
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/main.py`
- Modify: `bridge/tests/webapp/fakes.py`
- Modify: `bridge/tests/webapp/test_status.py`
- Modify: `bridge/tests/webapp/test_motion.py`
- Test: `bridge/tests/test_main.py` (new)

**Interfaces:**
- Consumes: `hardware_status` local from Task 2.
- Produces: `WebDeps.hardware_status: dict[str, bool]`; `/api/status`'s `hardware` field now equals `deps.hardware_status` verbatim; `MotionService.face()` now checks `self._deps.hardware_status.get("display", True)` instead of `self._deps.display is None` (since `deps.display` is never `None` anymore after Task 2 — it's always a real `FaceDisplay` or a `NullDisplay`).

- [ ] **Step 1: Write the failing tests**

Create `bridge/tests/test_main.py`:

```python
"""_optional() is a small, hardware-independent function -- test it
directly with fake factories rather than exercising main() itself (the
composition root, exercised only by the real service, as today)."""
from milo_bridge.main import _optional


def test_optional_returns_value_and_true_on_success():
    value, ok = _optional(lambda: "real-driver", "widget")
    assert value == "real-driver"
    assert ok is True


def test_optional_returns_none_and_false_on_failure():
    def boom():
        raise RuntimeError("no such device")

    value, ok = _optional(boom, "widget")
    assert value is None
    assert ok is False
```

In `bridge/tests/webapp/test_motion.py`, replace `test_face_requires_display` (it currently sets `deps.display = None` to simulate unavailability, which no longer happens in production now that `deps.display` is always a real driver or a `NullDisplay` — the new signal is `hardware_status["display"]`):

```python
async def test_face_requires_display():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.face("c1", "cute") == {"ok": True}
    assert deps.display.faces == ["cute"]
    deps.hardware_status = {**deps.hardware_status, "display": False}
    assert "error" in await svc.face("c1", "cute")
```

In `bridge/tests/webapp/test_status.py`, replace `test_status_flags_missing_hardware` (it currently passes `camera=None, audio=None, imu=None, display=None` overrides — those objects are never `None` in production anymore, so the test must exercise the new `hardware_status` field directly):

```python
async def test_status_flags_missing_hardware():
    deps = make_deps(hardware_status={
        "servos": True, "camera": False, "audio": False, "imu": False, "display": False,
    })
    client = await _client(deps)
    try:
        data = await (await client.get("/api/status")).json()
        assert data["hardware"] == {
            "servos": True, "camera": False, "audio": False, "imu": False, "display": False,
        }
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_main.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_status.py -v`
Expected: `test_main.py`'s two tests PASS immediately — `_optional()` already returns `(value, ok)` as of Task 2, so this file is verifying already-implemented behavior directly, not driving new implementation (there's nothing left to make GREEN for it in this task; it's included here because Task 2 had no test of its own). `test_face_requires_display` FAILS with `AttributeError: 'WebDeps' object has no attribute 'hardware_status'`. `test_status_flags_missing_hardware` FAILS the same way via `make_deps(hardware_status=...)` being an unrecognized override that just gets `setattr`'d onto an object missing the field the API doesn't yet read.

- [ ] **Step 3: Add `hardware_status` to `WebDeps`**

In `bridge/milo_bridge/webapp/deps.py`, replace:

```python
    broker: Any | None     # ControlBroker (Task 2)
    media_hub: Any | None  # MediaHub (Task 4)
    log_buffer: Any | None # RingBufferLogHandler (Task 7)
    get_link_state: Callable[[], str]
```

with:

```python
    broker: Any | None     # ControlBroker (Task 2)
    media_hub: Any | None  # MediaHub (Task 4)
    log_buffer: Any | None # RingBufferLogHandler (Task 7)
    hardware_status: dict[str, bool]  # servos/display/imu/camera/audio presence at boot
    get_link_state: Callable[[], str]
```

- [ ] **Step 4: Wire `hardware_status` into `main.py`'s `WebDeps` construction**

In `bridge/milo_bridge/main.py`, replace:

```python
    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=motion_servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
        graph_api=graph_api, graph_store=graph,
        broker=broker, media_hub=hub, log_buffer=log_buffer,
```

with:

```python
    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=motion_servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
        graph_api=graph_api, graph_store=graph,
        broker=broker, media_hub=hub, log_buffer=log_buffer,
        hardware_status=hardware_status,
```

- [ ] **Step 5: Make `/api/status` report `hardware_status` directly**

In `bridge/milo_bridge/webapp/api/status.py`, replace:

```python
    body.update(
        robot_id=deps.config.robot_id,
        robot_name=deps.config.robot_name,
        hardware={
            "camera": deps.camera is not None,
            "audio": deps.audio is not None,
            "imu": deps.imu is not None,
            "display": deps.display is not None,
        },
    )
```

with:

```python
    body.update(
        robot_id=deps.config.robot_id,
        robot_name=deps.config.robot_name,
        hardware=deps.hardware_status,
    )
```

- [ ] **Step 6: Gate `MotionService.face()` on `hardware_status` instead of `display is None`**

In `bridge/milo_bridge/webapp/motion.py`, replace:

```python
    async def face(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if self._deps.display is None:
            return {"error": "display unavailable"}
```

with:

```python
    async def face(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if not self._deps.hardware_status.get("display", True):
            return {"error": "display unavailable"}
```

- [ ] **Step 7: Add `hardware_status` to `make_deps()`**

In `bridge/tests/webapp/fakes.py`, replace:

```python
        broker=None,
        media_hub=None,
        log_buffer=None,
        get_link_state=lambda: "disconnected",
    )
```

with:

```python
        broker=None,
        media_hub=None,
        log_buffer=None,
        hardware_status={"servos": True, "display": True, "imu": True, "camera": True, "audio": True},
        get_link_state=lambda: "disconnected",
    )
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest bridge/tests/test_main.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_status.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 9: Commit**

```bash
git add bridge/milo_bridge/webapp/deps.py bridge/milo_bridge/webapp/api/status.py bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/main.py bridge/tests/webapp/fakes.py bridge/tests/webapp/test_status.py bridge/tests/webapp/test_motion.py bridge/tests/test_main.py
git commit -m "feat(bridge): hardware_status becomes the single source of truth for /api/status"
```

---

## Task 4: I2C reset — `MotionService.restart`

**Files:**
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Test: `bridge/tests/webapp/test_motion.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Produces: `MotionService.restart(client_id) -> dict` (control-gated); WS message `{"t": "restart"}` dispatched through the existing generic `handlers` dict (fire-and-ack, same pattern as `reset`/`standby`).

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/webapp/test_motion.py`:

```python
async def test_restart_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.restart("nobody") == {"error": "not-controlling"}


async def test_restart_schedules_exit_when_controlling(monkeypatch):
    deps = _controlled_deps()
    svc = MotionService(deps)
    monkeypatch.setattr("milo_bridge.webapp.motion.RESTART_DELAY_S", 0.01)
    calls = []
    monkeypatch.setattr("milo_bridge.webapp.motion.os._exit", lambda code: calls.append(code))
    result = await svc.restart("c1")
    assert result == {"ok": True}
    await asyncio.sleep(0.05)
    assert calls == [0]
```

Append to `bridge/tests/webapp/test_ws.py`:

```python
async def test_restart_dispatch_requires_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "restart"})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: FAIL with `AttributeError: 'MotionService' object has no attribute 'restart'` and `{"t": "err", "for": "restart", "error": "unknown-type"}`.

- [ ] **Step 3: Add `MotionService.restart`**

In `bridge/milo_bridge/webapp/motion.py`, add the import (replace the existing import block's first line):

```python
"""Motion commands from web clients: control-checked, clamped, stale-safed."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
```

with:

```python
"""Motion commands from web clients: control-checked, clamped, stale-safed."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path
```

Add the constant near the other module-level constants:

```python
STALE_S = 0.5
ASSETS_FACES = Path(__file__).resolve().parents[2] / "assets" / "faces"
```

becomes:

```python
STALE_S = 0.5
RESTART_DELAY_S = 0.3  # gives the WS ack a moment to flush before the process exits
ASSETS_FACES = Path(__file__).resolve().parents[2] / "assets" / "faces"
```

Insert the new method right after `standby` and before `stop`. Replace:

```python
    async def stop(self) -> dict:
```

with:

```python
    async def restart(self, client_id: str) -> dict:
        """Cleanly exit so systemd's Restart=always brings the service back
        with every I2C driver freshly re-probed -- the recovery path for a
        peripheral that was unplugged and replugged."""
        if err := self._denied(client_id):
            return err
        log.warning("restart requested by %s — exiting for systemd to restart with fresh hardware", client_id)
        asyncio.get_running_loop().call_later(RESTART_DELAY_S, os._exit, 0)
        return {"ok": True}

    async def stop(self) -> dict:
```

- [ ] **Step 4: Dispatch `restart` in `ws.py`**

In `bridge/milo_bridge/webapp/ws.py`, replace:

```python
        "reset": lambda: motion.reset(client_id),
        "standby": lambda: motion.standby(client_id),
    }
```

with:

```python
        "reset": lambda: motion.reset(client_id),
        "standby": lambda: motion.standby(client_id),
        "restart": lambda: motion.restart(client_id),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py
git commit -m "feat(bridge): I2C reset via a control-gated service self-restart"
```

---

## Task 5: Servo Release / Hold

**Files:**
- Modify: `bridge/milo_bridge/drivers/smooth_servos.py`
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Modify: `bridge/tests/webapp/fakes.py`
- Test: `bridge/tests/test_smooth_servos.py`
- Test: `bridge/tests/webapp/test_motion.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Produces: `SmoothServos.hold() -> None` (new); `SmoothServos.relax()` now also snapshots pre-relax targets. `MotionService.relax(client_id)` / `MotionService.hold(client_id)` (control-gated); WS messages `{"t":"relax"}` / `{"t":"hold"}`.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_smooth_servos.py`:

```python
def test_relax_remembers_pre_relax_targets_for_hold():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.set_angle("R1", 120)
    smooth.tick()
    assert driver.last_angle("R1") == 120
    smooth.relax()
    assert driver.last_angle("R1") is None
    smooth.hold()
    smooth.tick()
    assert driver.last_angle("R1") == 120


def test_hold_without_a_prior_relax_is_a_no_op():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.hold()
    smooth.tick()
    assert driver.last_angle("R1") is None
```

Append to `bridge/tests/webapp/test_motion.py`:

```python
async def test_relax_requires_control_and_calls_servos():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.relax("nobody") == {"error": "not-controlling"}
    assert deps.servos.relaxed is False

    deps2 = _controlled_deps()
    svc2 = MotionService(deps2)
    assert await svc2.relax("c1") == {"ok": True}
    assert deps2.servos.relaxed is True


async def test_hold_requires_control_and_calls_servos():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.hold("nobody") == {"error": "not-controlling"}
    assert deps.servos.held is False

    deps2 = _controlled_deps()
    svc2 = MotionService(deps2)
    assert await svc2.hold("c1") == {"ok": True}
    assert deps2.servos.held is True


async def test_relax_and_hold_never_raise_on_driver_error():
    class FailingServos:
        def relax(self):
            raise RuntimeError("relax failed")

        def hold(self):
            raise RuntimeError("hold failed")

    deps = _controlled_deps()
    deps.servos = FailingServos()
    svc = MotionService(deps)
    assert "error" in await svc.relax("c1")
    assert "error" in await svc.hold("c1")
```

Append to `bridge/tests/webapp/test_ws.py`:

```python
async def test_relax_and_hold_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "relax"})
        await _recv_json_until(ws, "ack")
        assert deps.servos.relaxed is True
        await ws.send_json({"t": "hold"})
        await _recv_json_until(ws, "ack")
        assert deps.servos.held is True
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_smooth_servos.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: `test_smooth_servos.py`'s two new tests FAIL with `AttributeError: 'SmoothServos' object has no attribute 'hold'`. The webapp tests FAIL with `AttributeError: 'MotionService' object has no attribute 'relax'` / missing `FakeServos.relaxed`/`.held` attributes.

- [ ] **Step 3: Add `hold()` and pre-relax snapshotting to `SmoothServos`**

In `bridge/milo_bridge/drivers/smooth_servos.py`, replace:

```python
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

DEFAULT_SLEW_DEG_PER_S = 300.0
TICK_HZ = 50
```

with:

```python
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

from .servos import SERVO_NAMES

DEFAULT_SLEW_DEG_PER_S = 300.0
TICK_HZ = 50
```

Replace:

```python
        self._targets: dict[str, float] = {}
        self._last_t: float | None = None
        self._task: asyncio.Task | None = None
```

with:

```python
        self._targets: dict[str, float] = {}
        self._pre_relax_targets: dict[str, float] = {}
        self._last_t: float | None = None
        self._task: asyncio.Task | None = None
```

Replace:

```python
    def relax(self) -> None:
        self._targets.clear()
        self._servos.relax()
```

with:

```python
    def relax(self) -> None:
        self._pre_relax_targets = {
            name: self._servos.last_angle(name)
            for name in SERVO_NAMES
            if self._servos.last_angle(name) is not None
        }
        self._targets.clear()
        self._servos.relax()

    def hold(self) -> None:
        """Re-engage every servo at the angle it was commanded to right
        before the last relax() call. No-op if nothing was ever relaxed."""
        for name, angle in self._pre_relax_targets.items():
            self.set_angle(name, angle)
```

- [ ] **Step 4: Add `MotionService.relax`/`hold`**

In `bridge/milo_bridge/webapp/motion.py`, insert between `restart` and `stop`. Replace:

```python
        asyncio.get_running_loop().call_later(RESTART_DELAY_S, os._exit, 0)
        return {"ok": True}

    async def stop(self) -> dict:
```

with:

```python
        asyncio.get_running_loop().call_later(RESTART_DELAY_S, os._exit, 0)
        return {"ok": True}

    async def relax(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.servos.relax()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def hold(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.servos.hold()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def stop(self) -> dict:
```

- [ ] **Step 5: Dispatch `relax`/`hold` in `ws.py`**

In `bridge/milo_bridge/webapp/ws.py`, replace:

```python
        "restart": lambda: motion.restart(client_id),
    }
```

with:

```python
        "restart": lambda: motion.restart(client_id),
        "relax": lambda: motion.relax(client_id),
        "hold": lambda: motion.hold(client_id),
    }
```

- [ ] **Step 6: Add `relax`/`hold` tracking to `FakeServos`**

In `bridge/tests/webapp/fakes.py`, replace:

```python
class FakeServos:
    def __init__(self):
        self.angles = {}

    def set_angle(self, servo, angle):
        self.angles[servo] = angle

    async def set_pose(self, angles, stagger=True):
        self.angles.update(angles)
```

with:

```python
class FakeServos:
    def __init__(self):
        self.angles = {}
        self.relaxed = False
        self.held = False

    def set_angle(self, servo, angle):
        self.angles[servo] = angle

    async def set_pose(self, angles, stagger=True):
        self.angles.update(angles)

    def relax(self):
        self.relaxed = True

    def hold(self):
        self.held = True
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest bridge/tests/test_smooth_servos.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 8: Commit**

```bash
git add bridge/milo_bridge/drivers/smooth_servos.py bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/fakes.py bridge/tests/test_smooth_servos.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py
git commit -m "feat(bridge): servo Release/Hold via SmoothServos.relax/hold"
```

---

## Task 6: `wake_up` boot pose

**Files:**
- Modify: `bridge/milo_bridge/poses.py`
- Test: `bridge/tests/test_poses.py`

**Interfaces:**
- Produces: `POSES["wake_up"]` — a one-shot pose (`AnimMode.ONCE`, face `"surprised"`), ends at `STAND_ANGLES` (default `end_stand=True`).

- [ ] **Step 1: Write the failing test**

Append to `bridge/tests/test_poses.py`:

```python
def test_wake_up_ends_at_stand():
    servos, _, completed = run_pose("wake_up")
    assert completed
    assert servos.angles == STAND_ANGLES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: FAIL with `KeyError: 'wake_up'`.

- [ ] **Step 3: Add the `wake_up` pose**

In `bridge/milo_bridge/poses.py`, replace:

```python
    "crab": Pose(
```

with:

```python
    "wake_up": Pose(
        "wake_up", "surprised", AnimMode.ONCE,
        [Step(STAND_ANGLES, 150)]
        + _repeat(
            [
                Step({"R1": 150, "L1": 30, "R2": 60, "L2": 150}, 120),
                Step({"R1": 120, "L1": 60, "R2": 30, "L2": 120}, 120),
            ],
            4,
        ),
    ),
    "crab": Pose(
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: all tests PASS (including `test_all_poses_use_known_servo_names_and_valid_angles`, which automatically covers `wake_up` since it iterates every entry in `POSES`).

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/poses.py bridge/tests/test_poses.py
git commit -m "feat(bridge): add the wake_up boot gesture pose"
```

---

## Task 7: Boot status screen + fault-indicating idle face

**Files:**
- Modify: `bridge/milo_bridge/drivers/display.py`
- Test: `bridge/tests/test_display.py`

**Interfaces:**
- Produces: `render_status_image(status: dict[str, bool]) -> Image.Image`; `FaceDisplay.show_status(status: dict[str, bool], seconds: float = 3.0) -> None`; `FaceDisplay.start_idle(base_face: str = "idle") -> None` (was `start_idle()` with no params).

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_display.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_display.py -v`
Expected: FAIL with `AttributeError: module 'milo_bridge.drivers.display' has no attribute 'render_status_image'` and `AttributeError: 'FaceDisplay' object has no attribute 'show_status'`; the existing `test_idle_loop_blinks` still PASSES unmodified (it doesn't pass `base_face`).

- [ ] **Step 3: Add `render_status_image` and `show_status`**

In `bridge/milo_bridge/drivers/display.py`, after `render_pin_image`, insert:

```python
def render_status_image(status: dict[str, bool]) -> Image.Image:
    """Startup checklist: one line per hardware item, OK/FAIL."""
    image = Image.new("1", (WIDTH, HEIGHT), 0)
    draw = ImageDraw.Draw(image)
    draw.text((4, 2), "STARTUP CHECK", fill=1)
    y = 14
    for name, ok in status.items():
        draw.text((4, y), f"{name.upper():<8}{'OK' if ok else 'FAIL'}", fill=1)
        y += 10
    return image
```

- [ ] **Step 4: Add `_idle_base`, `show_status`, and parametrize `start_idle`**

Replace:

```python
        self._cache: dict[str, list[Image.Image]] = {}
        self.current_face: str | None = None
        self._anim_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
```

with:

```python
        self._cache: dict[str, list[Image.Image]] = {}
        self.current_face: str | None = None
        self._idle_base = "idle"
        self._anim_task: asyncio.Task | None = None
        self._idle_task: asyncio.Task | None = None
```

Replace:

```python
    async def show_pin(self, pin: str) -> None:
        self._cancel_anim()
        self.stop_idle()
        self.current_face = None
        self._show(render_pin_image(pin))

    def start_idle(self) -> None:
        """Idle face + random blinking, until stop_idle()."""
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())
```

with:

```python
    async def show_pin(self, pin: str) -> None:
        self._cancel_anim()
        self.stop_idle()
        self.current_face = None
        self._show(render_pin_image(pin))

    async def show_status(self, status: dict[str, bool], seconds: float = 3.0) -> None:
        self._cancel_anim()
        self.stop_idle()
        self.current_face = None
        self._show(render_status_image(status))
        await asyncio.sleep(seconds)

    def start_idle(self, base_face: str = "idle") -> None:
        """Idle face + random blinking, until stop_idle()."""
        if self._idle_task is None or self._idle_task.done():
            self._idle_base = base_face
            self._idle_task = asyncio.create_task(self._idle_loop())
```

Replace:

```python
    async def _idle_loop(self) -> None:
        await self.set_face("idle", AnimMode.BOOMERANG)
        while True:
            await asyncio.sleep(next_blink_delay(self._rng))
            await self._blink()
            if should_double_blink(self._rng):
                await asyncio.sleep(self._rng.uniform(*DOUBLE_BLINK_GAP_S))
                await self._blink()

    async def _blink(self) -> None:
        blink = self._frames("idle_blink")
        for frame in blink:
            self._show(frame)
            await asyncio.sleep(1.0 / DEFAULT_FPS / 2)
        await self.set_face("idle", AnimMode.BOOMERANG)
```

with:

```python
    async def _idle_loop(self) -> None:
        await self.set_face(self._idle_base, AnimMode.BOOMERANG)
        while True:
            await asyncio.sleep(next_blink_delay(self._rng))
            await self._blink()
            if should_double_blink(self._rng):
                await asyncio.sleep(self._rng.uniform(*DOUBLE_BLINK_GAP_S))
                await self._blink()

    async def _blink(self) -> None:
        blink = self._frames("idle_blink")
        for frame in blink:
            self._show(frame)
            await asyncio.sleep(1.0 / DEFAULT_FPS / 2)
        await self.set_face(self._idle_base, AnimMode.BOOMERANG)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/test_display.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/drivers/display.py bridge/tests/test_display.py
git commit -m "feat(bridge): boot status screen and a fault-indicating idle base face"
```

---

## Task 8: Wire the new boot sequence into `main.py`

**Files:**
- Modify: `bridge/milo_bridge/main.py`

**Interfaces:**
- Consumes: `hardware_status` (Task 2), `POSES["wake_up"]` (Task 6), `display.show_status`/`start_idle(base_face=...)` (Task 7).

No new test — this is the composition-root boot sequence, exercised only by the real service (same as Task 2).

- [ ] **Step 1: Replace the boot sequence**

In `bridge/milo_bridge/main.py`, replace:

```python
    await runner.run("rest")
    display.start_idle()
    log.info("resting with idle face; scanning for brains")
```

with:

```python
    await display.show_status(hardware_status)
    await runner.run("wake_up")
    display.start_idle(base_face="idle" if all(hardware_status.values()) else "confused")
    log.info("boot sequence complete; scanning for brains")
```

- [ ] **Step 2: Run the full suite to confirm nothing broke**

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/main.py
git commit -m "feat(bridge): boot shows a status check, wakes up, and flags hardware faults on the face"
```

---

## Task 9: `crab_left`/`crab_right` scripted strafe gaits

**Files:**
- Modify: `bridge/milo_bridge/poses.py`
- Test: `bridge/tests/test_poses.py`

**Interfaces:**
- Produces: `POSES["crab_left"]`, `POSES["crab_right"]` — both cyclic (`cycle=` set, so they're repeatable/abortable like `walk`/`turn_left`/`turn_right`), face `"crab"`. `crab_right` is `crab_left` with every step's servo-name `R`/`L` prefix swapped (same values) — the existing one-shot `crab` pose is untouched.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_poses.py`:

```python
def _swap_lr(angles: dict[str, int]) -> dict[str, int]:
    def swap(name):
        return ("L" if name[0] == "R" else "R") + name[1:]
    return {swap(name): angle for name, angle in angles.items()}


def test_crab_left_and_right_are_cyclic_and_mirrored():
    left, right = POSES["crab_left"], POSES["crab_right"]
    assert left.cycle and right.cycle
    assert len(left.steps) == len(right.steps)
    assert len(left.cycle) == len(right.cycle)
    for l_step, r_step in zip(left.steps + left.cycle, right.steps + right.cycle):
        assert _swap_lr(l_step.updates) == r_step.updates
```

Extend the existing `test_gaits_have_cycles_and_oneshots_do_not`. Replace:

```python
def test_gaits_have_cycles_and_oneshots_do_not():
    for name in ("walk", "walk_backward", "turn_left", "turn_right"):
        assert POSES[name].cycle
    for name in ("wave", "dance", "bow", "rest", "stand"):
        assert not POSES[name].cycle
```

with:

```python
def test_gaits_have_cycles_and_oneshots_do_not():
    for name in ("walk", "walk_backward", "turn_left", "turn_right", "crab_left", "crab_right"):
        assert POSES[name].cycle
    for name in ("wave", "dance", "bow", "rest", "stand", "crab"):
        assert not POSES[name].cycle
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: FAIL with `KeyError: 'crab_left'`.

- [ ] **Step 3: Add `crab_left`/`crab_right`**

In `bridge/milo_bridge/poses.py`, replace:

```python
    "crab": Pose(
        "crab", "crab", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"R1": 90, "R2": 90, "L1": 90, "L2": 90, "R4": 0, "R3": 180, "L3": 45, "L4": 135}, 0)]
        + _repeat(
            [
                Step({"R4": 45, "R3": 135, "L3": 0, "L4": 180}, 300),
                Step({"R4": 0, "R3": 180, "L3": 45, "L4": 135}, 300),
            ],
            5,
        ),
    ),
```

with:

```python
    "crab": Pose(
        "crab", "crab", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"R1": 90, "R2": 90, "L1": 90, "L2": 90, "R4": 0, "R3": 180, "L3": 45, "L4": 135}, 0)]
        + _repeat(
            [
                Step({"R4": 45, "R3": 135, "L3": 0, "L4": 180}, 300),
                Step({"R4": 0, "R3": 180, "L3": 45, "L4": 135}, 300),
            ],
            5,
        ),
    ),
    "crab_left": Pose(
        "crab_left", "crab", AnimMode.ONCE,
        [Step({"R1": 90, "R2": 90, "L1": 90, "L2": 90, "R4": 0, "R3": 180, "L3": 45, "L4": 135}, FRAME_DELAY_MS)],
        cycle=[
            Step({"R4": 45, "R3": 135, "L3": 0, "L4": 180}, FRAME_DELAY_MS),
            Step({"R4": 0, "R3": 180, "L3": 45, "L4": 135}, FRAME_DELAY_MS),
        ],
    ),
    "crab_right": Pose(
        "crab_right", "crab", AnimMode.ONCE,
        [Step({"R1": 90, "R2": 90, "L1": 90, "L2": 90, "R4": 135, "R3": 45, "L3": 180, "L4": 0}, FRAME_DELAY_MS)],
        cycle=[
            Step({"R4": 180, "R3": 0, "L3": 135, "L4": 45}, FRAME_DELAY_MS),
            Step({"R4": 135, "R3": 45, "L3": 180, "L4": 0}, FRAME_DELAY_MS),
        ],
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/poses.py bridge/tests/test_poses.py
git commit -m "feat(bridge): add cyclic crab_left/crab_right strafe gaits"
```

---

## Task 10: `MotionService.turn`/`strafe` — hold-to-continue scripted gaits

**Files:**
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Test: `bridge/tests/webapp/test_motion.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Consumes: `POSES["turn_left"]`/`POSES["turn_right"]` (existing), `POSES["crab_left"]`/`POSES["crab_right"]` (Task 9).
- Produces: `MotionService.turn(client_id, direction) -> dict`, `MotionService.strafe(client_id, direction) -> dict` (both control-gated, share the existing `_pose_task` single-flight guard with `pose()`). WS messages `{"t":"turn","dir":"left"|"right"}` / `{"t":"strafe","dir":"left"|"right"}`. Releasing either reuses the existing `{"t":"stop"}` message (already calls `runner.abort()` unconditionally — no new release-side plumbing).

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/webapp/test_motion.py`:

```python
async def test_turn_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.turn("nobody", "left")
    assert res == {"error": "not-controlling"}
    assert deps.runner.ran == []


async def test_turn_runs_the_matching_pose_with_a_large_cycle_count():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.turn("c1", "left") == {"ok": True}
    await asyncio.sleep(0)
    assert deps.runner.ran == ["turn_left"]


async def test_turn_rejects_unknown_direction():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.turn("c1", "sideways")
    assert "error" in res
    assert deps.runner.ran == []


async def test_turn_shares_the_single_flight_guard_with_pose():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.turn("c1", "left") == {"ok": True}
    res = await svc.turn("c1", "right")
    assert res == {"error": "pose-running"}


async def test_strafe_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.strafe("nobody", "left")
    assert res == {"error": "not-controlling"}
    assert deps.runner.ran == []


async def test_strafe_runs_the_matching_crab_pose():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.strafe("c1", "right") == {"ok": True}
    await asyncio.sleep(0)
    assert deps.runner.ran == ["crab_right"]


async def test_strafe_rejects_unknown_direction():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.strafe("c1", "sideways")
    assert "error" in res
    assert deps.runner.ran == []
```

Append to `bridge/tests/webapp/test_ws.py`:

```python
async def test_turn_and_strafe_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "turn", "dir": "left"})
        await _recv_json_until(ws, "ack")
        assert deps.runner.ran == ["turn_left"]
        await ws.send_json({"t": "stop"})
        await _recv_json_until(ws, "ack")
        assert deps.runner.aborted is True
        await ws.send_json({"t": "strafe", "dir": "right"})
        await _recv_json_until(ws, "ack")
        assert deps.runner.ran == ["turn_left", "crab_right"]
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: FAIL with `AttributeError: 'MotionService' object has no attribute 'turn'`.

- [ ] **Step 3: Add `MotionService.turn`/`strafe`**

In `bridge/milo_bridge/webapp/motion.py`, add the constant near the other module-level constants:

```python
STALE_S = 0.5
RESTART_DELAY_S = 0.3  # gives the WS ack a moment to flush before the process exits
ASSETS_FACES = Path(__file__).resolve().parents[2] / "assets" / "faces"
```

becomes:

```python
STALE_S = 0.5
RESTART_DELAY_S = 0.3  # gives the WS ack a moment to flush before the process exits
ASSETS_FACES = Path(__file__).resolve().parents[2] / "assets" / "faces"
HOLD_CYCLES = 10_000  # effectively "until aborted" -- matches this codebase's own test idiom
```

Insert the two new methods right after `hold` and before `stop`. Replace:

```python
    async def hold(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.servos.hold()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def stop(self) -> dict:
```

with:

```python
    async def hold(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.servos.hold()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def turn(self, client_id: str, direction: str) -> dict:
        if err := self._denied(client_id):
            return err
        if direction not in ("left", "right"):
            return {"error": f"unknown turn direction {direction!r}"}
        if self._pose_task is not None and not self._pose_task.done():
            return {"error": "pose-running"}
        self._pose_task = asyncio.ensure_future(self._deps.runner.run(f"turn_{direction}", cycles=HOLD_CYCLES))
        self._pose_task.add_done_callback(_log_pose_result)
        return {"ok": True}

    async def strafe(self, client_id: str, direction: str) -> dict:
        if err := self._denied(client_id):
            return err
        if direction not in ("left", "right"):
            return {"error": f"unknown strafe direction {direction!r}"}
        if self._pose_task is not None and not self._pose_task.done():
            return {"error": "pose-running"}
        self._pose_task = asyncio.ensure_future(self._deps.runner.run(f"crab_{direction}", cycles=HOLD_CYCLES))
        self._pose_task.add_done_callback(_log_pose_result)
        return {"ok": True}

    async def stop(self) -> dict:
```

- [ ] **Step 4: Dispatch `turn`/`strafe` in `ws.py`**

In `bridge/milo_bridge/webapp/ws.py`, replace:

```python
        "relax": lambda: motion.relax(client_id),
        "hold": lambda: motion.hold(client_id),
    }
```

with:

```python
        "relax": lambda: motion.relax(client_id),
        "hold": lambda: motion.hold(client_id),
        "turn": lambda: motion.turn(client_id, data.get("dir", "")),
        "strafe": lambda: motion.strafe(client_id, data.get("dir", "")),
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py
git commit -m "feat(bridge): turn/strafe drive the actual scripted gaits, held via a large cycle count"
```

---

## Task 11: Stronger, leg-stretch-style balance correction

**Files:**
- Modify: `bridge/milo_bridge/gait/balance.py`
- Test: `bridge/tests/test_balance.py`

**Interfaces:**
- Produces: `correct()` now moves hip **and** knee together per leg (same signed delta, clamped once per leg). `PARAMS` gains larger defaults (`balanced`: `roll_kp=0.6, pitch_kp=0.6, max_correction_deg=25.0`; `angled`: `roll_kp=0.5, pitch_kp=0.5, max_correction_deg=45.0`).

- [ ] **Step 1: Write the failing tests**

In `bridge/tests/test_balance.py`, replace `test_roll_correction_opposes_left_and_right_hips`:

```python
def test_roll_correction_opposes_left_and_right_hips():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=15.0, pitch_deg=0.0, mode="balanced")
    left_delta = result["L1"] - angles["L1"]
    right_delta = result["R1"] - angles["R1"]
    assert left_delta != 0
    assert right_delta != 0
    assert (left_delta > 0) != (right_delta > 0)  # opposite directions
```

with:

```python
def test_roll_correction_opposes_left_and_right_hips_and_knees():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=15.0, pitch_deg=0.0, mode="balanced")
    for left_joint, right_joint in (("L1", "R1"), ("L2", "R2")):
        left_delta = result[left_joint] - angles[left_joint]
        right_delta = result[right_joint] - angles[right_joint]
        assert left_delta != 0
        assert right_delta != 0
        assert (left_delta > 0) != (right_delta > 0)  # opposite directions
```

Replace `test_pitch_correction_opposes_front_and_rear_hips`:

```python
def test_pitch_correction_opposes_front_and_rear_hips():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=15.0, mode="balanced")
    front_delta = result["L1"] - angles["L1"]  # FL
    rear_delta = result["L3"] - angles["L3"]  # RL
    assert front_delta != 0
    assert rear_delta != 0
    assert (front_delta > 0) != (rear_delta > 0)
```

with:

```python
def test_pitch_correction_opposes_front_and_rear_joints():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=15.0, mode="balanced")
    for front_joint, rear_joint in (("L1", "L3"), ("L2", "L4")):  # FL vs RL, hip and knee
        front_delta = result[front_joint] - angles[front_joint]
        rear_delta = result[rear_joint] - angles[rear_joint]
        assert front_delta != 0
        assert rear_delta != 0
        assert (front_delta > 0) != (rear_delta > 0)
```

Replace `test_correction_clamped_to_mode_max`:

```python
def test_correction_clamped_to_mode_max():
    angles = dict(GAIT_NEUTRAL)
    huge = correct(angles, roll_deg=500.0, pitch_deg=0.0, mode="balanced")
    max_c = PARAMS["balanced"].max_correction_deg
    for hip in ("L1", "R1", "L3", "R3"):
        assert abs(huge[hip] - angles[hip]) <= max_c + 1e-6
```

with:

```python
def test_correction_clamped_to_mode_max():
    angles = dict(GAIT_NEUTRAL)
    huge = correct(angles, roll_deg=500.0, pitch_deg=0.0, mode="balanced")
    max_c = PARAMS["balanced"].max_correction_deg
    for joint in ("L1", "R1", "L3", "R3", "L2", "R2", "L4", "R4"):
        assert abs(huge[joint] - angles[joint]) <= max_c + 1e-6
```

Replace `test_combined_roll_and_pitch_correction_stays_within_mode_max`:

```python
def test_combined_roll_and_pitch_correction_stays_within_mode_max():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=999.0, pitch_deg=-999.0, mode="angled")
    max_c = PARAMS["angled"].max_correction_deg
    for hip in ("L1", "R1", "L3", "R3"):
        assert abs(result[hip] - angles[hip]) <= max_c + 1e-6
```

with:

```python
def test_combined_roll_and_pitch_correction_stays_within_mode_max():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=999.0, pitch_deg=-999.0, mode="angled")
    max_c = PARAMS["angled"].max_correction_deg
    for joint in ("L1", "R1", "L3", "R3", "L2", "R2", "L4", "R4"):
        assert abs(result[joint] - angles[joint]) <= max_c + 1e-6
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_balance.py -v`
Expected: the four updated tests FAIL (knee joints currently don't move — `L2`/`R2`/`L4`/`R4` deltas are all `0`); the other five tests (`raw`, `zero_tilt`, `angled_allows_larger`, `result_stays_within_range`, `does_not_mutate`) still PASS.

- [ ] **Step 3: Move hip and knee together; raise the gains**

In `bridge/milo_bridge/gait/balance.py`, replace:

```python
PARAMS: dict[str, BalanceParams] = {
    "balanced": BalanceParams(roll_kp=0.3, pitch_kp=0.3, max_correction_deg=12.0),
    "angled": BalanceParams(roll_kp=0.25, pitch_kp=0.25, max_correction_deg=30.0),
}
```

with:

```python
PARAMS: dict[str, BalanceParams] = {
    "balanced": BalanceParams(roll_kp=0.6, pitch_kp=0.6, max_correction_deg=25.0),
    "angled": BalanceParams(roll_kp=0.5, pitch_kp=0.5, max_correction_deg=45.0),
}
```

Replace:

```python
def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    """Apply IMU-fed roll/pitch trim to ``angles`` (a full hip+knee angle
    dict as produced by CpgGait.angles_at / OnnxPolicy.step). Returns a new
    dict; ``angles`` is never mutated. ``mode="raw"`` (or any mode without
    tuned params) returns ``angles`` unchanged. Each hip's combined
    roll+pitch correction is clamped to ``max_correction_deg`` -- clamping
    the two axes independently before summing them would let a hip's total
    correction reach up to 2x the documented per-mode maximum when both
    roll and pitch are extreme at once."""
    if mode not in PARAMS:
        return angles
    params = PARAMS[mode]
    roll_term = params.roll_kp * roll_deg
    pitch_term = params.pitch_kp * pitch_deg

    corrected = dict(angles)
    for leg, (hip, *_rest) in LEGS.items():
        if hip not in corrected:
            continue
        side = 1.0 if leg[1] == "L" else -1.0  # opposite sign per side
        front = 1.0 if leg[0] == "F" else -1.0  # opposite sign front vs rear
        delta = _clamp(side * roll_term + front * pitch_term, params.max_correction_deg)
        corrected[hip] = max(0.0, min(180.0, corrected[hip] + delta))
    return corrected
```

with:

```python
def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    """Apply IMU-fed roll/pitch trim to ``angles`` (a full hip+knee angle
    dict as produced by CpgGait.angles_at / OnnxPolicy.step). Returns a new
    dict; ``angles`` is never mutated. ``mode="raw"`` (or any mode without
    tuned params) returns ``angles`` unchanged. Hip and knee move together
    per leg (same signed delta) so the reaction reads as "stretch that
    leg out," not a subtle hip rotation -- a hip-only nudge was too weak
    to have real mechanical effect. Each leg's combined roll+pitch
    correction is clamped to ``max_correction_deg`` once (not per-axis) --
    clamping the two axes independently before summing them would let a
    leg's total correction reach up to 2x the documented per-mode
    maximum when both roll and pitch are extreme at once."""
    if mode not in PARAMS:
        return angles
    params = PARAMS[mode]
    roll_term = params.roll_kp * roll_deg
    pitch_term = params.pitch_kp * pitch_deg

    corrected = dict(angles)
    for leg, (hip, knee, *_rest) in LEGS.items():
        if hip not in corrected:
            continue
        side = 1.0 if leg[1] == "L" else -1.0  # opposite sign per side
        front = 1.0 if leg[0] == "F" else -1.0  # opposite sign front vs rear
        delta = _clamp(side * roll_term + front * pitch_term, params.max_correction_deg)
        for joint in (hip, knee):
            if joint in corrected:
                corrected[joint] = max(0.0, min(180.0, corrected[joint] + delta))
    return corrected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_balance.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/gait/balance.py bridge/tests/test_balance.py
git commit -m "feat(bridge): balance correction moves hip and knee together with larger gains"
```

---

## Task 12: Move panel — D-pad replaces the joystick; turn/strafe rewiring

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/move.js`

**Interfaces:**
- Consumes: WS messages `turn`/`strafe`/`stop` (Task 10), `gait` (existing), `mode` (existing).
- Produces: no new exports; UI-only rewrite of the `move` panel.

- [ ] **Step 1: Replace the joystick with a fixed D-pad, split gait vs. scripted-pose input handling**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/panels/move.js` with:

```js
const SEND_MS = 100;
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
          <button class="btn" data-dpad="turnleft" style="font-size:20px;width:56px">↺</button>
          <button class="btn" data-dpad="turnright" style="font-size:20px;width:56px">↻</button>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:220px">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, Q/E to turn</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const speed = el.querySelector("#speed");
    const modeStatus = el.querySelector("#mode-status");
    let vec = { vx: 0 }, timer = null;

    function setModeButtons(name) {
      el.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
      modeStatus.textContent = name === "raw" ? "Mode: Raw" : `Mode: ${MODE_LABEL[name]} — enabled`;
    }
    setModeButtons("raw");
    const offMode = bus.on("mode", (m) => setModeButtons(m.name));
    el.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });

    // -- continuous gait: forward/backward only (turning/strafing now use
    // the scripted turn_left/turn_right/crab_left/crab_right gaits below) --
    function sending(active) {
      if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
      if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
    }
    const scaled = () => ({ vx: vec.vx * (speed.value / 100), vy: 0, yaw: 0 });

    const gaitKeys = { w: 1, s: -1, ArrowUp: 1, ArrowDown: -1 };
    const down = new Set();
    const sync = () => {
      let vx = 0;
      down.forEach((k) => { vx += gaitKeys[k]; });
      vec = { vx: Math.sign(vx) };
      sending(down.size > 0);
    };
    const kd = (e) => { if (gaitKeys[e.key] !== undefined && !e.repeat && e.target.tagName !== "INPUT") { down.add(e.key); sync(); } };
    const ku = (e) => { if (gaitKeys[e.key] !== undefined) { down.delete(e.key); sync(); } };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    function bindGaitButton(dir, key) {
      const btn = el.querySelector(`[data-dpad="${dir}"]`);
      const press = (e) => { e.preventDefault(); down.add(key); sync(); };
      const release = () => { down.delete(key); sync(); };
      btn.addEventListener("pointerdown", press);
      btn.addEventListener("pointerup", release);
      btn.addEventListener("pointerleave", release);
      btn.addEventListener("pointercancel", release);
    }
    bindGaitButton("up", "w");
    bindGaitButton("down", "s");

    // -- turn/strafe: scripted gaits, held via a large cycle count on the
    // server and stopped with the existing universal {t:"stop"} message --
    function bindScripted(dir, msg) {
      const btn = el.querySelector(`[data-dpad="${dir}"]`);
      const press = (e) => { e.preventDefault(); bus.send(msg); };
      const release = () => bus.send({ t: "stop" });
      btn.addEventListener("pointerdown", press);
      btn.addEventListener("pointerup", release);
      btn.addEventListener("pointerleave", release);
      btn.addEventListener("pointercancel", release);
    }
    bindScripted("left", { t: "strafe", dir: "left" });
    bindScripted("right", { t: "strafe", dir: "right" });
    bindScripted("turnleft", { t: "turn", dir: "left" });
    bindScripted("turnright", { t: "turn", dir: "right" });

    const turnKeys = { q: "left", e: "right", ArrowLeft: "left", ArrowRight: "right" };
    const strafeKeys = { a: "left", d: "right" };
    const scriptedDown = new Set();
    const skd = (e) => {
      if (e.repeat || e.target.tagName === "INPUT" || scriptedDown.has(e.key)) return;
      if (turnKeys[e.key]) { scriptedDown.add(e.key); bus.send({ t: "turn", dir: turnKeys[e.key] }); }
      else if (strafeKeys[e.key]) { scriptedDown.add(e.key); bus.send({ t: "strafe", dir: strafeKeys[e.key] }); }
    };
    const sku = (e) => {
      if (turnKeys[e.key] || strafeKeys[e.key]) { scriptedDown.delete(e.key); bus.send({ t: "stop" }); }
    };
    window.addEventListener("keydown", skd);
    window.addEventListener("keyup", sku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => {
      sending(false);
      offMode();
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("keydown", skd);
      window.removeEventListener("keyup", sku);
    };
  },
};
```

- [ ] **Step 2: Run the webapp test suite**

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

Note: this is a UI-only change with no automated behavioral coverage in this repo. Manual check needed on the real dashboard: each D-pad direction, Q/E, A/D, and the arrow keys, confirming turn/strafe stop cleanly on release and forward/backward still respects the speed slider — can't happen in this session.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/move.js
git commit -m "feat(webapp): D-pad replaces the joystick; Q/E and A/D drive scripted turn/strafe gaits"
```

---

## Task 13: Tools — Release/Hold and Restart Bridge buttons

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/servos.js`

**Interfaces:**
- Consumes: WS messages `relax`/`hold` (Task 5), `restart` (Task 4).
- Produces: no new exports; UI-only change to the existing `servos` panel.

- [ ] **Step 1: Add the three buttons**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/panels/servos.js` with:

```js
const SERVOS = ["R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4"];

export default {
  id: "servos", title: "Servo Test", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = SERVOS.map((s) => `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="width:26px;font-weight:600">${s}</span>
        <input type="range" min="0" max="180" value="90" data-servo="${s}" style="flex:1">
        <span data-val="${s}" style="width:34px;text-align:right">90°</span>
      </div>`).join("") +
      `<div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn" id="reset" style="flex:1">Reset (90°)</button>
        <button class="btn" id="standby" style="flex:1">Standby</button>
      </div>
      <div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn" id="release" style="flex:1">Release</button>
        <button class="btn" id="hold" style="flex:1">Hold</button>
      </div>
      <button class="btn danger" id="restart" style="margin-top:8px;width:100%">Restart Bridge (I2C reset)</button>`;
    el.querySelectorAll("input[type=range]").forEach((sl) => {
      sl.oninput = () => {
        el.querySelector(`[data-val="${sl.dataset.servo}"]`).textContent = `${sl.value}°`;
        bus.send({ t: "servo", servo: sl.dataset.servo, deg: Number(sl.value) });
      };
    });
    el.querySelector("#reset").onclick = () => {
      SERVOS.forEach((s) => {
        el.querySelector(`[data-servo="${s}"]`).value = 90;
        el.querySelector(`[data-val="${s}"]`).textContent = "90°";
      });
      bus.send({ t: "reset" });
    };
    el.querySelector("#standby").onclick = () => bus.send({ t: "standby" });
    el.querySelector("#release").onclick = () => bus.send({ t: "relax" });
    el.querySelector("#hold").onclick = () => bus.send({ t: "hold" });
    el.querySelector("#restart").onclick = () => {
      if (confirm("Restart the bridge service? Every connected tab will briefly disconnect.")) {
        bus.send({ t: "restart" });
      }
    };
  },
};
```

- [ ] **Step 2: Run the static-integrity and full test suites**

Run: `pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

Note: this is a UI-only change with no automated behavioral coverage in this repo. Manual check needed: Release lets a leg be repositioned by hand, Hold smoothly returns it to where it was, Restart Bridge shows the confirm dialog and the tab reconnects a few seconds later — can't happen in this session.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/servos.js
git commit -m "feat(webapp): add Release/Hold and Restart Bridge buttons to Tools"
```

---

## Final verification

- [ ] Run the complete suite once more from the repo root: `pytest bridge/tests -v`. Expected: all tests PASS.
- [ ] On the real robot: unplug and replug the OLED (or another I2C device) and confirm the web dashboard still comes up and the idle face shows "confused"; use the Restart Bridge button and confirm the service comes back with the peripheral now working and the face back to normal. Verify boot performs the wake_up wiggle and settles into stand. Verify Q/E and A/D turn/strafe correctly and stop cleanly on release. Verify Release/Hold on a leg. Tune `BalanceParams` in `gait/balance.py` if the reaction is still too weak, too strong, or backwards in sign — this cannot be validated off-hardware.
