"""Smooth interpolation layer over ServoDriver.

Every existing caller (poses, gait, manual servo commands) currently
snaps a servo straight to its commanded angle -- fine for a single value,
jarring as motion. SmoothServos exposes the same interface as ServoDriver
(set_angle/set_pose/last_angle/relax) so it's a drop-in replacement, but
set_angle/set_pose only record a *target*; a separate periodic tick()
steps every channel toward its target at a bounded slew rate and performs
the actual hardware write. This lets both GaitEngine (which already calls
set_angle every ~20ms itself) and PoseRunner (one-shot calls that rely on
a subsequent `wait_ms` sleep to give the move time to land) share one
mechanism without either blocking the other -- a version that
blocked-and-ramped inside the call itself would stall GaitEngine's own
50Hz loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

from .servos import SERVO_NAMES

DEFAULT_SLEW_DEG_PER_S = 300.0
TICK_HZ = 50


class SmoothServos:
    def __init__(
        self,
        servos,
        slew_deg_per_s: float = DEFAULT_SLEW_DEG_PER_S,
        stagger_ms: int = 20,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self._servos = servos
        self._slew = slew_deg_per_s
        self._stagger_s = stagger_ms / 1000
        self._sleep = sleep
        self._clock = clock
        self._targets: dict[str, float] = {}
        self._pre_relax_targets: dict[str, float] = {}
        self._last_t: float | None = None
        self._task: asyncio.Task | None = None

    def set_angle(self, servo: str, angle: float) -> None:
        self._targets[servo] = angle

    async def set_pose(self, angles: Mapping[str, float], stagger: bool = True) -> None:
        """Record a target per channel, staggering *when* each target starts
        moving (not the write itself, which now happens on tick()) so all
        servos don't begin slewing in the same instant."""
        for i, (name, angle) in enumerate(angles.items()):
            self.set_angle(name, angle)
            if stagger and self._stagger_s and i < len(angles) - 1:
                await self._sleep(self._stagger_s)

    def last_angle(self, servo):
        return self._servos.last_angle(servo)

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

    def tick(self) -> None:
        now = self._clock()
        dt = (now - self._last_t) if self._last_t is not None else 1.0 / TICK_HZ
        self._last_t = now
        max_step = self._slew * dt
        for name, target in self._targets.items():
            current = self._servos.last_angle(name)
            if current is None:
                self._servos.set_angle(name, target)
                continue
            delta = target - current
            if abs(delta) <= max_step:
                if delta != 0:
                    self._servos.set_angle(name, target)
            else:
                self._servos.set_angle(name, current + max_step * (1 if delta > 0 else -1))

    async def run(self) -> None:
        interval = 1.0 / TICK_HZ
        while True:
            started = self._clock()
            self.tick()
            elapsed = self._clock() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.ensure_future(self.run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
