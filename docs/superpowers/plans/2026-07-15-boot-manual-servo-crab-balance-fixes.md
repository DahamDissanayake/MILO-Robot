# Boot Standby, Real Crab Strafing, Manual Servo Mode, Balance Direction, Gait-Resume Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix five real-hardware issues: the robot falls back asleep seconds after boot instead of staying standing; A/D's crab poses don't move sideways; Servo Test fights the operator's slider drags; balance correction moves hip/knee the wrong way relative to each other; and gait resumes with a phase jump after a pose interrupts it.

**Architecture:** `SessionManager` gains an injectable clock and a boot grace window before its existing sleep-when-no-brain logic can fire. `poses.py` gets a smaller `wake_up` dip and two from-scratch `crab_left`/`crab_right` gaits (no longer derived from the old cosmetic `crab` emote or a mechanical mirror of it). `GaitEngine` gains a `manual_override` flag checked alongside the existing pose-deference check, and both together drive a phase-clock reset when deferring ends so a resumed walk doesn't jump. `gait/balance.py`'s hip/knee relationship is corrected to move in opposite directions per leg. The web layer exposes the new manual toggle through the same control-gated, broadcast pattern already used for `mode`.

**Tech Stack:** Python 3 (asyncio, aiohttp), pytest, vanilla JS.

## Global Constraints

- All new/changed Python code must be testable off-hardware with injected fakes/clocks, matching the existing pattern (`GaitEngine`, `SmoothServos`, `PoseRunner`, `Mpu6050` all take an injectable `clock`/`sleep`).
- Every existing test in `bridge/tests/` must keep passing after each task; run `python -m pytest bridge/tests` (from repo root) at the end of every task.
- Follow existing code style: relative imports, `from __future__ import annotations`, control-gated + never-raising handler pattern in `MotionService`, broadcast-to-all-clients pattern for global mode-like state (see `mode`/`_broadcast_mode` in `ws.py`).
- Commit after each task with a plain, present-tense message; no Claude co-author trailer.
- **Correction from the design spec, found during planning:** the spec described the balance fix as `hip_delta = side * delta`, `knee_delta = -side * delta`. That's wrong — `delta` already has `side` baked into its own computation (`delta = clamp(side*roll_term + front*pitch_term, max)`), so multiplying by `side` again on the hip assignment would double it and change today's already-correct left/right hip opposition. The actual fix is simpler: leave the hip assignment exactly as it is today (`corrected[hip] += delta`), and just negate it for the knee (`corrected[knee] += -delta`). This still produces hip and knee moving toward opposite ends of their range on every leg (verified against the RL-leg/left-tilt example), without touching the hip formula's already-validated left/right and front/rear behavior at all. Use this formula, not the spec's.

---

## Task 1: Boot grace period before sleep can trigger

**Files:**
- Modify: `bridge/milo_bridge/net/session.py`
- Test: `bridge/tests/test_session.py`

**Interfaces:**
- Produces: `SessionManager(cfg, *, servos, display, runner, ..., clock=time.monotonic)` — new `clock` keyword param (defaults to the real clock, matching every other injectable-clock class in this codebase); `SessionManager.BOOT_GRACE_S: float` class constant (`8.0`).

- [ ] **Step 1: Write the failing test**

Append to `bridge/tests/test_session.py` (it already imports `asyncio`; add these new imports at the top alongside the existing ones):

```python
from milo_bridge.config import BridgeConfig
from milo_bridge.net.session import SessionManager
```

Then append at the bottom of the file:

