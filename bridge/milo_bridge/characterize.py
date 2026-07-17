"""Offline IMU characterization: runs every pose/gait on real hardware and
reports how it actually behaves (peak tilt, settle time, safety), so
gait/servo tuning changes can be diffed against a known-good baseline
instead of trusting a human watching the robot.

This module is split into pure analysis (testable off-hardware) and
hardware orchestration (Task 19, real-robot only).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImuSample:
    t: float
    roll: float
    pitch: float
    gyro: tuple[float, float, float]


@dataclass(frozen=True)
class MovementReport:
    name: str
    peak_roll: float
    peak_pitch: float
    residual_roll: float
    residual_pitch: float
    peak_gyro: float
    settle_time_s: float | None
    safe: bool


def analyze_samples(
    name: str,
    samples: list[ImuSample],
    movement_end_s: float,
    safety_ceiling_deg: float = 45.0,
    settle_threshold_deg: float = 3.0,
    settle_hold_s: float = 0.5,
) -> MovementReport:
    peak_roll = max(abs(s.roll) for s in samples)
    peak_pitch = max(abs(s.pitch) for s in samples)
    peak_gyro = max(max(abs(g) for g in s.gyro) for s in samples)
    last = samples[-1]
    residual_roll = abs(last.roll)
    residual_pitch = abs(last.pitch)

    # First post-movement sample from which every remaining sample (to the
    # end of the recording) stays under the settle threshold on both axes,
    # provided the recording actually covers at least settle_hold_s of real
    # time from that point -- a candidate near the very end of a short
    # recording hasn't actually demonstrated it *stays* settled.
    settle_time_s = None
    after = [s for s in samples if s.t >= movement_end_s]
    for i, candidate in enumerate(after):
        tail = after[i:]
        stayed_under = all(
            abs(s.roll) < settle_threshold_deg and abs(s.pitch) < settle_threshold_deg for s in tail
        )
        if stayed_under and tail[-1].t - candidate.t >= settle_hold_s:
            settle_time_s = candidate.t - movement_end_s
            break

    safe = peak_roll <= safety_ceiling_deg and peak_pitch <= safety_ceiling_deg
    return MovementReport(
        name=name, peak_roll=peak_roll, peak_pitch=peak_pitch,
        residual_roll=residual_roll, residual_pitch=residual_pitch,
        peak_gyro=peak_gyro, settle_time_s=settle_time_s, safe=safe,
    )
