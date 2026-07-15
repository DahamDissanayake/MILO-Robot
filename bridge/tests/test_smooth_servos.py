"""Off-hardware tests for SmoothServos: target recording + slew-limited tick()."""
import asyncio

import pytest

from milo_bridge.drivers.servos import ServoDriver
from milo_bridge.drivers.smooth_servos import SmoothServos


class FakeChannel:
    def __init__(self):
        self.duty_cycle = 0


class FakePca:
    def __init__(self):
        self.channels = [FakeChannel() for _ in range(16)]


def _driver():
    return ServoDriver(FakePca(), stagger_ms=0)


def test_set_angle_records_target_without_writing():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.set_angle("R1", 180)
    assert driver.last_angle("R1") is None  # nothing written yet


def test_first_tick_jumps_straight_to_target_when_never_written():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.set_angle("R1", 45)
    smooth.tick()
    assert driver.last_angle("R1") == 45


def test_tick_steps_at_most_the_slew_limit():
    now = {"t": 0.0}
    driver = _driver()
    smooth = SmoothServos(driver, slew_deg_per_s=300.0, clock=lambda: now["t"])
    smooth.set_angle("R1", 0)
    smooth.tick()  # establishes a baseline at 0deg
    assert driver.last_angle("R1") == 0
    smooth.set_angle("R1", 180)  # big jump requested
    now["t"] = 0.02  # 20ms later (50Hz tick)
    smooth.tick()
    # 300 deg/s * 0.02s = 6deg max step
    assert driver.last_angle("R1") == pytest.approx(6.0)
    now["t"] = 0.04
    smooth.tick()
    assert driver.last_angle("R1") == pytest.approx(12.0)


def test_tick_reaches_target_and_stops_writing():
    now = {"t": 0.0}
    driver = _driver()
    smooth = SmoothServos(driver, slew_deg_per_s=300.0, clock=lambda: now["t"])
    smooth.set_angle("R1", 0)
    smooth.tick()
    smooth.set_angle("R1", 3.0)  # within one tick's slew budget (6deg)
    now["t"] = 0.02
    smooth.tick()
    assert driver.last_angle("R1") == pytest.approx(3.0)
    now["t"] = 0.04
    smooth.tick()  # already at target -- no further movement
    assert driver.last_angle("R1") == pytest.approx(3.0)


def test_set_pose_staggers_target_assignment():
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    driver = _driver()
    smooth = SmoothServos(driver, stagger_ms=20, sleep=fake_sleep, clock=lambda: 0.0)
    asyncio.run(smooth.set_pose({"R1": 90, "R2": 90, "L1": 90}))
    assert sleeps == [0.02, 0.02]
    smooth.tick()
    assert driver.last_angle("R1") == 90
    assert driver.last_angle("R2") == 90
    assert driver.last_angle("L1") == 90


def test_last_angle_reflects_physical_not_target():
    now = {"t": 0.0}
    driver = _driver()
    smooth = SmoothServos(driver, slew_deg_per_s=300.0, clock=lambda: now["t"])
    smooth.set_angle("R1", 0)
    smooth.tick()
    smooth.set_angle("R1", 180)  # far target, not yet reached
    now["t"] = 0.02
    smooth.tick()
    assert smooth.last_angle("R1") == driver.last_angle("R1")
    assert smooth.last_angle("R1") != 180


def test_relax_clears_targets_and_relaxes_driver():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.set_angle("R1", 90)
    smooth.tick()
    assert driver.last_angle("R1") == 90
    smooth.relax()
    assert driver.last_angle("R1") is None
    smooth.tick()  # no pending targets after relax -- nothing to write
    assert driver.last_angle("R1") is None


def test_relax_remembers_pre_relax_targets_for_hold():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.set_angle("R1", 120)
    smooth.tick()
    assert driver.last_angle("R1") == 120
    smooth.relax()
    assert driver.last_angle("R1") is None
    smooth.hold()
    smooth.tick()
    assert driver.last_angle("R1") == 120


def test_hold_without_a_prior_relax_is_a_no_op():
    driver = _driver()
    smooth = SmoothServos(driver, clock=lambda: 0.0)
    smooth.hold()
    smooth.tick()
    assert driver.last_angle("R1") is None
