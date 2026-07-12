import asyncio
from datetime import datetime, timezone

from milo_bridge.drivers.servos import ServoDriver

from iot_tester.results_log import ResultRecorder
from iot_tester.screens.servos import SWEEP_UP_ANGLES, ServoScreen, run_sweep


class FakeChannel:
    def __init__(self) -> None:
        self.duty_cycle = 0


class FakePca:
    def __init__(self) -> None:
        self.channels = [FakeChannel() for _ in range(16)]


def test_run_sweep_moves_through_every_angle() -> None:
    driver = ServoDriver(FakePca(), stagger_ms=0)
    asyncio.run(run_sweep(driver, "R1", SWEEP_UP_ANGLES, step_delay_s=0))
    assert driver.last_angle("R1") == 180


def test_run_sweep_ends_at_last_angle_in_sequence() -> None:
    driver = ServoDriver(FakePca(), stagger_ms=0)
    asyncio.run(run_sweep(driver, "L3", (10, 20, 30), step_delay_s=0))
    assert driver.last_angle("L3") == 30


def test_servo_screen_composes_without_error(tmp_path) -> None:
    recorder = ResultRecorder(tmp_path, datetime.now(timezone.utc))
    screen = ServoScreen(recorder)
    widgets = list(screen.compose())
    assert len(widgets) > 0