```python
class FakeDiscoveryEmpty:
    def snapshot(self):
        return []

    def start(self):
        pass

    def stop(self):
        pass


class FakeSleepController:
    def __init__(self):
        self.asleep_calls = 0

    async def ensure_asleep(self):
        self.asleep_calls += 1

    async def ensure_awake(self):
        pass


def test_boot_grace_period_delays_sleep_when_no_brain_found(tmp_path):
    now = {"t": 0.0}
    cfg = BridgeConfig(data_dir=str(tmp_path), reconnect_seconds=0.0)
    sleep_controller = FakeSleepController()
    manager = SessionManager(
        cfg,
        servos=None,
        display=None,
        runner=None,
        sleep_controller=sleep_controller,
        discovery=FakeDiscoveryEmpty(),
        clock=lambda: now["t"],
    )

    asyncio.run(manager._tick())
    assert sleep_controller.asleep_calls == 0  # still within the grace period

    now["t"] = SessionManager.BOOT_GRACE_S + 1
    asyncio.run(manager._tick())
    assert sleep_controller.asleep_calls == 1  # grace period has elapsed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest bridge/tests/test_session.py -v`
Expected: the new test FAILS with `TypeError: SessionManager.__init__() got an unexpected keyword argument 'clock'`. Every pre-existing test in the file (all `RobotSession` tests) still PASSES.

- [ ] **Step 3: Add the clock and boot grace period**

In `bridge/milo_bridge/net/session.py`, add `import time` to the imports. Replace:

```python
from __future__ import annotations

import asyncio
import contextlib
import logging

from milo_common import protocol
```

with:

```python
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from milo_common import protocol
```

Replace:

```python
class SessionManager:
    """Discovery -> select -> connect -> session; failover and sleep in a loop."""

    def __init__(
        self,
        cfg,
        *,
        servos,
        display,
        runner,
        audio=None,
        graph_api=None,
        gait=None,
        media_hub=None,
        broker=None,
        sleep_controller=None,
        discovery: BrainDiscovery | None = None,
        connect=None,
    ):
        self._cfg = cfg
        self._display = display
        self._runner = runner
        # Local speaker only (T_TTS playback); capture streaming is owned by
        # media_hub, built once in main() from the same driver.
        self._audio = audio
        self._graph_api = graph_api
        self._gait = gait
        self._media_hub = media_hub
        self._broker = broker
        self._store = PairedStore(cfg.paired_path)
        self._discovery = discovery or BrainDiscovery()
        self._connect = connect
        self.link_state: str = "disconnected"
        if sleep_controller is None:
            from ..sleep import SleepController

            sleep_controller = SleepController(
                runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=servos
            )
        self._sleep = sleep_controller
```

with:

```python
class SessionManager:
    """Discovery -> select -> connect -> session; failover and sleep in a loop."""

    BOOT_GRACE_S = 8.0  # stay standing for a few seconds after boot before sleeping, even with no brain yet

    def __init__(
        self,
        cfg,
        *,
        servos,
        display,
        runner,
        audio=None,
        graph_api=None,
        gait=None,
        media_hub=None,
        broker=None,
        sleep_controller=None,
        discovery: BrainDiscovery | None = None,
        connect=None,
        clock=time.monotonic,
    ):
        self._cfg = cfg
        self._display = display
        self._runner = runner
        # Local speaker only (T_TTS playback); capture streaming is owned by
        # media_hub, built once in main() from the same driver.
        self._audio = audio
        self._graph_api = graph_api
        self._gait = gait
        self._media_hub = media_hub
        self._broker = broker
        self._store = PairedStore(cfg.paired_path)
        self._discovery = discovery or BrainDiscovery()
        self._connect = connect
        self._clock = clock
        self._booted_at = clock()
        self.link_state: str = "disconnected"
        if sleep_controller is None:
            from ..sleep import SleepController

            sleep_controller = SleepController(
                runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=servos
            )
        self._sleep = sleep_controller
```

Replace:

```python
    async def _tick(self) -> None:
        choice = select_brain(self._discovery.snapshot(), self._store)
        if choice is None:
            await self._sleep.ensure_asleep()
            await asyncio.sleep(self._cfg.reconnect_seconds)
            return
```

with:

