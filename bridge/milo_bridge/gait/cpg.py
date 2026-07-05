"""CPG fallback gait: a parameterized diagonal trot from sine oscillators.

Two diagonal pairs run in anti-phase (FL+RR vs FR+RL), the same scheme as the
firmware's scripted walk. Hips swing the legs fore/aft; knees lift during the
swing phase. Left/right servos mirror, so per-servo signs map a positive
"forward swing" onto the correct rotation direction (derived from the stand
pose and walk keyframes in poses.py).

This backend must always work: it ships before the RL policy and stays as the
fallback if sim-to-real stalls (plan D.7).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..poses import STAND_ANGLES

# leg -> (hip servo, knee servo, phase offset, mirror sign)
LEGS = {
    "FL": ("L1", "L2", 0.0, -1.0),
    "RR": ("R3", "R4", 0.0, +1.0),
    "FR": ("R1", "R2", math.pi, +1.0),
    "RL": ("L3", "L4", math.pi, -1.0),
}

# The rear hips *stand* at their servo limits (R3=180, L3=0), so the gait
# oscillates around a mid-range neutral instead — the firmware walk moves
# them within 135-180 / 0-45 for exactly this reason.
GAIT_NEUTRAL = dict(STAND_ANGLES) | {"R3": 157.5, "L3": 22.5}

MAX_VX = 0.15       # m/s command that maps to full hip amplitude
MAX_YAW = 45.0      # deg/s command that maps to full differential


@dataclass
class CpgParams:
    frequency_hz: float = 1.5
    hip_amplitude_deg: float = 25.0    # fore/aft swing at full forward command
    knee_lift_deg: float = 30.0        # lift during swing phase
    duty: float = 0.5                  # fraction of cycle in swing


class CpgGait:
    """Stateless oscillator: angles are a pure function of (t, command)."""

    def __init__(self, params: CpgParams | None = None):
        self.params = params or CpgParams()

    def angles_at(self, t: float, vx: float, vy: float, yaw_rate: float) -> dict[str, float]:
        """Servo angles (absolute degrees) for time ``t`` and a velocity command.

        ``vy`` is accepted for interface parity but ignored — Milo turns in
        place instead of strafing.
        """
        p = self.params
        forward = max(-1.0, min(1.0, vx / MAX_VX))
        turn = max(-1.0, min(1.0, yaw_rate / MAX_YAW))
        if abs(forward) < 1e-3 and abs(turn) < 1e-3:
            return dict(STAND_ANGLES)

        phase = 2 * math.pi * p.frequency_hz * t
        angles: dict[str, float] = {}
        for leg, (hip, knee, offset, mirror) in LEGS.items():
            # Positive turn (clockwise/right): left legs stride longer, right shorter.
            side = 1.0 if leg[1] == "L" else -1.0
            drive = forward + side * turn
            drive = max(-1.0, min(1.0, drive))

            swing = math.sin(phase + offset)
            hip_delta = p.hip_amplitude_deg * drive * swing
            # Knee lifts on the half-cycle when the leg swings forward.
            lift_gate = math.sin(phase + offset + math.pi / 2)
            knee_delta = p.knee_lift_deg * abs(drive) * max(0.0, lift_gate)

            # Right legs (+mirror): forward swing and lift increase the angle;
            # left legs decrease it (matches the firmware walk keyframes).
            angles[hip] = _clamp(GAIT_NEUTRAL[hip] + mirror * hip_delta)
            angles[knee] = _clamp(GAIT_NEUTRAL[knee] + mirror * knee_delta)
        return angles


def _clamp(angle: float) -> float:
    return min(max(angle, 0.0), 180.0)
