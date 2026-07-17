from milo_bridge.characterize import ImuSample, MovementReport, analyze_samples


def _samples(rows):
    return [ImuSample(t=t, roll=r, pitch=p, gyro=(gx, gy, gz)) for t, r, p, gx, gy, gz in rows]


def test_peak_and_residual_are_reported():
    samples = _samples([
        (0.0, 0.0, 0.0, 0, 0, 0),
        (0.5, 10.0, -5.0, 20, 0, 0),
        (1.0, 2.0, -1.0, 5, 0, 0),
        (1.5, 0.5, -0.2, 1, 0, 0),
    ])
    report = analyze_samples("wave", samples, movement_end_s=1.0)
    assert report.name == "wave"
    assert report.peak_roll == 10.0
    assert report.peak_pitch == 5.0  # magnitude, not signed
    assert report.residual_roll == 0.5
    assert report.residual_pitch == 0.2
    assert report.peak_gyro == 20.0


def test_settle_time_is_first_point_after_movement_end_holding_below_threshold():
    samples = _samples([
        (0.0, 0.0, 0.0, 0, 0, 0),
        (1.0, 15.0, 0.0, 0, 0, 0),   # during the movement, ignored for settle
        (1.1, 8.0, 0.0, 0, 0, 0),    # still above threshold, after movement_end_s=1.0
        (1.4, 2.0, 0.0, 0, 0, 0),    # under threshold...
        (2.0, 1.0, 0.0, 0, 0, 0),    # ...and stays under for >= settle_hold_s=0.5 from t=1.4
    ])
    report = analyze_samples("bow", samples, movement_end_s=1.0, settle_threshold_deg=3.0, settle_hold_s=0.5)
    assert report.settle_time_s == 1.4 - 1.0  # 0.4s after the movement ended


def test_settle_time_is_none_when_it_never_settles():
    samples = _samples([(t, 20.0, 0.0, 0, 0, 0) for t in [0.0, 0.5, 1.0, 1.5, 2.0]])
    report = analyze_samples("dance", samples, movement_end_s=0.5)
    assert report.settle_time_s is None


def test_unsafe_when_peak_tilt_exceeds_the_ceiling():
    samples = _samples([(0.0, 50.0, 0.0, 0, 0, 0), (0.5, 0.0, 0.0, 0, 0, 0)])
    report = analyze_samples("crab", samples, movement_end_s=0.5, safety_ceiling_deg=45.0)
    assert report.safe is False


def test_safe_when_within_the_ceiling():
    samples = _samples([(0.0, 10.0, 5.0, 0, 0, 0), (0.5, 1.0, 0.5, 0, 0, 0)])
    report = analyze_samples("look_up", samples, movement_end_s=0.5, safety_ceiling_deg=45.0)
    assert report.safe is True


import asyncio
import json
from pathlib import Path

from milo_bridge.characterize import run_characterization


class FakeServos:
    pass


class FakeGait:
    def __init__(self):
        self.standby_calls = 0

    def standby(self):
        self.standby_calls += 1

    def set_velocity_command(self, vx, vy, yaw):
        pass


class FakeImu:
    def __init__(self, rolls):
        self._rolls = iter(rolls)
        self.last = 0.0

    def update(self):
        from milo_bridge.drivers.imu import ImuState

        try:
            self.last = next(self._rolls)
        except StopIteration:
            pass
        return ImuState(roll=self.last, pitch=0.0, yaw=0.0, gyro=(0.0, 0.0, 0.0), accel=(0.0, 0.0, 1.0))


class FakeRunner:
    def __init__(self):
        self.ran: list[str] = []

    async def run(self, name, cycles=None):
        self.ran.append(name)
        return True


def test_run_characterization_writes_report_and_calls_standby_between_moves(tmp_path):
    servos = FakeServos()
    gait = FakeGait()
    runner = FakeRunner()
    imu = FakeImu([5.0, 1.0, 0.5, 20.0, 3.0, 0.5])  # small, safe wiggles for two poses

    async def main():
        return await run_characterization(
            servos, imu, runner, gait, names=["wave", "bow"], out_dir=tmp_path,
            safety_ceiling_deg=45.0, settle_window_s=0.05, between_pause_s=0.0,
        )

    reports = asyncio.run(main())
    assert [r.name for r in reports] == ["wave", "bow"]
    assert all(r.safe for r in reports)
    assert gait.standby_calls == 2

    report_md = (tmp_path / "report.md").read_text()
    assert "wave" in report_md and "bow" in report_md
    data = json.loads((tmp_path / "data.json").read_text())
    assert set(data.keys()) == {"wave", "bow"}


def test_run_characterization_flags_an_unsafe_pose_but_continues(tmp_path):
    servos, gait, runner = FakeServos(), FakeGait(), FakeRunner()
    imu = FakeImu([60.0, 0.5, 1.0, 0.5])  # first pose spikes past the 45deg ceiling

    async def main():
        return await run_characterization(
            servos, imu, runner, gait, names=["crab", "look_up"], out_dir=tmp_path,
            safety_ceiling_deg=45.0, settle_window_s=0.05, between_pause_s=0.0,
        )

    reports = asyncio.run(main())
    assert reports[0].name == "crab" and reports[0].safe is False
    assert reports[1].name == "look_up" and reports[1].safe is True
    assert runner.ran == ["crab", "look_up"]  # continued past the unsafe one
