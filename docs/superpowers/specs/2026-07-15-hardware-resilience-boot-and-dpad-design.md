# Hardware Resilience, I2C Reset, Servo Hold, Boot Choreography & D-pad Move Controls

**Date:** 2026-07-15
**Status:** Approved for planning

## Problem

Seven related gaps surfaced after the central-motion-controller branch shipped:

1. If a single I2C peripheral (most concretely the OLED display) fails to initialize at boot, the entire `milo-bridge` process crashes before the web dashboard even starts — there's no way to recover short of SSHing in and fixing the hardware, then waiting for (or forcing) a restart.
2. There's no way to release servo holding torque (for manual leg repositioning during assembly/calibration) and then re-engage it, short of restarting the whole process.
3. Boot gives no physical indication that the robot came online successfully — it silently settles into the rest pose.
4. There's no visible startup confirmation that hardware actually initialized, and no ongoing indication if something's missing.
5. The Move panel's joystick is harder to use precisely than fixed directional controls, especially on touch devices.
6. Turning (Q/E) via the CPG's continuous yaw command doesn't turn properly, unlike the scripted `turn_left`/`turn_right` gaits already visible in the Poses & Emotes panel. Sideways movement (A/D) does nothing at all — `CpgGait.angles_at` accepts `vy` but its own docstring says it's ignored; Milo turns in place instead of strafing.
7. Balanced/Angled mode's self-leveling is too weak to be useful: tilt the robot by hand and the IMU visibly registers it, but the correction is a barely-perceptible nudge, not a real recovery reaction.

## Goals

