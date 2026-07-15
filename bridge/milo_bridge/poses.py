"""Scripted poses and gaits, ported angle-for-angle from the Sesame firmware
(``hardware/reference-sesame/movement-sequences.h``). The angles transfer
directly because the printed body and servo-horn geometry are unchanged.

A pose is a sequence of Steps; each step writes some servos (staggered) and
waits. Cyclic gaits (walk/turns) have entry steps plus a repeatable cycle.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .drivers.display import AnimMode

FRAME_DELAY_MS = 120  # firmware `frameDelay` between gait sub-steps
DEFAULT_WALK_CYCLES = 4  # firmware `walkCycles`

REST_ANGLES = {n: 90 for n in ("R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4")}
STAND_ANGLES = {"R1": 135, "R2": 45, "L1": 45, "L2": 135, "R4": 0, "R3": 180, "L3": 0, "L4": 180}


@dataclass(frozen=True)
class Step:
    updates: dict[str, int]
    wait_ms: int = 0


@dataclass(frozen=True)
class Pose:
    name: str
    face: str
    face_mode: AnimMode
    steps: list[Step]
    cycle: list[Step] = field(default_factory=list)  # repeated `cycles` times if set
    end_stand: bool = True


def _repeat(steps: list[Step], times: int) -> list[Step]:
    return steps * times


_STAND_STEP = [Step(STAND_ANGLES, 200)]

POSES: dict[str, Pose] = {
    "rest": Pose("rest", "rest", AnimMode.BOOMERANG, [Step(REST_ANGLES)], end_stand=False),
    "stand": Pose("stand", "stand", AnimMode.ONCE, [Step(STAND_ANGLES)], end_stand=False),
    "wave": Pose(
        "wave", "wave", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"R4": 80, "L3": 180, "L2": 90, "R1": 100}, 200), Step({"L3": 180}, 300)]
        + _repeat([Step({"L3": 180}, 300), Step({"L3": 100}, 300)], 4),
    ),
    "dance": Pose(
        "dance", "dance", AnimMode.LOOP,
        [Step({"R1": 90, "R2": 90, "L1": 90, "L2": 90, "R4": 160, "R3": 160, "L3": 10, "L4": 10}, 300)]
        + _repeat(
            [
                Step({"R4": 115, "R3": 115, "L3": 10, "L4": 10}, 300),
                Step({"R4": 160, "R3": 160, "L3": 65, "L4": 65}, 300),
            ],
            5,
        ),
    ),
    "swim": Pose(
        "swim", "swim", AnimMode.ONCE,
        [Step(REST_ANGLES)]
        + _repeat(
            [
                Step({"R1": 135, "R2": 45, "L1": 45, "L2": 135}, 400),
                Step({"R1": 90, "R2": 90, "L1": 90, "L2": 90}, 400),
            ],
            4,
        ),
    ),
    "point": Pose(
        "point", "point", AnimMode.BOOMERANG,
        [Step({"L2": 90, "R1": 135, "R2": 100, "L4": 180, "L1": 25, "L3": 145, "R4": 80, "R3": 170}, 2000)],
    ),
    "pushup": Pose(
        "pushup", "pushup", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"L1": 0, "R1": 180, "L3": 90, "R3": 90}, 500)]
        + _repeat([Step({"L3": 0, "R3": 180}, 600), Step({"L3": 90, "R3": 90}, 500)], 4),
    ),
    "bow": Pose(
        "bow", "bow", AnimMode.ONCE,
        _STAND_STEP
        + [
            Step({"L1": 0, "R1": 180, "L3": 0, "R3": 180, "L2": 180, "R2": 0, "R4": 0, "L4": 180}, 600),
            Step({"L3": 90, "R3": 90}, 3000),
        ],
    ),
    "cute": Pose(
        "cute", "cute", AnimMode.ONCE,
        _STAND_STEP
        + [
            Step({"L2": 160, "R2": 20, "R4": 180, "L4": 0}, 0),
            Step({"L1": 0, "R1": 180, "L3": 180, "R3": 0}, 200),
        ]
        + _repeat([Step({"R4": 180, "L4": 45}, 300), Step({"R4": 135, "L4": 0}, 300)], 5),
    ),
    "freaky": Pose(
        "freaky", "freaky", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"L1": 0, "R1": 180, "L2": 180, "R2": 0, "R4": 90, "R3": 0}, 200)]
        + _repeat([Step({"R3": 25}, 400), Step({"R3": 0}, 400)], 3),
    ),
    "worm": Pose(
        "worm", "worm", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"R1": 180, "R2": 0, "L1": 0, "L2": 180, "R4": 90, "R3": 90, "L3": 90, "L4": 90}, 200)]
        + _repeat(
            [
                Step({"R3": 45, "L3": 135, "R4": 45, "L4": 135}, 300),
                Step({"R3": 135, "L3": 45, "R4": 135, "L4": 45}, 300),
            ],
            5,
        ),
    ),
    "shake": Pose(
        "shake", "shake", AnimMode.ONCE,
        _STAND_STEP
        + [Step({"R1": 135, "L1": 45, "L3": 90, "R3": 90, "L2": 90, "R2": 90}, 200)]
        + _repeat([Step({"R4": 45, "L4": 135}, 300), Step({"R4": 0, "L4": 180}, 300)], 5),
    ),
    "shrug": Pose(
        "shrug", "shrug", AnimMode.ONCE,
        _STAND_STEP
        + [
            Step({"R3": 90, "R4": 90, "L3": 90, "L4": 90}, 1000),
            Step({"R3": 0, "R4": 180, "L3": 180, "L4": 0}, 1500),
        ],
    ),
    "dead": Pose(
        "dead", "dead", AnimMode.BOOMERANG,
        _STAND_STEP + [Step({"R3": 90, "R4": 90, "L3": 90, "L4": 90}, 200)],
        end_stand=False,
    ),
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
    # --- cyclic gaits (diagonal trot, from the firmware) ---
    "walk": Pose(
        "walk", "walk", AnimMode.ONCE,
        [Step({"R3": 135, "L3": 45, "R2": 100, "L1": 25}, FRAME_DELAY_MS)],
        cycle=[
            Step({"R3": 135, "L3": 0}, FRAME_DELAY_MS),
            Step({"L4": 135, "L2": 90, "R4": 0, "R1": 180}, FRAME_DELAY_MS),
            Step({"R2": 45, "L1": 90}, FRAME_DELAY_MS),
            Step({"R4": 45, "L4": 180}, FRAME_DELAY_MS),
            Step({"R3": 180, "L3": 45, "R2": 90, "L1": 0}, FRAME_DELAY_MS),
            Step({"L2": 135, "R1": 90}, FRAME_DELAY_MS),
        ],
    ),
    "walk_backward": Pose(
        "walk_backward", "walk", AnimMode.ONCE,
        [],
        cycle=[
            Step({"R3": 135, "L3": 0}, FRAME_DELAY_MS),
            Step({"L4": 135, "L2": 135, "R4": 0, "R1": 90}, FRAME_DELAY_MS),
            Step({"R2": 90, "L1": 0}, FRAME_DELAY_MS),
            Step({"R4": 45, "L4": 180}, FRAME_DELAY_MS),
            Step({"R3": 180, "L3": 45, "R2": 45, "L1": 90}, FRAME_DELAY_MS),
            Step({"L2": 90, "R1": 180}, FRAME_DELAY_MS),
        ],
    ),
    "turn_left": Pose(
        "turn_left", "walk", AnimMode.ONCE,
        [],
        cycle=[
            Step({"R3": 135, "L4": 135}, FRAME_DELAY_MS),
            Step({"R1": 180, "L2": 180}, FRAME_DELAY_MS),
            Step({"R3": 180, "L4": 180}, FRAME_DELAY_MS),
            Step({"R1": 135, "L2": 135}, FRAME_DELAY_MS),
            Step({"R4": 45, "L3": 45}, FRAME_DELAY_MS),
            Step({"R2": 90, "L1": 90}, FRAME_DELAY_MS),
            Step({"R4": 0, "L3": 0}, FRAME_DELAY_MS),
            Step({"R2": 45, "L1": 45}, FRAME_DELAY_MS),
        ],
    ),
    "turn_right": Pose(
        "turn_right", "walk", AnimMode.ONCE,
        [],
        cycle=[
            Step({"R4": 45, "L3": 45}, FRAME_DELAY_MS),
            Step({"R2": 0, "L1": 0}, FRAME_DELAY_MS),
            Step({"R4": 0, "L3": 0}, FRAME_DELAY_MS),
            Step({"R2": 45, "L1": 45}, FRAME_DELAY_MS),
            Step({"R3": 135, "L4": 135}, FRAME_DELAY_MS),
            Step({"R1": 90, "L2": 90}, FRAME_DELAY_MS),
            Step({"R3": 180, "L4": 180}, FRAME_DELAY_MS),
            Step({"R1": 135, "L2": 135}, FRAME_DELAY_MS),
        ],
    ),
}


class PoseRunner:
    """Executes poses on the servo driver, with the matching face and
    firmware-style interruptibility (abort mid-gait -> stand)."""

    def __init__(self, servos, display=None, sleep=asyncio.sleep):
        self._servos = servos
        self._display = display
        self._sleep = sleep
        self._abort = asyncio.Event()
        self.is_running = False

    def abort(self) -> None:
        self._abort.set()

    async def run(self, name: str, cycles: int = DEFAULT_WALK_CYCLES) -> bool:
        """Run a pose to completion. Returns False if aborted early."""
        pose = POSES[name]
        self._abort.clear()
        self.is_running = True
        try:
            if self._display is not None:
                await self._display.set_face(pose.face, pose.face_mode)

            completed = await self._run_steps(pose.steps)
            if completed and pose.cycle:
                for _ in range(cycles):
                    if not await self._run_steps(pose.cycle):
                        completed = False
                        break
            if pose.end_stand or pose.cycle or not completed:
                # Recovery stand must run even after an abort (firmware behavior:
                # releasing the button mid-walk always ends in runStandPose).
                await self._servos.set_pose(STAND_ANGLES)
                if self._display is not None and completed:
                    self._display.start_idle()
            return completed
        finally:
            self.is_running = False

    async def _run_steps(self, steps: list[Step]) -> bool:
        for step in steps:
            if self._abort.is_set():
                return False
            await self._servos.set_pose(step.updates)
            if step.wait_ms:
                await self._sleep(step.wait_ms / 1000)
        return True
