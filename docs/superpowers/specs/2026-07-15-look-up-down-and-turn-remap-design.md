# Remove Non-Working Crab Strafe, Remap A/D to Turn, Add Q/E Look Up/Down

**Date:** 2026-07-15
**Status:** Approved for planning

## Problem

1. `crab_left`/`crab_right` (A/D sideways strafe) don't produce real movement on the
   real robot — confirmed on hardware. They should be removed rather than kept as
   dead/misleading functionality.
2. With crab strafe gone, A/D (and the D-pad's left/right arrow buttons) have no
   job. The user wants A/D to take over turning (the job Q/E currently do).
3. Q/E (and the D-pad's turn-icon buttons) should be repurposed: instead of
   turning, they should make the robot look up/down by tilting its body via leg
   angles — Q looks up, E looks down.

## Root cause / mechanical constraint discovered during design

The rear legs' `STAND_ANGLES` are already calibrated at the servo range extremes:
`R3=180/R4=0` (RR) and `L3=0/L4=180` (RL). Each rear leg therefore has headroom to
move in only *one* direction from stand (RR can only decrease R3/increase R4; RL
can only increase L3/decrease L4) — neither can move the *other* way without
commanding a servo past 0 or 180. A symmetric front/back pitch tilt (front pair
and rear pair moving in mirrored opposite directions, the way `balance.py`'s roll
correction moves left/right pairs) is therefore not achievable for the rear pair
without clipping.

Front legs don't have this problem — `FL` (`L1=45/L2=135`) and `FR` (`R1=135/R2=45`)
both sit at mid-range values with headroom in both directions.

**Decision:** `look_up`/`look_down` move only the front leg pair; the rear pair
stays at `STAND_ANGLES`. Extending the front pair (hip away from stand toward its
far end, knee toward its near end — the same "extend" relationship already
validated in the balance-correction fix) tilts the nose up; retracting it (the
opposite direction) tilts the nose down. This still produces the wanted
look-up/look-down effect and needs no rear-leg movement to do it.

## Goals

- Remove `crab_left`/`crab_right` poses, the `strafe()` service method, the
  `"strafe"` websocket message, and the `bindScripted("left"/"right", {t:"strafe"})`
  UI bindings.
- The D-pad's left/right arrow buttons and the A/D keys now send `{"t":"turn","dir":"left"/"right"}`
  (identical to what Q/E currently send) instead of strafe.
- Add `look_up`/`look_down` poses, a new `look()` service method mirroring
  `turn()`, a new `"look"` websocket message, and rebind Q/E (and the D-pad's
  turn-icon buttons, re-labeled) to `{"t":"look","dir":"up"/"down"}`.
- Held-to-continue behavior for look, identical to turn: press-and-hold plays the
  pose (`HOLD_CYCLES`), release sends the existing universal `{"t":"stop"}`.

## Non-goals

- No change to the one-shot cosmetic `"crab"` pose (a separate, untouched pose;
  only `crab_left`/`crab_right` are removed).
- No change to `turn_left`/`turn_right` pose definitions themselves — only which
  buttons/keys trigger them.
- No IMU feedback involved in look_up/look_down — these are fixed scripted poses,
  not sensor-driven corrections (unlike `balance.py`).
- No guarantee about the exact visual tilt angle looking "right" on first try —
  30° was chosen as a visible-but-safe starting point; may need real-hardware
  tuning like every other pose in this project.

## Design

### 1. `poses.py`: remove crab_left/crab_right, add look_up/look_down

Remove the `"crab_left"` and `"crab_right"` entries entirely (the one-shot
`"crab"` pose stays untouched).

Add, using `STAND_ANGLES = {"R1":135,"R2":45,"L1":45,"L2":135,"R4":0,"R3":180,"L3":0,"L4":180}`
and a fixed tilt magnitude of 30°, moving only the front pair (`L1,L2,R1,R2`):

```python
"look_up": Pose(
    "look_up", "idle", AnimMode.ONCE,
    [Step(STAND_ANGLES, 150)],
    cycle=[Step({"L1": 75, "L2": 105, "R1": 165, "R2": 15}, FRAME_DELAY_MS)],
),
"look_down": Pose(
    "look_down", "idle", AnimMode.ONCE,
    [Step(STAND_ANGLES, 150)],
    cycle=[Step({"L1": 15, "L2": 165, "R1": 105, "R2": 75}, FRAME_DELAY_MS)],
),
```

(Single-step cycles are intentional — this is a static held tilt, not a
multi-phase gait like `turn_left`/`walk`; repeating the same target every cycle
under `HOLD_CYCLES` just holds the position while the button is held, and
`PoseRunner.abort()` already recovers to stand on release, matching `turn_left`/
`turn_right`'s existing interrupt behavior.)

### 2. `MotionService`: remove `strafe()`, add `look()`

Remove:
```python
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

Add, mirroring `turn()` exactly except direction vocabulary and pose prefix:
```python
    async def look(self, client_id: str, direction: str) -> dict:
        if err := self._denied(client_id):
            return err
        if direction not in ("up", "down"):
            return {"error": f"unknown look direction {direction!r}"}
        if self._pose_task is not None and not self._pose_task.done():
            return {"error": "pose-running"}
        self._pose_task = asyncio.ensure_future(self._deps.runner.run(f"look_{direction}", cycles=HOLD_CYCLES))
        self._pose_task.add_done_callback(_log_pose_result)
        return {"ok": True}
```

### 3. `ws.py`: swap the dispatch entry

Replace:
```python
        "strafe": lambda: motion.strafe(client_id, data.get("dir", "")),
```
with:
```python
        "look": lambda: motion.look(client_id, data.get("dir", "")),
```
(`"turn"` entry is untouched — A/D will simply send the same message type `turn`
already dispatches.)

### 4. `move.js`: rebind buttons and keys

- D-pad's left/right arrow buttons (`data-dpad="left"/"right"`) and the `a`/`d`
  keys: change from `{t:"strafe",dir:...}` to `{t:"turn",dir:...}` (same message
  Q/E/the turn-icon buttons already send).
- D-pad's turn-icon buttons (`data-dpad="turnleft"/"turnright"`) and the `q`/`e`
  keys: change from `{t:"turn",dir:...}` to `{t:"look",dir:"up"/"down"}`. Re-label
  the two icon buttons from ↺/↻ (turn arrows) to a look-tilt icon (e.g. ⤴/⤵) since
  they no longer mean "turn."
- Arrow-key duplicates (`ArrowLeft`/`ArrowRight` currently mapped to turn in
  `turnKeys`) move to the `look`-mirrored key set is NOT required by the user's
  request (only A/D and Q/E were mentioned) — leave `ArrowLeft`/`ArrowRight`
  bound to turn as today, and `ArrowUp`/`ArrowDown` bound to forward/back as
  today. Only `q`/`e` and `a`/`d` change meaning.
- The `.muted` hint text ("or WASD / arrows, Q/E to turn") should be updated to
  reflect the new mapping.

## Testing

- `bridge/tests/test_poses.py`: remove crab_left/crab_right-specific tests
  (`test_crab_left_and_right_are_cyclic_and_distinct`, and drop `crab_left`/
  `crab_right` from `test_gaits_have_cycles_and_oneshots_do_not`'s cyclic list);
  add equivalent coverage for `look_up`/`look_down` (cyclic, valid angles, added
  to the cyclic-gaits list).
- `bridge/tests/webapp/test_motion.py`: remove `strafe()` tests, add `look()`
  tests mirroring the existing `turn()` test shapes (control-gating, invalid
  direction, pose-running conflict, success).
- `bridge/tests/webapp/test_ws.py`: remove `"strafe"` dispatch test if one
  exists, add `"look"` dispatch test mirroring the existing `"turn"` test.
- No frontend test changes needed beyond the existing static-integrity check
  (this repo has no functional JS test suite, per prior tasks in this project).