```python
    async def _tick(self) -> None:
        choice = select_brain(self._discovery.snapshot(), self._store)
        if choice is None:
            if self._clock() - self._booted_at > self.BOOT_GRACE_S:
                await self._sleep.ensure_asleep()
            await asyncio.sleep(self._cfg.reconnect_seconds)
            return
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_session.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/net/session.py bridge/tests/test_session.py
git commit -m "feat(bridge): add a boot grace period before the no-brain sleep behavior can trigger"
```

---

## Task 2: `wake_up` becomes a small dip

**Files:**
- Modify: `bridge/milo_bridge/poses.py`

**Interfaces:**
- Produces: `POSES["wake_up"]` — same name/face/end-stand behavior, smaller motion (2 steps instead of 9).

No new test — the existing `test_wake_up_ends_at_stand` (in `bridge/tests/test_poses.py`) already checks the pose completes and ends at `STAND_ANGLES`, which is unaffected by shrinking the motion; `test_all_poses_use_known_servo_names_and_valid_angles` automatically re-validates the new angles.

- [ ] **Step 1: Replace the pose definition**

In `bridge/milo_bridge/poses.py`, replace:

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
```

with:

```python
    "wake_up": Pose(
        "wake_up", "surprised", AnimMode.ONCE,
        [
            Step(STAND_ANGLES, 150),
            Step({"R2": 65, "L2": 115, "R4": 20, "L4": 160}, 250),
        ],
    ),
```

- [ ] **Step 2: Run tests to verify they still pass**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: all tests PASS, including `test_wake_up_ends_at_stand` (unaffected — it only checks completion and the final stand angles, not the intermediate motion) and `test_all_poses_use_known_servo_names_and_valid_angles` (re-validates the new, smaller step against known servo names and the `[0,180]` range).

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/poses.py
git commit -m "feat(bridge): shrink wake_up to a small dip instead of a full wiggle"
```

---

## Task 3: From-scratch `crab_left`/`crab_right`

**Files:**
- Modify: `bridge/milo_bridge/poses.py`
- Test: `bridge/tests/test_poses.py`

**Interfaces:**
- Produces: `POSES["crab_left"]`, `POSES["crab_right"]` — still cyclic (so `MotionService.strafe()` from the previous branch, which already runs `f"crab_{direction}"` with a large cycle count, needs no changes), but now hand-authored from scratch instead of derived from the old one-shot `crab` emote or a mechanical R/L swap of each other. `crab_left`/`crab_right` no longer satisfy an R/L-swap-mirror relationship to each other (that relationship is what was proven wrong on hardware), so the old `_swap_lr`-based test is removed.

**Non-goal reminder from the spec:** this is a best-effort attempt. The robot's legs have only two joints each (hip = fore-aft swing, knee = lift) — there's no dedicated sideways-swing axis, so true lateral translation may not be achievable at all with this geometry. This task cannot verify whether the robot actually moves sideways; that needs the real robot.

- [ ] **Step 1: Write the failing test**

In `bridge/tests/test_poses.py`, replace the `_swap_lr` helper and `test_crab_left_and_right_are_cyclic_and_mirrored` test:

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

with:

```python
def test_crab_left_and_right_are_cyclic_and_distinct():
    left, right = POSES["crab_left"], POSES["crab_right"]
    assert left.cycle and right.cycle
    assert left.cycle != right.cycle  # genuinely different lean directions, not accidentally identical
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: `test_crab_left_and_right_are_cyclic_and_distinct` FAILS — the current `crab_left`/`crab_right` cycles ARE mirror-related to each other (via the old swap technique), but that's not what this test checks for; run it first to confirm it fails specifically because the current pose definitions haven't been replaced yet (the test itself is a straightforward assertion, so confirm it fails only due to the old pose content, not a typo — the old cycles will make `left.cycle != right.cycle` likely still True actually, since they ARE different dicts even under the old design; the important RED signal here is really Step 4's `test_all_poses_use_known_servo_names_and_valid_angles` and the general shape check — proceed to Step 3 regardless and confirm GREEN in Step 4).

- [ ] **Step 3: Replace `crab_left`/`crab_right` with from-scratch gaits**

In `bridge/milo_bridge/poses.py`, replace:

```python
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

