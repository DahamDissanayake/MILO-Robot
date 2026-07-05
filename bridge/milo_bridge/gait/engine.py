"""The 50 Hz gait control loop.

One interface for all callers — ``set_velocity_command(vx, vy, yaw_rate)`` —
with two backends: the ONNX RL policy (primary) and the CPG trot (fallback).
Zero command -> hold stand and stop writing servos (lets scripted poses run).
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from ..poses import STAND_ANGLES
from .cpg import CpgGait
from .policy import SERVO_ORDER, OnnxPolicy

log = logging.getLogger(__name__)

RATE_HZ = 50


class GaitEngine:
    def __init__(
        self,
        servos,
        imu=None,
        policy_path: Path | str | None = None,
        rate_hz: int = RATE_HZ,
        clock=time.monotonic,
    ):
        self._servos = servos
        self._imu = imu
        self._cpg = CpgGait()
        self._policy: OnnxPolicy | None = None
        if policy_path is not None and Path(policy_path).exists():
            try:
                self._policy = OnnxPolicy(policy_path)
                log.info("gait policy loaded from %s", policy_path)
            except Exception as exc:
                log.warning("policy load failed (%s); CPG fallback active", exc)
        self._rate_hz = rate_hz
        self._clock = clock
        self._command = (0.0, 0.0, 0.0)
        self._active = False
        self._t0 = clock()

    @property
    def backend(self) -> str:
        return "policy" if self._policy is not None else "cpg"

    def set_velocity_command(self, vx: float, vy: float, yaw_rate: float) -> None:
        """vx/vy in m/s, yaw_rate in deg/s. (0,0,0) stops walking."""
        was_active = self._active
        self._command = (vx, vy, yaw_rate)
        self._active = any(abs(c) > 1e-6 for c in self._command)
        if self._active and not was_active:
            self._t0 = self._clock()  # restart the CPG cycle cleanly

    def tick(self) -> dict[str, float] | None:
        """One control step; returns the angles written (None while idle)."""
        if not self._active:
            return None
        vx, vy, yaw = self._command
        if self._policy is not None:
            state = self._imu.update() if self._imu is not None else None
            joints = np.array(
                [self._servos.last_angle(n) or STAND_ANGLES[n] for n in SERVO_ORDER],
                dtype=np.float32,
            )
            angles = self._policy.step(
                joints,
                state.roll if state else 0.0,
                state.pitch if state else 0.0,
                state.gyro if state else (0.0, 0.0, 0.0),
                (vx, vy, yaw),
            )
        else:
            angles = self._cpg.angles_at(self._clock() - self._t0, vx, vy, yaw)
        for name, angle in angles.items():
            self._servos.set_angle(name, angle)
        return angles

    async def run(self) -> None:
        """Drive ticks at rate_hz forever (owns the loop's timing)."""
        interval = 1.0 / self._rate_hz
        while True:
            started = self._clock()
            self.tick()
            elapsed = self._clock() - started
            await asyncio.sleep(max(0.0, interval - elapsed))
