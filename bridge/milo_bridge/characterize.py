"""Offline IMU characterization: runs every pose/gait on real hardware and
reports how it actually behaves (peak tilt, settle time, safety), so
gait/servo tuning changes can be diffed against a known-good baseline
instead of trusting a human watching the robot.

This module is split into pure analysis (testable off-hardware) and
hardware orchestration (Task 19, real-robot only).
"""
from __future__ import annotations

import asyncio
import json as json_module
from dataclasses import dataclass
from pathlib import Path


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


SAMPLE_HZ = 50


async def _sample_during(imu, coro, settle_window_s: float = 1.0, clock=None) -> tuple[list[ImuSample], float]:
    """Runs ``coro`` to completion while sampling ``imu`` at SAMPLE_HZ; keeps
    sampling for ``settle_window_s`` afterward so settle time has something
    to measure against. ``settle_window_s`` is injectable (default 1.0s on
    real hardware) so tests don't have to spend a real wall-clock second
    per movement."""
    import time

    clock = clock or time.monotonic
    t0 = clock()
    samples: list[ImuSample] = []
    task = asyncio.ensure_future(coro)

    async def sample_loop():
        while True:
            state = imu.update()
            samples.append(ImuSample(t=clock() - t0, roll=state.roll, pitch=state.pitch, gyro=state.gyro))
            await asyncio.sleep(1.0 / SAMPLE_HZ)

    sampler = asyncio.ensure_future(sample_loop())
    await task
    movement_end_s = clock() - t0
    await asyncio.sleep(settle_window_s)
    sampler.cancel()
    try:
        await sampler
    except asyncio.CancelledError:
        pass
    return samples, movement_end_s


def _write_report(reports: list[MovementReport], out_dir: Path, raw: dict[str, list[ImuSample]]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lines = ["| pose | peak roll | peak pitch | settle (s) | safe |", "|---|---|---|---|---|"]
    for r in reports:
        settle = f"{r.settle_time_s:.2f}" if r.settle_time_s is not None else "never"
        lines.append(f"| {r.name} | {r.peak_roll:.1f} | {r.peak_pitch:.1f} | {settle} | {'yes' if r.safe else 'NO'} |")
    (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
    data = {
        name: [{"t": s.t, "roll": s.roll, "pitch": s.pitch, "gyro": list(s.gyro)} for s in samples]
        for name, samples in raw.items()
    }
    (out_dir / "data.json").write_text(json_module.dumps(data, indent=2), encoding="utf-8")


async def run_characterization(
    servos, imu, runner, gait, names: list[str], out_dir: Path,
    safety_ceiling_deg: float = 45.0, settle_window_s: float = 1.0, between_pause_s: float = 0.1,
) -> list[MovementReport]:
    reports: list[MovementReport] = []
    raw: dict[str, list[ImuSample]] = {}
    for name in names:
        samples, movement_end_s = await _sample_during(imu, runner.run(name), settle_window_s=settle_window_s)
        report = analyze_samples(name, samples, movement_end_s, safety_ceiling_deg=safety_ceiling_deg)
        reports.append(report)
        raw[name] = samples
        gait.standby()
        await asyncio.sleep(between_pause_s)  # let standby's own slew settle before the next pose
    _write_report(reports, Path(out_dir), raw)
    return reports