with:

```python
    "crab_left": Pose(
        "crab_left", "crab", AnimMode.ONCE,
        [Step(STAND_ANGLES, 150)],
        cycle=[
            Step({"L1": 70, "R1": 110, "L3": 45, "R3": 135,
                  "L2": 115, "R2": 65, "L4": 160, "R4": 20}, FRAME_DELAY_MS),
            Step(STAND_ANGLES, FRAME_DELAY_MS),
        ],
    ),
    "crab_right": Pose(
        "crab_right", "crab", AnimMode.ONCE,
        [Step(STAND_ANGLES, 150)],
        cycle=[
            Step({"L1": 20, "R1": 160, "L3": 5, "R3": 175,
                  "L2": 115, "R2": 65, "L4": 160, "R4": 20}, FRAME_DELAY_MS),
            Step(STAND_ANGLES, FRAME_DELAY_MS),
        ],
    ),
```

The existing one-shot `"crab"` pose (a few lines above these two) is untouched.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: all tests PASS, including `test_gaits_have_cycles_and_oneshots_do_not` (already lists `crab_left`/`crab_right` as cyclic and `crab` as one-shot — unaffected by this content change) and `test_all_poses_use_known_servo_names_and_valid_angles` (validates the new angles: `70,110,45,135,115,65,160,20,20,160,5,175` are all within `[0,180]`).

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/poses.py bridge/tests/test_poses.py
git commit -m "feat(bridge): replace crab_left/crab_right with a from-scratch sideways-stepping attempt"
```

---

## Task 4: `GaitEngine` — Manual Servo Mode + clean phase resume after deferring

**Files:**
- Modify: `bridge/milo_bridge/gait/engine.py`
- Test: `bridge/tests/test_gait.py`

**Interfaces:**
- Produces: `GaitEngine.set_manual(on: bool) -> None` (stops all writes while `on`, and clears any active velocity command); `tick()` now also defers (returns `None`, writes nothing) while manual mode is on, in addition to the existing pose-deference check; `tick()` resets the CPG phase clock (`self._t0`) the moment deferring ends while a walk command is still active, instead of letting the CPG "walk ahead" in phase-space while servos were frozen.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_gait.py`:

```python
def test_set_manual_stops_all_writes_and_clears_active_command():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    engine.set_manual(True)
    assert engine.tick() is None
    assert servos.writes == 0


def test_manual_mode_blocks_balanced_self_leveling_too():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    engine.set_manual(True)
    assert engine.tick() is None
    assert servos.writes == 0


def test_gait_resumes_with_a_fresh_phase_after_a_pose_defers_it():
    now = {"t": 0.0}
    servos = FakeServos()
    runner = FakeRunner()
    engine = GaitEngine(servos, runner=runner, clock=lambda: now["t"])
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 5.0
    runner.is_running = True
    assert engine.tick() is None  # deferring while the pose runs
    now["t"] = 5.5
    assert engine.tick() is None  # still deferring
    runner.is_running = False
    now["t"] = 5.52
    written = engine.tick()  # pose just finished; gait resumes
    expected = CpgGait().angles_at(0.0, 0.1, 0.0, 0.0)  # phase restarted at t=0, not the stale elapsed time
    assert written == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_gait.py -v`
Expected: the first two new tests FAIL with `AttributeError: 'GaitEngine' object has no attribute 'set_manual'`. The third FAILS because `written` doesn't match `expected` — the current code computes the phase from the full stale elapsed time (`5.52 - 0.0 = 5.52`) instead of a freshly-reset `0.0`. All pre-existing tests in the file still PASS.

