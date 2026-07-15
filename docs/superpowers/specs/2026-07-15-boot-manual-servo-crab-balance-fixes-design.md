# Boot Standby, Real Crab Strafing, Manual Servo Mode, Balance Direction, Gait-Resume Fix

**Date:** 2026-07-15
**Status:** Approved for planning

## Problem

Real-hardware testing of the previous two branches surfaced five issues:

1. The robot stands up after boot's `wake_up` gesture, then immediately collapses back to rest and goes limp — `wake_up` itself is fine, but `SessionManager` calls `SleepController.ensure_asleep()` on its very first tick if no brain has connected yet, which happens almost instantly after boot since discovery takes a few seconds. Separately, `wake_up`'s wiggle is bigger than wanted — a simple dip is enough.
2. A/D now trigger `crab_left`/`crab_right`, but neither actually translates the robot sideways. `crab_left` is a direct copy of the old one-shot `crab` emote (a stationary rear-leg shimmy, never designed as locomotion), and `crab_right`'s mechanical R/L-name-swap mirror produces an unrelated-looking motion. This isn't fixable by tuning — the emote never walked sideways in the first place.
3. Servo Test in Tools is unusable: whatever mode the robot is in when the operator drags a slider, the drag gets fought by background writes and the servo "keeps trying to come back to the previous state." The Release/Hold buttons added last time don't solve this and should come out.
4. Balance correction's direction is wrong. On a real hand-tilt, the desired reaction — confirmed by the user's own worked example — is that the leaning-side leg should *straighten* (hip and knee move toward *opposite* ends of their range, e.g. tilted left → `L3` toward 180 *and* `L4` toward 0), not move together as the last revision implemented.
5. Combining a continuous walk (holding a direction) with a triggered pose (e.g. clicking "wave") causes servos to move wrong afterward.

## Root causes

