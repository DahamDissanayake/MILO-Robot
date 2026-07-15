"""IMU-fed proportional balance correction, layered on top of whatever the
CPG/policy gait backend already computed for this tick.

Not full inverse kinematics -- a lightweight trim: roll error nudges left
vs right hip angles in opposite directions, pitch error nudges front vs
rear hip angles in opposite directions, both clamped to a per-mode
maximum. Angled (climb) mode reuses the exact same math with a wider
pitch authority so it can hold the body level against a real incline, not
just a walking wobble.

Which absolute direction actually counters a given tilt is a hardware
question this can't answer off-robot -- it only guarantees left/right and
front/rear hips are corrected in *opposite* directions from each other.
Flip the sign of roll_kp/pitch_kp in PARAMS below if it leans the wrong
way on the real robot.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cpg import LEGS


@dataclass(frozen=True)
class BalanceParams:
    roll_kp: float
    pitch_kp: float
    max_correction_deg: float


PARAMS: dict[str, BalanceParams] = {
    "balanced": BalanceParams(roll_kp=0.3, pitch_kp=0.3, max_correction_deg=12.0),
    "angled": BalanceParams(roll_kp=0.25, pitch_kp=0.25, max_correction_deg=30.0),
}


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    """Apply IMU-fed roll/pitch trim to ``angles`` (a full hip+knee angle
    dict as produced by CpgGait.angles_at / OnnxPolicy.step). Returns a new
    dict; ``angles`` is never mutated. ``mode="raw"`` (or any mode without
    tuned params) returns ``angles`` unchanged. Each hip's combined
    roll+pitch correction is clamped to ``max_correction_deg`` -- clamping
    the two axes independently before summing them would let a hip's total
    correction reach up to 2x the documented per-mode maximum when both
    roll and pitch are extreme at once."""
    if mode not in PARAMS:
        return angles
    params = PARAMS[mode]
    roll_term = params.roll_kp * roll_deg
    pitch_term = params.pitch_kp * pitch_deg

    corrected = dict(angles)
    for leg, (hip, *_rest) in LEGS.items():
        if hip not in corrected:
            continue
        side = 1.0 if leg[1] == "L" else -1.0  # opposite sign per side
        front = 1.0 if leg[0] == "F" else -1.0  # opposite sign front vs rear
        delta = _clamp(side * roll_term + front * pitch_term, params.max_correction_deg)
        corrected[hip] = max(0.0, min(180.0, corrected[hip] + delta))
    return corrected