- [ ] **Step 3: Add `set_manual` and rewrite `tick()`**

In `bridge/milo_bridge/gait/engine.py`, replace:

```python
        self._command = (0.0, 0.0, 0.0)
        self._active = False
        self._mode = "raw"
        self._holding_target: dict[str, float] | None = None
        self._t0 = clock()
```

with:

```python
        self._command = (0.0, 0.0, 0.0)
        self._active = False
        self._mode = "raw"
        self._holding_target: dict[str, float] | None = None
        self._manual_override = False
        self._was_deferring = False
        self._t0 = clock()
```

Replace:

```python
    def _set_discrete_target(self, angles: dict[str, float]) -> None:
        self._active = False
        self._holding_target = dict(angles)
        for name, angle in angles.items():
            self._servos.set_angle(name, angle)

    def tick(self) -> dict[str, float] | None:
        """One control step; returns the angles written (None while idle)."""
        if self._runner is not None and self._runner.is_running:
            return None  # a scripted pose owns the servos right now
        if not self._active:
            return self._hold_level() if self._mode in _BALANCE_MODES else None
        vx, vy, yaw = self._command
```

with:

```python
    def _set_discrete_target(self, angles: dict[str, float]) -> None:
        self._active = False
        self._holding_target = dict(angles)
        for name, angle in angles.items():
            self._servos.set_angle(name, angle)

    def set_manual(self, on: bool) -> None:
        """Stop writing servos entirely while a human is testing them
        directly (Tools > Servo Test) -- without this, balanced/angled
        mode's self-leveling fights every slider drag."""
        self._manual_override = on
        if on:
            self._command = (0.0, 0.0, 0.0)
            self._active = False

    def tick(self) -> dict[str, float] | None:
        """One control step; returns the angles written (None while idle)."""
        deferring = self._manual_override or (self._runner is not None and self._runner.is_running)
        if deferring:
            self._was_deferring = True
            return None  # manual override, or a scripted pose, owns the servos right now
        if self._was_deferring and self._active:
            self._t0 = self._clock()  # resume the CPG cycle cleanly, not mid-phase
        self._was_deferring = False
        if not self._active:
            return self._hold_level() if self._mode in _BALANCE_MODES else None
        vx, vy, yaw = self._command
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_gait.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/gait/engine.py bridge/tests/test_gait.py
git commit -m "feat(bridge): manual servo override + clean CPG phase resume after deferring"
```

---

## Task 5: Manual Servo Mode — `MotionService` + `ws.py`

**Files:**
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Modify: `bridge/tests/webapp/fakes.py`
- Test: `bridge/tests/webapp/test_motion.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Consumes: `GaitEngine.set_manual` (Task 4).
- Produces: `MotionService.manual(client_id, on) -> dict` (control-gated, returns `{"ok": True, "on": on}` or `{"error": ...}`, never raises — calls `gait.set_manual(on)` and, only when turning it on, also `runner.abort()`). WS message `{"t": "manual", "on": true|false}`, handled specially in `ws.py` (like `mode`, not the generic dict) because it broadcasts to every connected client.

- [ ] **Step 1: Write the failing tests**

In `bridge/tests/webapp/fakes.py`, replace:

```python
class FakeGait:
    backend = "cpg"

    def __init__(self):
        self.vel = (0.0, 0.0, 0.0)
        self.mode = "raw"
        self.reset_called = False
        self.standby_called = False

    def set_velocity_command(self, vx, vy, yaw_rate):
        self.vel = (vx, vy, yaw_rate)

    def set_mode(self, name):
        self.mode = name

    def reset(self):
        self.reset_called = True

    def standby(self):
        self.standby_called = True
