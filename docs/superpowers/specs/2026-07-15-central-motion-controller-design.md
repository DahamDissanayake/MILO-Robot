# Central Motion Controller, Balance Modes & Servo Calibration Fix

**Date:** 2026-07-15
**Status:** Approved for planning

## Problem

Three related issues on the physical robot:

1. **Servo range bug.** In the Tools > Servo Test panel, sliding a servo to 0° or 180° drives it to the wrong physical angle instead of that servo's true mechanical extreme.
2. **Movement is not smooth.** Poses, gait transitions, and manual servo commands all snap servos instantly to their target angle (only staggered a few ms apart across servos, never interpolated over time), which reads as jerky/violent motion.
3. **No unified motion coordinator.** There's no single place that owns "what should each servo be doing right now" across gait, poses, and manual commands, and no way to enable IMU-driven self-leveling while walking or climbing.

## Goals

- Fix the servo slider so 0° and 180° always reach the servo's true calibrated physical extreme.
- Make all servo motion (poses, gait, manual commands) interpolate smoothly instead of snapping.
- Give the robot three selectable motion modes — Raw (today's behavior, no help), Balanced (IMU-corrected level walking), Angled (IMU-corrected climbing) — usable identically whether driven from the web UI or the brain.
- Auto-return to a standby stance after a movement ends, in Balanced/Angled modes only.
- Add explicit Reset (90° all) and Standby (stand pose) controls to Tools.
- Move the Move panel to the right side of the cockpit layout; Communication and Sensors move to the left.

## Non-goals

- Full inverse-kinematics body leveling. The balance approach is a proportional IMU-feedback trim on top of the existing CPG/policy gait, not per-leg foot-position IK.
- A servo calibration UI. The pulse-range fix only removes the bug; setting per-servo custom ranges remains a config-file edit (matches how `servo_trims` works today).
- Changing the ONNX RL policy backend or training pipeline.
- Resolving pre-existing concurrency gaps between simultaneous gait commands and pose runs (out of scope; not introduced by this work).

## Design

### 1. Servo calibration fix (`drivers/servos.py`)

Replace the per-channel degree trim with a per-channel **calibrated pulse range**:

- `BridgeConfig.servo_trims: list[int]` (degrees) → `servo_pulse_ranges: list[tuple[int, int]]` (µs), default `[(500, 2500)] * 8` — identical behavior to today when uncalibrated.
- `ServoDriver` stores `pulse_ranges` per channel instead of `trims`. `_write(channel, angle)` clamps `angle` to `[0, 180]` and linearly maps it onto that channel's own `(pulse_min_us, pulse_max_us)`:

  ```
  pulse_us = pulse_min_us + (angle / 180) * (pulse_max_us - pulse_min_us)
  ```

  0° always yields `pulse_min_us`, 180° always yields `pulse_max_us` — no post-hoc clamping that can strand the endpoints.
- `angle_to_pulse_us` becomes a per-instance/per-range function (or takes `min_us`/`max_us` params) rather than the current module-level function with hardcoded `PULSE_MIN_US`/`PULSE_MAX_US`. Those constants remain as the *default* range.

### 2. Smooth motion — `SmoothServos`

New class, same file area as `drivers/servos.py` or a new `drivers/smooth_servos.py`. Wraps a `ServoDriver` and exposes the identical interface (`set_angle`, `set_pose`, `last_angle`, `relax`) so every existing call site can take it as a drop-in replacement — but unlike `ServoDriver`, it owns its own periodic tick rather than writing hardware synchronously on every call. This matters because callers use it two different ways (`GaitEngine` calls `set_angle` itself every ~20ms already; `PoseRunner` makes one-shot calls and expects the move to happen while it `await`s `wait_ms`) — a version that blocked-and-ramped inside the call itself would stall `GaitEngine`'s own 50Hz loop and couldn't serve both callers correctly.

- `set_angle(servo, angle)` / `set_pose(angles, stagger=True)` only **record a target** per channel (immediately, `set_pose`'s existing stagger becomes a staggered *target-set*, not a staggered write) and return without touching hardware.
- A separate internal `tick()`, driven by its own fixed-rate loop (50 Hz, started/stopped the same way `GaitEngine.run()` is in `main.py`), steps every channel whose current angle hasn't yet reached its target by at most `slew_deg_per_s * dt` (default **300°/s** — well under typical MG90 speed of ~600°/s, enough to remove instant-jump jerk without feeling sluggish), and writes the result to the wrapped `ServoDriver`. Channels already at their target are left untouched (idle, no redundant writes).
- `last_angle(servo)` returns the wrapped driver's last *written* (physical, in-flight) angle, not the pending target — callers like the ONNX policy that read back current joint state should see where the servo actually is, not where it's headed.
- Constructed once in `main.py`: `servos = ServoDriver.from_hardware(...)`; `motion_servos = SmoothServos(servos)`; `motion_servos` (not the raw driver) is what's passed to `PoseRunner`, `GaitEngine`, `SleepController`, and `WebDeps.servos`. Its tick task starts/stops alongside the gait task.

This single wrapper is "the central control panel living inside" the robot for raw servo motion — poses, gait, and manual servo/servo-batch commands all move through it, so the smoothness fix is one change instead of three.

### 3. `GaitEngine` becomes the mode/balance coordinator

`GaitEngine` already is the single interface both the web app (`webapp/motion.py`) and the brain (`net/session.py`) call through via `set_velocity_command`. Rather than adding a second coordinator class, it grows to own mode state and the discrete reset/standby targets — so both control paths get the new behavior with zero changes to `net/session.py`.

New surface on `GaitEngine`:

- `mode: Literal["raw", "balanced", "angled"]` property, default `"raw"` (today's behavior unchanged unless explicitly opted in).
- `set_mode(name: str) -> None` — validates, stores, and (via a callback injected like `on_change` elsewhere in this codebase, e.g. `ControlBroker`) notifies the web layer to broadcast the change.
- `reset() -> None` / `standby() -> None` — issue a one-shot `set_pose(REST_ANGLES)` / `set_pose(STAND_ANGLES)` to the injected `SmoothServos`, which then handles the interpolation itself on its own tick (see §2) — `GaitEngine` doesn't need to track slew progress; it just needs to avoid also writing servos while that settle is in flight, and to skip entirely while a `PoseRunner` pose is actively running (new `PoseRunner.is_running` property, a simple flag set/cleared around `run()`) so writers never fight over the servos.
- `tick()` changes:
  - When `mode != "raw"`, after computing angles from the CPG/policy backend, pass them through `BalanceCorrector.correct(angles, imu_state, mode)` before writing.
  - In `"balanced"`/`"angled"` modes, keep ticking (self-leveling) even at zero velocity command instead of going idle — needed so the robot holds level on a slope or after a balanced-mode stop. In `"raw"`, idle behavior is unchanged (no writes when velocity is zero).
  - When velocity returns to `(0,0,0)` in `"balanced"`/`"angled"` mode, automatically call the equivalent of `standby()` so the robot settles into stand after each movement. `"raw"` never does this.

### 4. `BalanceCorrector` (new, `gait/balance.py`)

Pure function/class, no hardware dependency, easily unit-tested:

```
correct(angles: dict[str, float], imu: ImuState, mode: str) -> dict[str, float]
```

- Proportional-only controller (no integral term for v1 — flagged as a tuning knob if oscillation shows up on hardware): `correction = clamp(Kp * error, -max_correction, max_correction)`, where `error` is `imu.roll` (target 0) for the roll axis and `imu.pitch` (target 0) for the pitch axis.
- Roll correction is added with opposite sign to left-side vs right-side leg hip angles (reusing the mirror-sign convention already established in `gait/cpg.py`'s `LEGS` mapping); pitch correction is added with opposite sign to front-leg vs rear-leg hip angles.
- Per-mode tuning (constants, not user-configurable in v1):
  - `"balanced"`: modest `Kp`, `max_correction ≈ 12°` — flat-ground micro-corrections while walking.
  - `"angled"`: similar/slightly lower `Kp` (steadier), `max_correction ≈ 30°` — enough authority to hold level against a real incline.
  - `"raw"`: `BalanceCorrector` is never invoked.
- Result is clamped to `[0, 180]` same as everywhere else before being written.

### 5. Web protocol additions

New WS message types in `webapp/ws.py`'s handler dispatch, each gated through `MotionService._denied` the same way existing motion commands are:

- `{"t": "mode", "name": "raw" | "balanced" | "angled"}` → `MotionService.mode()` → `deps.gait.set_mode()`. Broadcasts `{"t": "mode", "name": ..., }` to *all* connected clients (mirrors `_broadcast_owner`'s pattern for `control`), so every tab's Move panel reflects the live mode, not just the controller's own tab.
- `{"t": "reset"}` → `MotionService.reset()` → `deps.gait.reset()`.
- `{"t": "standby"}` → `MotionService.standby()` → `deps.gait.standby()`.

### 6. Web UI changes

- **`console.css`**: `grid-template-areas: "move camera side"` → `"side camera move"`; swap the paired column widths (`280px`/`320px`) so the wider column still belongs to the comm+sensors side. No DOM/JS reordering needed — grid-area placement is independent of source order.
- **`move.js`**: add a Raw/Balanced/Angled segmented control above the joystick, sending `{"t": "mode", "name": ...}` on change, and a status line ("Mode: Balanced — enabled") driven by the broadcast `mode` message (subscribed via `bus.on("mode", ...)`, same pattern as `bus.on("control", ...)` in `statusbar.js`).
- **`servos.js`** (Tools > Servo Test): replace the single "Center All (90°)" button with two buttons — **Reset** (sends `{"t":"reset"}`) and **Standby** (sends `{"t":"standby"}`).

### 7. Config

`BridgeConfig.servo_trims: list[int]` → `servo_pulse_ranges: list[tuple[int, int]]`, default `[(500, 2500)] * 8`. `main.py`'s `ServoDriver.from_hardware(trims=cfg.servo_trims, ...)` call updates to pass `pulse_ranges=cfg.servo_pulse_ranges`.

## Testing

All off-hardware, following the existing pattern (injected fakes/fake clocks, no real I2C):

- `test_servos.py`: extend for per-channel pulse-range mapping — 0°→`pulse_min_us`, 180°→`pulse_max_us`, midpoint linearity, and that differing per-channel ranges don't cross-contaminate.
- New `test_smooth_servos.py`: `set_angle`/`set_pose` record targets without writing hardware immediately; repeated `tick()` calls step at most the slew limit per elapsed time and eventually reach the exact target; idle channels already at target produce no redundant writes; `last_angle` reflects the physical (written) angle, not the pending target; pass-through of `relax`.
- New `test_balance.py`: sign/magnitude of roll correction (left vs right) and pitch correction (front vs rear) for known IMU states, clamping at the mode's `max_correction`, and that `mode="raw"` never invokes the corrector.
- `test_gait.py`: extend for `set_mode`/`reset`/`standby`, continued ticking at zero velocity in balanced/angled mode, auto-standby-on-stop in balanced/angled but not raw, and yielding to an in-progress `PoseRunner` pose.
- `webapp` tests: `mode`/`reset`/`standby` message handling, control-gate denial, and the mode-change broadcast reaching all connected clients.

**Cannot be verified off-hardware:** actual servo smoothness, whether the slider now reaches true physical 0°/180°, and whether the balance/angled gains (`Kp`, `max_correction`) actually hold the robot level — these need a pass on the real robot after implementation, including likely gain tuning.