- A missing/failed servo or display driver must not prevent the web dashboard (or anything else that doesn't strictly need that specific piece of hardware) from coming up.
- A one-click "I2C reset" recovers a peripheral that was unplugged and replugged, without SSH.
- Tools gets Release/Hold buttons for servo torque.
- Boot performs a physical "waking up" gesture and settles into the stand pose, not the rest pose.
- Boot shows a startup hardware checklist on the OLED before switching to the normal eyes face; if anything failed, the idle face uses a distinct "concerned" expression for the rest of that process's life.
- The Move panel's joystick is replaced by a fixed 6-button D-pad (4 directional arrows + 2 turn-rotate icons).
- Forward/backward (W/S, ↑/↓) keep working exactly as today — continuous CPG velocity-command walking.
- Turning (Q/E, ↺/↻) drives the actual `turn_left`/`turn_right` scripted gaits, held for as long as the button is held.
- Strafing (A/D, ←/→) drives new scripted `crab_left`/`crab_right` gaits (mirrored from the existing one-shot `crab` emote), held for as long as the button is held.
- Balanced/Angled mode's correction is strong enough to be a real physical reaction — bigger magnitude, and both hip and knee move together per leg so it reads as "stretch that side's legs out," not a subtle rotation.

## Non-goals

- Continuous runtime I2C health polling. Detection is boot-time only; a peripheral that fails mid-session (after a successful boot) is not separately detected by this work.
- Hot-swapping an individual failed driver in-process. Recovery is a full, clean process restart (systemd's existing `Restart=always` already re-probes every driver).
- Any change to `SleepController`'s existing rest/wake behavior once a brain connects or disconnects. The boot flourish is a one-time sequence before `SessionManager.run_forever()` starts, not a change to the ongoing sleep/wake cycle.
- Any new safety interlock preventing Release while the robot is actively walking — Tools is already a manual/developer surface with no such guards elsewhere.
- Keyboard controls are not removed from the Move panel — the D-pad is an additional/replacement pointer-input surface, not a removal of WASD/arrow/QE keyboard support.
- No blending of simultaneous scripted-pose directions (e.g. holding both a turn and a strafe button at once) — the second input is simply rejected while the first is running, same as the existing single-pose-at-a-time exclusivity in `PoseRunner`/`MotionService.pose()`.
- No speed control for turn/strafe — `turn_left`/`turn_right`/`crab_left`/`crab_right` run at their own fixed per-step timing (`FRAME_DELAY_MS`), same as every other scripted gait. The Move panel's speed slider continues to scale only the continuous forward/backward gait command.
- No live-tunable balance gains (config fields or a debug UI) — `BalanceParams` remain hardcoded constants in `gait/balance.py`, just with substantially larger values. Real tuning still requires editing that file and redeploying, same as before.
- No new balance control architecture (derivative/integral terms, recovery state machine) — this is a magnitude and joint-coverage change to the existing proportional controller, not a redesign.

## Design

### 1. Hardware resilience — null-object drivers

New file `bridge/milo_bridge/drivers/null_hardware.py`:

```python
class NullServos:
    """Stand-in for ServoDriver/SmoothServos when the PCA9685 isn't reachable
    at boot -- every call is a silent no-op so GaitEngine/PoseRunner/etc.
    don't need special-casing, and the rest of the service stays up."""
    def set_angle(self, servo, angle): ...
    async def set_pose(self, angles, stagger=True): ...
    def last_angle(self, servo): return None
    def relax(self): ...
    def hold(self): ...


class NullDisplay:
    """Stand-in for FaceDisplay when the OLED isn't reachable at boot."""
    current_face = None
    async def set_face(self, name, mode=None, fps=8.0): ...
    async def show_pin(self, pin): ...
    async def show_status(self, status, seconds=3.0): ...
    def start_idle(self, base_face="idle"): ...
    def stop_idle(self): ...
```

`main.py`'s `_optional()` helper changes to return `(value, ok)` instead of just `value`, uniformly for all five optional-hardware call sites (servos, display, imu, camera, audio):

```python
def _optional(factory, what: str) -> tuple[object | None, bool]:
    try:
        return factory(), True
    except Exception as exc:
        log.warning("%s unavailable (%s: %s) — continuing without it", what, type(exc).__name__, exc)
        return None, False
```

`servos`/`display` construction moves from the "Required hardware" block into this same optional pattern, falling back to `NullServos()`/`NullDisplay()` on failure:

```python
servos, servos_ok = _optional(lambda: ServoDriver.from_hardware(...), "servos")
servos = servos or NullServos()
...
display, display_ok = _optional(lambda: FaceDisplay.from_hardware(ASSETS_DIR), "display")
display = display or NullDisplay()
```

A single `hardware_status: dict[str, bool]` (`servos`, `display`, `imu`, `camera`, `audio`) is built from all five `_optional()` results and becomes the one source of truth for hardware presence — passed into `WebDeps` as a new field. `webapp/api/status.py`'s `get_status` reports `hardware=deps.hardware_status` directly, replacing its current per-field `is not None` checks (unifying two previously-divergent mechanisms into one).

### 2. I2C reset — service self-restart

New `MotionService.restart(client_id) -> dict` (control-gated like every other motion command): logs the request, schedules a clean process exit (`os._exit(0)` via `loop.call_later(0.3, os._exit, 0)` so the WS ack has time to flush), and returns `{"ok": True}`. systemd's `Restart=always` / `RestartSec=3` (already configured in `bridge/systemd/milo-bridge.service`) brings the process back with every driver freshly re-probed.

New WS message `{"t": "restart"}`, dispatched through the existing generic `handlers` dict in `ws.py` (fire-and-ack, same pattern as `reset`/`standby`).

Web UI: a new "Restart Bridge" button in Tools, styled as a danger action, gated behind a client-side `confirm()` dialog (it's a brief full outage for every connected client).

### 3. Servo Release / Hold

`SmoothServos` (from the central-motion-controller branch) gains:

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

`_pre_relax_targets: dict[str, float] = {}` initialized in `__init__`. `hold()` re-commands through the normal `set_angle` path, so re-engagement is smoothly interpolated like any other motion, not an instant snap.

New `MotionService.relax(client_id)` / `MotionService.hold(client_id)` (control-gated), new WS messages `{"t":"relax"}` / `{"t":"hold"}` (generic `handlers` dict), new "Release" / "Hold" buttons in Tools > Servo Test.

### 4. Boot choreography + startup status + fault-indicating face

New pose in `poses.py`, `"wake_up"` — a quick full-body wiggle on the front hip/knee servos around the stand pose, ending in stand (poses default to `end_stand=True`):

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

`display.py` gains a startup checklist renderer, matching the existing `render_pin_image` style:

```python
def render_status_image(status: dict[str, bool]) -> Image.Image:
    """One line per hardware item, OK/FAIL, shown once at boot."""
```

And `FaceDisplay.show_status(status: dict[str, bool], seconds: float = 3.0) -> None`, which renders it and holds for `seconds`.

`FaceDisplay.start_idle` gains a `base_face: str = "idle"` parameter (stored as `self._idle_base`), and `_idle_loop`/`_blink` use `self._idle_base` instead of the literal `"idle"` string — a minimal, backward-compatible generalization (existing callers that don't pass `base_face` are unaffected).

`main.py`'s boot sequence changes from:

```python
await runner.run("rest")
display.start_idle()
```

to:

```python
await display.show_status(hardware_status)
await runner.run("wake_up")
display.start_idle(base_face="idle" if all(hardware_status.values()) else "confused")
```

`"confused"` is an existing face in `assets/faces/eyes.py`'s `EMOTIONS` table — no new face art needed. This state persists for the process's lifetime (no background polling) and only clears via the next restart (whichever hardware failed must actually come back for `hardware_status` to read all-true on the next boot).

### 5. Turn and strafe via scripted gaits; D-pad replaces the joystick

**New scripted poses.** `poses.py` gains `crab_left`/`crab_right`: the existing one-shot `crab` emote (front legs held neutral, rear legs oscillating in a lateral shuffle) restructured with an entry step plus a `cycle=` list — the same shape as `walk`/`turn_left`/`turn_right` — so it can be repeated indefinitely and aborted, instead of running a fixed 5 repeats and stopping. `crab_right` is `crab_left` with every servo name's `L`/`R` prefix swapped, the identical mechanical mirroring already used to derive `turn_right` from `turn_left`. The existing one-shot `crab` pose is untouched (still available as a quick emote in Poses & Emotes); `crab_left`/`crab_right` are new, additional entries, and — like every pose — automatically show up in the Poses & Emotes list too via the existing `/api/poses` endpoint.

**Hold-to-continue for scripted gaits.** `turn_left`/`turn_right` (already existing, cyclic) and the two new `crab_left`/`crab_right` need to run for as long as a button is held, then stop cleanly — the same "run with a very large cycle count, `abort()` on release" idiom already used by this codebase's own tests (`test_abort_interrupts_and_recovers_to_stand` runs `walk` with `cycles=10_000`). Two new `MotionService` methods, both control-gated and sharing the existing `_pose_task` single-flight guard (so a scripted gait can't run concurrently with another one, or fight the exclusivity the Poses & Emotes panel's own `pose()` calls already rely on):

```python
HOLD_CYCLES = 10_000  # effectively "until aborted"

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
```

New WS messages `{"t": "turn", "dir": "left"|"right"}` / `{"t": "strafe", "dir": "left"|"right"}`, dispatched through the existing generic `handlers` dict. Releasing either button sends the existing `{"t": "stop"}` message, which already calls `runner.abort()` unconditionally — no new release-side plumbing needed. Because `GaitEngine.tick()` already defers entirely whenever `PoseRunner.is_running` (built in the central-motion-controller branch), holding a continuous-gait direction (W/S) and a scripted-gait direction (Q/E or A/D) at the same time behaves safely without new coordination code: the gait engine simply pauses its own writes while the scripted gait owns the servos, and resumes automatically once it's released and stopped.

**D-pad UI.** In `move.js`, remove the `#pad`/`#knob` draggable joystick (HTML + all its `pointerdown`/`pointermove`/`pointerup` wiring). In its place, a fixed 3×3 CSS-grid D-pad: `↑`/`↓` (top/bottom-center) still drive the continuous gait command exactly as W/S do today (reusing the existing `down` Set + `sync()` + periodic `{t:"gait",...}` send). `←`/`→` (middle row sides) and the two rotate-icon buttons `↺`/`↻` are wired independently: `pointerdown` sends `{t:"strafe"|"turn", dir:...}`; `pointerup`/`pointerleave`/`pointercancel` sends `{t:"stop"}`.

**Keyboard remapping.** To keep keyboard and D-pad semantics consistent: `a`/`d` now send `strafe(left/right)` on keydown and `stop` on keyup (previously fed `vy` into the ignored CPG strafe path); `q`/`e` and `ArrowLeft`/`ArrowRight` now send `turn(left/right)`/`stop` the same way (previously `q`/`e` fed CPG `yaw`, and `ArrowLeft`/`ArrowRight` already meant "turn" — this makes all three consistent with each other for the first time). `w`/`s`/`ArrowUp`/`ArrowDown` are unchanged, still feeding the continuous gait command via `down`/`sync()`. This splits the single `keys` map into two independent groups with two different wire protocols, rather than one unified `sync()` covering all six inputs.

**Known consequence:** the speed slider no longer has any effect on turning or strafing (scripted gaits run at their own fixed tempo) — only forward/backward speed remains adjustable. This is called out in Non-goals above rather than solved by adding speed control to `PoseRunner`.

### 6. Stronger, leg-stretch-style balance correction

`gait/balance.py`'s `correct()` currently nudges only each leg's hip servo. It's extended to move hip **and knee** together per leg — the same signed correction applied to both joints of a leg reads as "reach/stretch that leg out," which has real mechanical effect on stance, versus a hip-only rotation that barely shows:

```python
def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    if mode not in PARAMS:
        return angles
    params = PARAMS[mode]
    roll_term = params.roll_kp * roll_deg
    pitch_term = params.pitch_kp * pitch_deg

    corrected = dict(angles)
    for leg, (hip, knee, *_rest) in LEGS.items():
        side = 1.0 if leg[1] == "L" else -1.0
        front = 1.0 if leg[0] == "F" else -1.0
        delta = _clamp(side * roll_term + front * pitch_term, params.max_correction_deg)
        for joint in (hip, knee):
            corrected[joint] = max(0.0, min(180.0, corrected[joint] + delta))
    return corrected
```

`PARAMS` gains meaningfully larger defaults so the reaction is actually visible against a hand-tilt, not just a barely-perceptible trim:

```python
PARAMS: dict[str, BalanceParams] = {
    "balanced": BalanceParams(roll_kp=0.6, pitch_kp=0.6, max_correction_deg=25.0),
    "angled": BalanceParams(roll_kp=0.5, pitch_kp=0.5, max_correction_deg=45.0),
}
```

These are still first-pass numbers — real gain tuning happens on the robot, same as the original design already called out. One known physical limitation carried over from the original design: the rear knees (`R4`/`L4`) sit at their servo extremes (`0`/`180`) in the stand pose, so correction in the outward direction clamps immediately there regardless of gain — this is a hardware-geometry constraint, not something the correction math can route around.

## Testing

Off-hardware, following the existing pattern:

- New `test_null_hardware.py`: every `NullServos`/`NullDisplay` method is callable without raising and has the documented no-op/None return.
- New `test_main.py`: `_optional()` is a small, hardware-independent function (`from milo_bridge.main import _optional`) — unit-test it directly with fake factories, one that succeeds and one that raises, asserting the `(value, ok)` tuple in each case. `main()` itself (the composition root) stays exercised only by the real service, as today.
- `test_smooth_servos.py`: extend for `relax()` snapshotting `_pre_relax_targets` and `hold()` restoring them; `hold()` with nothing relaxed is a no-op.
- `test_poses.py`: extend `test_all_poses_use_known_servo_names_and_valid_angles` and `test_gaits_have_cycles_and_oneshots_do_not` coverage to include `"wake_up"`; new test that it ends at `STAND_ANGLES`.
- `test_display.py`: extend for `render_status_image` (renders without raising, correct size) and `FaceDisplay.show_status`/`start_idle(base_face=...)`.
- `test_status.py` (webapp): `hardware` now reads from `deps.hardware_status` directly.
- `test_motion.py`/`test_ws.py`: new tests for `restart`/`relax`/`hold`/`turn`/`strafe`, each requiring control and never raising on driver error, following the exact pattern established for `mode`/`reset`/`standby`. `turn`/`strafe` additionally get a test asserting they share `pose()`'s single-flight guard (a second call while one is running returns `{"error": "pose-running"}`), and a test that an unknown `dir` value is rejected before touching the runner.
- `test_poses.py`: extend for `crab_left`/`crab_right` — both use only known servo names and valid `[0,180]` angles (existing `test_all_poses_use_known_servo_names_and_valid_angles` covers this automatically once they're added to `POSES`), both have a `cycle` (extend `test_gaits_have_cycles_and_oneshots_do_not`'s cyclic-poses list), and a new test confirming `crab_right`'s cycle is `crab_left`'s with every servo name's `L`/`R` prefix swapped (mirrors the mechanical relationship, doesn't just eyeball it).
- `test_balance.py`: extend every existing hip-only assertion to also check the matching knee moved by the same delta; extend `test_correction_clamped_to_mode_max` to cover knees too; update the two hardcoded gain/max-correction expectations to the new `PARAMS` values.

**Cannot be verified off-hardware:** whether the OLED status screen is legible, whether the `wake_up` wiggle looks right physically, whether I2C reset actually recovers a replugged device (systemd restart itself is not exercisable in unit tests), whether the D-pad buttons are comfortably sized/spaced on a real touch device, whether `turn_left`/`turn_right`/`crab_left`/`crab_right` actually turn/strafe correctly when held continuously via the new large-cycle-count path, and whether the strengthened balance correction is actually strong enough (or now too strong/oscillatory) against a real hand-tilt. These need a pass on the real robot after implementation.