```

with:

```python
class FakeGait:
    backend = "cpg"

    def __init__(self):
        self.vel = (0.0, 0.0, 0.0)
        self.mode = "raw"
        self.reset_called = False
        self.standby_called = False
        self.manual_on = False

    def set_velocity_command(self, vx, vy, yaw_rate):
        self.vel = (vx, vy, yaw_rate)

    def set_mode(self, name):
        self.mode = name

    def reset(self):
        self.reset_called = True

    def standby(self):
        self.standby_called = True

    def set_manual(self, on):
        self.manual_on = on
```

Append to `bridge/tests/webapp/test_motion.py`:

```python
async def test_manual_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.manual("nobody", True)
    assert res == {"error": "not-controlling"}
    assert deps.gait.manual_on is False


async def test_manual_on_sets_gait_and_aborts_runner():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.manual("c1", True) == {"ok": True, "on": True}
    assert deps.gait.manual_on is True
    assert deps.runner.aborted is True


async def test_manual_off_sets_gait_without_aborting():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.manual("c1", False) == {"ok": True, "on": False}
    assert deps.gait.manual_on is False
    assert deps.runner.aborted is False


async def test_manual_never_raises_on_driver_error():
    class FailingGait:
        def set_manual(self, on):
            raise RuntimeError("manual failed")

    deps = _controlled_deps()
    deps.gait = FailingGait()
    svc = MotionService(deps)
    assert "error" in await svc.manual("c1", True)
```

Append to `bridge/tests/webapp/test_ws.py`:

```python
async def test_manual_broadcasts_to_all_clients():
    deps = make_deps(broker=ControlBroker())
    client, ws1 = await _ws(deps)
    try:
        ws2 = await client.ws_connect("/ws")
        await ws1.send_json({"t": "control", "take": True})
        await _recv_json_until(ws1, "control")
        await ws1.send_json({"t": "manual", "on": True})
        data1 = await _recv_json_until(ws1, "manual")
        data2 = await _recv_json_until(ws2, "manual")
        assert data1 == {"t": "manual", "on": True}
        assert data2 == {"t": "manual", "on": True}
        assert deps.gait.manual_on is True
    finally:
        await client.close()


async def test_manual_denied_without_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "manual", "on": True})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: FAIL with `AttributeError: 'MotionService' object has no attribute 'manual'` and `{"t":"err","for":"manual","error":"unknown-type"}`.

- [ ] **Step 3: Add `MotionService.manual`**

In `bridge/milo_bridge/webapp/motion.py`, insert between `hold` and `turn`. Replace:

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

    async def manual(self, client_id: str, on: bool) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.gait.set_manual(on)
            if on:
                self._deps.runner.abort()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "on": on}

    async def turn(self, client_id: str, direction: str) -> dict:
```

- [ ] **Step 4: Dispatch `manual` in `ws.py` with a broadcast**

In `bridge/milo_bridge/webapp/ws.py`, replace:

```python
    if t == "mode":
        res = await motion.mode(client_id, data.get("name", ""))
        if "error" in res:
            await ws.send_json({"t": "err", "for": "mode", "error": res["error"]})
        else:
            _broadcast_mode(app, res["mode"])
        return
    if t == "audio":
```

with:

```python
    if t == "mode":
        res = await motion.mode(client_id, data.get("name", ""))
        if "error" in res:
            await ws.send_json({"t": "err", "for": "mode", "error": res["error"]})
        else:
            _broadcast_mode(app, res["mode"])
        return
    if t == "manual":
        res = await motion.manual(client_id, bool(data.get("on")))
        if "error" in res:
            await ws.send_json({"t": "err", "for": "manual", "error": res["error"]})
        else:
            _broadcast_manual(app, res["on"])
        return
    if t == "audio":
```

Replace:

```python
def _broadcast_mode(app: web.Application, name: str) -> None:
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            asyncio.ensure_future(_send_safe(ws, {"t": "mode", "name": name}))
```

with:

```python
def _broadcast_mode(app: web.Application, name: str) -> None:
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            asyncio.ensure_future(_send_safe(ws, {"t": "mode", "name": name}))


