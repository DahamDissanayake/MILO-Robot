# Hardware Resilience, I2C Reset, Servo Hold, Boot Choreography & D-pad Move Controls

**Date:** 2026-07-15
**Status:** Approved for planning

## Problem

Five related gaps surfaced after the central-motion-controller branch shipped:

1. If a single I2C peripheral (most concretely the OLED display) fails to initialize at boot, the entire `milo-bridge` process crashes before the web dashboard even starts — there's no way to recover short of SSHing in and fixing the hardware, then waiting for (or forcing) a restart.
2. There's no way to release servo holding torque (for manual leg repositioning during assembly/calibration) and then re-engage it, short of restarting the whole process.
3. Boot gives no physical indication that the robot came online successfully — it silently settles into the rest pose.
4. There's no visible startup confirmation that hardware actually initialized, and no ongoing indication if something's missing.
5. The Move panel's joystick is harder to use precisely than fixed directional controls, especially on touch devices.

## Goals

- A missing/failed servo or display driver must not prevent the web dashboard (or anything else that doesn't strictly need that specific piece of hardware) from coming up.
- A one-click "I2C reset" recovers a peripheral that was unplugged and replugged, without SSH.
- Tools gets Release/Hold buttons for servo torque.
- Boot performs a physical "waking up" gesture and settles into the stand pose, not the rest pose.
- Boot shows a startup hardware checklist on the OLED before switching to the normal eyes face; if anything failed, the idle face uses a distinct "concerned" expression for the rest of that process's life.
- The Move panel's joystick is replaced by a fixed 6-button D-pad (4 directional arrows + 2 turn-rotate icons).

## Non-goals

- Continuous runtime I2C health polling. Detection is boot-time only; a peripheral that fails mid-session (after a successful boot) is not separately detected by this work.
- Hot-swapping an individual failed driver in-process. Recovery is a full, clean process restart (systemd's existing `Restart=always` already re-probes every driver).
- Any change to `SleepController`'s existing rest/wake behavior once a brain connects or disconnects. The boot flourish is a one-time sequence before `SessionManager.run_forever()` starts, not a change to the ongoing sleep/wake cycle.
- Any new safety interlock preventing Release while the robot is actively walking — Tools is already a manual/developer surface with no such guards elsewhere.
- Keyboard controls are not removed from the Move panel — the D-pad is an additional/replacement pointer-input surface, not a removal of WASD/arrow/QE keyboard support.

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

### 5. D-pad replaces the joystick

In `move.js`, remove the `#pad`/`#knob` draggable joystick (HTML + all its `pointerdown`/`pointermove`/`pointerup` wiring). In its place, a fixed 3×3 CSS-grid D-pad: `↑` (top-center), `←`/`→` (middle row sides), `↓` (bottom-center), mapped to `w`/`a`/`d`/`s` respectively, plus two rotate-icon buttons `↺`/`↻` (turn left/right) mapped to `q`/`e`. Each button wires into the *same* `down` Set and `sync()` function the keyboard handlers (`kd`/`ku`) already use — `pointerdown` adds the matching letter to `down` and calls `sync()`; `pointerup`/`pointerleave`/`pointercancel` remove it and call `sync()` again. This means an on-screen button press is indistinguishable from a keyboard press to the rest of the panel's logic (including combining two held buttons for a diagonal), and the existing `keys`/`down`/`sync` block just needs to be declared before both the keyboard listeners and the new D-pad wiring (a reordering, not new logic). Keyboard support (WASD, arrow keys, Q/E) is unchanged and stays working alongside the buttons. Speed slider and STOP are unchanged.

## Testing

Off-hardware, following the existing pattern:

- New `test_null_hardware.py`: every `NullServos`/`NullDisplay` method is callable without raising and has the documented no-op/None return.
- New `test_main.py`: `_optional()` is a small, hardware-independent function (`from milo_bridge.main import _optional`) — unit-test it directly with fake factories, one that succeeds and one that raises, asserting the `(value, ok)` tuple in each case. `main()` itself (the composition root) stays exercised only by the real service, as today.
- `test_smooth_servos.py`: extend for `relax()` snapshotting `_pre_relax_targets` and `hold()` restoring them; `hold()` with nothing relaxed is a no-op.
- `test_poses.py`: extend `test_all_poses_use_known_servo_names_and_valid_angles` and `test_gaits_have_cycles_and_oneshots_do_not` coverage to include `"wake_up"`; new test that it ends at `STAND_ANGLES`.
- `test_display.py`: extend for `render_status_image` (renders without raising, correct size) and `FaceDisplay.show_status`/`start_idle(base_face=...)`.
- `test_status.py` (webapp): `hardware` now reads from `deps.hardware_status` directly.
- `test_motion.py`/`test_ws.py`: new tests for `restart`/`relax`/`hold`, each requiring control and never raising on driver error, following the exact pattern established for `mode`/`reset`/`standby`.

**Cannot be verified off-hardware:** whether the OLED status screen is legible, whether the `wake_up` wiggle looks right physically, whether I2C reset actually recovers a replugged device (systemd restart itself is not exercisable in unit tests), and whether the D-pad buttons are comfortably sized/spaced on a real touch device. These need a pass on the real robot after implementation.
