"""The 50 Hz gait control loop.

One interface for all callers -- ``set_velocity_command(vx, vy, yaw_rate)``
-- with two backends: the ONNX RL policy (primary) and the CPG trot
(fallback). Zero command -> hold stand and stop writing servos (lets
scripted poses run), except in balanced/angled mode, which keeps
self-leveling at a standstill.

This is also the robot's mode/reset/standby coordinator: both the web app
and the brain call the same GaitEngine instance, so ``set_mode``/``reset``/
``standby`` apply identically no matter who's driving.

Every servo write here is clamped into the safe angle band
(``SAFE_ANGLE_MIN..SAFE_ANGLE_MAX``) so a full CPG swing or a 0/180 discrete
target can never drive a servo into its mechanical hard-stop -- ServoDriver
enforces the same limit at the hardware gate, this is the same guarantee one
layer up for the highest-frequency writer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from ..drivers.servos import SAFE_ANGLE_MAX, SAFE_ANGLE_MIN
from ..poses import REST_ANGLES, STAND_ANGLES
from . import balance
from .cpg import CpgGait
from .policy import SERVO_ORDER, OnnxPolicy

log = logging.getLogger(__name__)

RATE_HZ = 50
MODES = ("raw", *balance.PARAMS)
_BALANCE_MODES = tuple(balance.PARAMS)


class GaitEngine:
    def __init__(
        self,
        servos,
        imu=None,
        runner=None,
        policy_path: Path | str | None = None,
        rate_hz: int = RATE_HZ,
        clock=time.monotonic,
    ):
        self._servos = servos
        self._imu = imu
        self._runner = runner
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
        self._mode = "balanced"
        self._holding_target: dict[str, float] | None = None
        self._manual_override = False
        self._was_deferring = False
        self._t0 = clock()

    @property
    def backend(self) -> str:
        return "policy" if self._policy is not None else "cpg"

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, name: str) -> None:
        if name not in MODES:
            raise ValueError(f"unknown mode {name!r}")
        self._mode = name

    def set_velocity_command(self, vx: float, vy: float, yaw_rate: float) -> None:
        """vx/vy in m/s, yaw_rate in deg/s. (0,0,0) stops walking."""
        was_active = self._active
        self._command = (vx, vy, yaw_rate)
        self._active = any(abs(c) > 1e-6 for c in self._command)
        if self._active and not was_active:
            self._t0 = self._clock()  # restart the CPG cycle cleanly
            self._holding_target = None
        elif was_active and not self._active and self._mode in _BALANCE_MODES:
            self.standby()

    def reset(self) -> None:
        """Smoothly return every servo to the 90-degree rest angles."""
        self._set_discrete_target(REST_ANGLES)

    def standby(self) -> None:
        """Smoothly return every servo to the stand pose."""
        self._set_discrete_target(STAND_ANGLES)

    def _write(self, name: str, angle: float) -> None:
        # Keep every gait write inside the servos' safe band -- a full CPG
        # swing or a 0/180 discrete target must never reach a mechanical
        # hard-stop (ServoDriver enforces the same limit at the hardware gate).
        self._servos.set_angle(name, min(max(angle, SAFE_ANGLE_MIN), SAFE_ANGLE_MAX))

    def _set_discrete_target(self, angles: dict[str, float]) -> None:
        self._active = False
        self._holding_target = dict(angles)
        for name, angle in angles.items():
            self._write(name, angle)

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
        need_imu = self._policy is not None or self._mode in _BALANCE_MODES
        state = self._imu.update() if (self._imu is not None and need_imu) else None
        if self._policy is not None:
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
        if self._mode in _BALANCE_MODES and state is not None:
            angles = balance.correct(angles, state.roll, state.pitch, self._mode)
        for name, angle in angles.items():
            self._write(name, angle)
        return angles

    def _hold_level(self) -> dict[str, float] | None:
        if self._imu is None:
            return None
        state = self._imu.update()
        base = self._holding_target if self._holding_target is not None else STAND_ANGLES
        angles = balance.correct(dict(base), state.roll, state.pitch, self._mode)
        for name, angle in angles.items():
            self._write(name, angle)
        return angles

    async def run(self) -> None:
        """Drive ticks at rate_hz forever (owns the loop's timing)."""
        interval = 1.0 / self._rate_hz
        while True:
            started = self._clock()
            self.tick()
            elapsed = self._clock() - started
            await asyncio.sleep(max(0.0, interval - elapsed))