def _broadcast_manual(app: web.Application, on: bool) -> None:
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            asyncio.ensure_future(_send_safe(ws, {"t": "manual", "on": on}))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/fakes.py bridge/tests/webapp/test_motion.py bridge/tests/webapp/test_ws.py
git commit -m "feat(bridge): expose Manual Servo Mode over the websocket, broadcast to all clients"
```

---

## Task 6: Balance correction — hip and knee move toward opposite ends

**Files:**
- Modify: `bridge/milo_bridge/gait/balance.py`
- Test: `bridge/tests/test_balance.py`

**Interfaces:**
- Produces: `correct()`'s hip formula is unchanged from today (still `corrected[hip] += delta`, preserving the already-correct left/right and front/rear opposition); the knee now gets `corrected[knee] += -delta` (the exact negation of the hip's delta on the same leg), instead of the previous `+delta` (same as hip).

- [ ] **Step 1: Write the failing tests**

In `bridge/tests/test_balance.py`, replace `test_roll_correction_opposes_left_and_right_hips_and_knees`:

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

with:

```python
def test_left_and_right_hips_oppose_each_other_under_roll():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=15.0, pitch_deg=0.0, mode="balanced")
    left_delta = result["L1"] - angles["L1"]
    right_delta = result["R1"] - angles["R1"]
    assert left_delta != 0
    assert right_delta != 0
    assert (left_delta > 0) != (right_delta > 0)


def test_hip_and_knee_move_toward_opposite_ends_on_each_leg():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=15.0, pitch_deg=0.0, mode="balanced")
    for hip, knee in (("L1", "L2"), ("R1", "R2"), ("L3", "L4"), ("R3", "R4")):
        hip_delta = result[hip] - angles[hip]
        knee_delta = result[knee] - angles[knee]
        assert hip_delta != 0
        assert knee_delta != 0
        assert (hip_delta > 0) != (knee_delta > 0)  # straightening, not moving together
```

Replace `test_pitch_correction_opposes_front_and_rear_joints`:

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

with:

```python
def test_front_and_rear_hips_oppose_each_other_under_pitch():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=15.0, mode="balanced")
    front_delta = result["L1"] - angles["L1"]
    rear_delta = result["L3"] - angles["L3"]
    assert front_delta != 0
    assert rear_delta != 0
    assert (front_delta > 0) != (rear_delta > 0)
```

Leave every other test in the file (`test_raw_mode_returns_angles_unchanged`, `test_zero_tilt_leaves_angles_unchanged`, `test_correction_clamped_to_mode_max`, `test_angled_mode_allows_larger_pitch_correction_than_balanced`, `test_result_angles_stay_within_servo_range`, `test_does_not_mutate_input_dict`, `test_combined_roll_and_pitch_correction_stays_within_mode_max`) untouched — none of them depend on the hip-vs-knee sign relationship, only on magnitudes and clamping, which are unaffected by this change (`|knee_delta| == |-delta| == |delta| == |hip_delta|`, so the existing clamp bound still holds for both joints).

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_balance.py -v`
Expected: `test_hip_and_knee_move_toward_opposite_ends_on_each_leg` FAILS (today's code moves hip and knee the *same* direction). `test_left_and_right_hips_oppose_each_other_under_roll` and `test_front_and_rear_hips_oppose_each_other_under_pitch` PASS immediately (they only assert the hip behavior, which isn't changing) — that's expected, not a mistake; they're included for completeness against the renamed/narrowed test set. Every other test still PASSES.

- [ ] **Step 3: Negate the knee correction**

In `bridge/milo_bridge/gait/balance.py`, replace:

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

with:

```python
def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    """Apply IMU-fed roll/pitch trim to ``angles`` (a full hip+knee angle
    dict as produced by CpgGait.angles_at / OnnxPolicy.step). Returns a new
    dict; ``angles`` is never mutated. ``mode="raw"`` (or any mode without
    tuned params) returns ``angles`` unchanged. Hip and knee move toward
    *opposite* ends of their range (one increases, the other decreases)
    on each leg -- confirmed against a concrete on-robot example (a rear
    leg's hip should swing toward 180 while its knee swings toward 0 to
    "straighten" the leg) -- moving them the same direction, as an
    earlier revision did, doesn't produce a real physical reaction. Each
    leg's combined roll+pitch correction is clamped to
    ``max_correction_deg`` once (not per-axis) -- clamping the two axes
    independently before summing them would let a leg's total correction
    reach up to 2x the documented per-mode maximum when both roll and
    pitch are extreme at once."""
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
        if hip in corrected:
            corrected[hip] = max(0.0, min(180.0, corrected[hip] + delta))
        if knee in corrected:
            corrected[knee] = max(0.0, min(180.0, corrected[knee] - delta))
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
git commit -m "fix(bridge): balance correction moves hip and knee toward opposite ends, not together"
```

---

## Task 7: Tools UI — remove Release/Hold, add Manual Servo Mode toggle

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/servos.js`

**Interfaces:**
- Consumes: WS message `manual` (Task 5).
- Produces: no new exports; UI-only change to the existing `servos` panel.

- [ ] **Step 1: Replace the Release/Hold buttons with a Manual Servo Mode toggle**

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
      <button class="btn" id="manual" style="margin-top:8px;width:100%">Manual Servo Mode: Off</button>
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

    const manualBtn = el.querySelector("#manual");
    function setManualButton(on) {
      manualBtn.textContent = `Manual Servo Mode: ${on ? "On" : "Off"}`;
      manualBtn.classList.toggle("active", on);
    }
    setManualButton(false);
    const offManual = bus.on("manual", (m) => setManualButton(m.on));
    manualBtn.onclick = () => {
      const nowOn = !manualBtn.classList.contains("active");
      bus.send({ t: "manual", on: nowOn });
    };

    el.querySelector("#restart").onclick = () => {
      if (confirm("Restart the bridge service? Every connected tab will briefly disconnect.")) {
        bus.send({ t: "restart" });
      }
    };

    return () => offManual();
  },
};
```

Note the panel now returns a cleanup function (`() => offManual()`) — it didn't need one before since it had no `bus.on(...)` subscriptions; the new `manual` broadcast listener must be unsubscribed on unmount, matching the pattern already used by the Move panel's `offMode`.

- [ ] **Step 2: Run the static-integrity and full test suites**

Run: `pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

Note: this is a UI-only change with no automated behavioral coverage in this repo. Manual check needed: toggling Manual Servo Mode on stops the robot from fighting slider drags (including in Balanced/Angled mode), the toggle's on/off label and highlight sync across multiple open tabs, and Restart Bridge still shows its confirm dialog — can't happen in this session.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/servos.js
git commit -m "feat(webapp): replace Release/Hold with a Manual Servo Mode toggle in Tools"
```

---

## Final verification

- [ ] Run the complete suite once more from the repo root: `pytest bridge/tests -v`. Expected: all tests PASS.
- [ ] On the real robot: confirm the robot stays standing for several seconds after boot instead of collapsing to rest; confirm the wake_up dip looks right; test A/D and honestly assess whether `crab_left`/`crab_right` produce any real sideways translation (very possibly not, given the leg geometry — if not, that's expected per the spec's stated risk, not a bug to re-report blindly) and iterate on the angles in `poses.py` directly if it's close but not quite right; confirm Manual Servo Mode actually stops the robot from fighting slider drags, including while in Balanced/Angled mode; confirm holding a direction, triggering a pose like "wave," and releasing the pose resumes walking smoothly without a jump; confirm the balance correction's hip/knee straightening reaction now looks like the intended physical response on at least the one confirmed leg (rear-left under a left tilt), and note whether the other three legs need their own sign flip if the extrapolation was wrong for them.