1a. `net/session.py`'s `SessionManager._tick()` calls `self._sleep.ensure_asleep()` unconditionally whenever no brain is currently selectable — including the very first tick, with no boot grace period.
1b. `poses.py`'s `wake_up` pose is an 8-step, ~1s wiggle — bigger than requested.
2. `crab_left`/`crab_right`'s angles came from repurposing a cosmetic emote plus a naive per-servo-name R/L swap; the swap technique isn't a reliable mirroring method for an arbitrary hand-authored gait (already proven false for `turn_left`/`turn_right` too — see the first plan's Global Constraints note).
3. Whenever `mode` is `balanced`/`angled`, `GaitEngine.tick()` keeps writing to *every* servo every ~20ms (self-leveling / `_hold_level()`), which directly fights a manual slider drag sent via `MotionService.servo()`. There's no way to tell `GaitEngine` "hands off, a human is testing servos right now" — the existing `PoseRunner.is_running` deference only covers scripted poses, not manual single-servo commands.
4. `BalanceCorrector.correct()` (added in the previous branch) applies the *same* signed delta to both a leg's hip and knee. The user's worked example shows the correct relationship is *opposite* signs — hip and knee should move toward opposite ends of their range to "straighten"/extend the leg, not move in lockstep.
5. `GaitEngine.tick()` already defers entirely while `PoseRunner.is_running` (correct), but the CPG's phase clock `self._t0` is never adjusted for how long that deferral lasted. When a pose finishes and gait resumes, `self._cpg.angles_at(self._clock() - self._t0, ...)` computes the phase as if the gait had kept running the whole time the pose was playing — the CPG has silently "walked ahead" in phase-space while the servos were physically frozen at the pose's stand-recovery position, so the very next gait tick can jump to a angle far from where the legs actually are.

## Goals

- After boot, the robot stays standing (not asleep/relaxed) for a short grace period even with no brain connected yet.
- `wake_up` becomes a small dip-and-recover, not a wiggle.
- A/D drive a genuinely new, from-scratch sideways-stepping attempt, not derived from the old "crab" emote or a mechanical mirror of it.
- Servo Test gets a "Manual Servo Mode" toggle that makes `GaitEngine` stop writing entirely while it's on, so slider drags are never fought. Release/Hold buttons are removed.
- Balance correction moves each leg's hip and knee toward *opposite* ends of their range (straightening), scaled by the same per-leg roll/pitch reaction as before.
- Resuming a walk after a scripted pose interrupts it no longer jumps — the CPG phase clock accounts for the deferred time.

## Non-goals

- No guarantee the new crab gait actually achieves true lateral translation. This robot's legs have only two joints each (hip = fore-aft swing, knee = lift) and no dedicated sideways-swing axis — real crab-walking may not be mechanically achievable at all with this geometry. This is a best-effort attempt that will very likely need on-robot angle iteration, explicitly acknowledged going in.
- No change to the balance correction's overall magnitude/gains (`roll_kp`, `pitch_kp`, `max_correction_deg`) — only the hip-vs-knee sign relationship. If the reaction is still too weak/strong after this fix, that's a separate follow-up.
- No live-tunable gain UI, still out of scope as established previously.
- Manual Servo Mode does not persist across a service restart or affect anything except `GaitEngine`'s own writes — it doesn't touch `SleepController`, `PoseRunner`, or the brain-driven control path.

## Design

### 1a. Boot grace period before sleep can trigger

`net/session.py`'s `SessionManager` gains a boot timestamp and a grace constant:

```python
import time

class SessionManager:
    BOOT_GRACE_S = 8.0

    def __init__(self, ...):
        ...
        self._booted_at = time.monotonic()

    async def _tick(self) -> None:
        choice = select_brain(self._discovery.snapshot(), self._store)
        if choice is None:
            if time.monotonic() - self._booted_at > self.BOOT_GRACE_S:
                await self._sleep.ensure_asleep()
            await asyncio.sleep(self._cfg.reconnect_seconds)
            return
        ...
```

For the first 8 seconds after `SessionManager` is constructed (right after `main.py`'s boot choreography finishes), a no-brain-found tick no longer puts the robot to sleep — it just waits and retries. If a brain connects within that window, sleep never triggers at all. After the grace period, today's behavior (sleep when no brain) resumes unchanged.

### 1b. `wake_up` becomes a small dip

Replace the 8-step wiggle with a 2-step dip — stand, bend all four knees slightly, then let `end_stand=True`'s automatic recovery write settle it back to `STAND_ANGLES`:

```python
"wake_up": Pose(
    "wake_up", "surprised", AnimMode.ONCE,
    [
        Step(STAND_ANGLES, 150),
        Step({"R2": 65, "L2": 115, "R4": 20, "L4": 160}, 250),
    ],
),
```

### 2. New from-scratch `crab_left`/`crab_right`

Hand-authored directly (not derived from the old `crab` emote, not derived from each other via a mechanical swap) using a "lean the hips sideways + lift the knees, then return to stand" 2-step shuffle cycle. Hip lean direction is what differs between left/right (the actual left/right differentiator); knee lift is identical in both since lifting isn't inherently directional. Rear hips (`L3`/`R3`) are biased off their true stand extremes (`0`/`180`) toward a mid-range base — the same problem `gait/cpg.py`'s `GAIT_NEUTRAL` already solves for the CPG gait, solved independently here (not imported, to avoid a circular `poses.py` ↔ `gait/cpg.py` dependency) so both lean directions have room to move:

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

The existing one-shot `"crab"` emote is untouched. `MotionService.strafe()` (already wired from the previous branch) needs no changes — it already runs `f"crab_{direction}"` with a large cycle count.

### 3. Manual Servo Mode replaces Release/Hold

`GaitEngine` gains an override flag checked first in `tick()`, before the pose-deference check:

```python
def __init__(self, ...):
    ...
    self._manual_override = False

def set_manual(self, on: bool) -> None:
    self._manual_override = on
    if on:
        self._command = (0.0, 0.0, 0.0)
        self._active = False

```

`tick()` gains a manual-override check alongside the existing pose-deference check — both are folded into one `deferring` condition in §5 below, since §5 also changes how `tick()` reacts when deferring ends; see §5 for the complete, final `tick()` body rather than treating this as two separate edits to the same method.

`MotionService.manual(client_id, on)` (control-gated, same pattern as every other command) calls `gait.set_manual(on)` and, when turning manual mode *on*, also calls `runner.abort()` — mirroring `stop()`'s "stop every motion source" behavior, so a running pose can't fight the slider either:

```python
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
```

New WS message `{"t": "manual", "on": true|false}`, handled specially in `ws.py` (like `mode`, not the generic dict) because it broadcasts to every connected client — mirrors `_broadcast_mode`'s exact pattern with a new `_broadcast_manual`.

`bridge/milo_bridge/webapp/motion.py`'s `relax`/`hold` methods and `bridge/milo_bridge/drivers/smooth_servos.py`'s `SmoothServos.relax`/`hold` stay in the codebase (no reason to remove working, tested code) — only the two Tools UI buttons for them come out, replaced by a single "Manual Servo Mode" toggle button (same `.btn.active`-toggle pattern as the Move panel's mode selector), listening for the broadcast so every tab reflects the same state.

### 4. Balance correction: opposite-sign hip/knee

`gait/balance.py`'s `correct()` changes from "same delta on both joints" to "opposite delta, scaled by which side of the body the leg is on" — reusing the `side` value already computed per leg (worked out from the user's example: for a rear-left leg, `side=+1`, and the correct relationship is `hip_delta = side * delta`, `knee_delta = -side * delta`):

```python
def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    """... Hip and knee move toward *opposite* ends of their range (one
    increases, the other decreases) so the leaning-side leg visibly
    straightens/extends -- moving them the same direction, as an earlier
    revision did, doesn't produce a real physical reaction. ..."""
    if mode not in PARAMS:
        return angles
    params = PARAMS[mode]
    roll_term = params.roll_kp * roll_deg
    pitch_term = params.pitch_kp * pitch_deg

    corrected = dict(angles)
    for leg, (hip, knee, *_rest) in LEGS.items():
        if hip not in corrected:
            continue
        side = 1.0 if leg[1] == "L" else -1.0
        front = 1.0 if leg[0] == "F" else -1.0
        delta = _clamp(side * roll_term + front * pitch_term, params.max_correction_deg)
        if hip in corrected:
            corrected[hip] = max(0.0, min(180.0, corrected[hip] + side * delta))
        if knee in corrected:
            corrected[knee] = max(0.0, min(180.0, corrected[knee] - side * delta))
    return corrected
```

`|hip_delta| == |knee_delta| == |delta| <= max_correction_deg` always, so the existing combined-clamp invariant (delta computed and clamped once per leg, not independently per joint) still holds — no reopening of the 2x-overshoot bug class fixed earlier in this codebase's history.

This is still a best-effort derivation extrapolated from one confirmed data point (the rear-left leg under a left tilt) to all four legs via the same `side`-based rule — flagged, like the original sign-convention caveat, as needing on-robot confirmation for the other three legs.

### 5. Gait resumes cleanly after a pose interrupts it

`GaitEngine` tracks whether it was deferring last tick, and resets the CPG phase clock the moment deferring ends while a walk command is still active — the same "restart the cycle cleanly" logic `set_velocity_command` already uses for a fresh start, just triggered by resuming from a pose interruption instead:

```python
def __init__(self, ...):
    ...
    self._was_deferring = False

def tick(self) -> dict[str, float] | None:
    deferring = (self._manual_override
                 or (self._runner is not None and self._runner.is_running))
    if deferring:
        self._was_deferring = True
        return None
    if self._was_deferring and self._active:
        self._t0 = self._clock()  # resume the CPG cycle cleanly, not mid-phase
    self._was_deferring = False
    if not self._active:
        return self._hold_level() if self._mode in _BALANCE_MODES else None
    ...
```

Note `_manual_override` is folded into the same `deferring` check as `runner.is_running`, so manual mode gets the same clean-resume treatment for free.

## Testing

Off-hardware, following the existing pattern:

- `test_session.py` (if it exists covering `SessionManager`) or a new focused test: no-brain-found within `BOOT_GRACE_S` of construction does not call `ensure_asleep()`; past the grace period, it does. Uses an injected/fake clock the same way `GaitEngine` already does.
- `test_poses.py`: `wake_up` still ends at `STAND_ANGLES` (existing test, unaffected by the simpler steps); new/updated coverage that `crab_left`/`crab_right` are cyclic and use only valid servo names/angles (existing `test_all_poses_use_known_servo_names_and_valid_angles` covers the latter automatically); the old `_swap_lr`-mirror test is removed since `crab_right` is no longer derived from `crab_left` by that transform — replace it with a simpler test that both poses exist, are cyclic, and their entry+cycle steps differ (proving they're not accidentally identical).
- `test_gait.py`: `set_manual`/`tick()` deferring correctly on `_manual_override`; the `_t0` reset test — set a velocity command, simulate a `runner.is_running` deferral window, end the deferral, and assert the very next tick's angles match a freshly-started CPG cycle (`t=0`) rather than a stale elapsed-time phase.
- `test_balance.py`: rewrite the hip/knee-relationship tests to assert *opposite* signs (`(hip_delta > 0) != (knee_delta > 0)`) instead of the previous "same delta" assertions; clamp tests still check both joints stay within `max_correction_deg`.
- `test_motion.py`/`test_ws.py`: `manual` follows the exact `mode` pattern — control-gated, broadcasts to all clients, never raises. Remove/update any `relax`/`hold` UI-adjacent assumptions that assumed those were the only Tools servo-safety controls (the backend methods and their own tests are untouched).

**Cannot be verified off-hardware:** whether the boot grace period feels right in practice, whether the dip motion reads well, whether the new crab gait produces any real lateral translation at all (the biggest open question — may need a completely different mechanical approach if this attempt doesn't work), whether Manual Servo Mode actually stops all fighting during slider tests, and whether the corrected hip/knee balance direction is right for the other three legs beyond the one confirmed example. All need a pass on the real robot.
