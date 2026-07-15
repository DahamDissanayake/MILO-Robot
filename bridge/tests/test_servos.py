import asyncio

import pytest

from milo_bridge.drivers import servos as sv
from milo_bridge.drivers.servos import ServoDriver


class FakeChannel:
    def __init__(self):
        self.duty_cycle = 0


class FakePca:
    def __init__(self):
        self.channels = [FakeChannel() for _ in range(16)]


def test_angle_to_pulse_endpoints():
    assert sv.angle_to_pulse_us(0) == 500
    assert sv.angle_to_pulse_us(90) == 1500
    assert sv.angle_to_pulse_us(180) == 2500
    assert sv.angle_to_pulse_us(-20) == 500      # clamped
    assert sv.angle_to_pulse_us(400) == 2500     # clamped


def test_pulse_to_duty_16bit():
    # 1500us of a 20000us period -> 7.5% of 0xFFFF
    assert sv.pulse_us_to_duty(1500) == round(1500 / 20000 * 0xFFFF)
    assert sv.pulse_us_to_duty(20000) == 0xFFFF


def test_channel_map_matches_firmware():
    assert sv.SERVO_CHANNELS == {
        "R1": 0, "R2": 1, "L1": 2, "L2": 3, "R4": 4, "R3": 5, "L3": 6, "L4": 7,
    }


def test_set_angle_by_name_and_channel():
    pca = FakePca()
    driver = ServoDriver(pca, stagger_ms=0)
    driver.set_angle("R3", 90)
    assert pca.channels[5].duty_cycle == sv.pulse_us_to_duty(1500)
    driver.set_angle(0, 0)
    assert pca.channels[0].duty_cycle == sv.pulse_us_to_duty(500)


def test_angle_to_pulse_custom_range():
    assert sv.angle_to_pulse_us(0, min_us=600, max_us=2400) == 600
    assert sv.angle_to_pulse_us(180, min_us=600, max_us=2400) == 2400
    assert sv.angle_to_pulse_us(90, min_us=600, max_us=2400) == 1500


def test_calibrated_range_hits_true_endpoints():
    pca = FakePca()
    ranges = [(600, 2400)] + [sv.DEFAULT_PULSE_RANGE] * 7
    driver = ServoDriver(pca, pulse_ranges=ranges, stagger_ms=0)
    driver.set_angle(0, 0)
    assert pca.channels[0].duty_cycle == sv.pulse_us_to_duty(600)
    driver.set_angle(0, 180)
    assert pca.channels[0].duty_cycle == sv.pulse_us_to_duty(2400)
    driver.set_angle(0, 90)
    assert pca.channels[0].duty_cycle == sv.pulse_us_to_duty(1500)


def test_uncalibrated_channel_still_hits_default_endpoints():
    pca = FakePca()
    driver = ServoDriver(pca, stagger_ms=0)
    driver.set_angle("R3", 0)
    assert pca.channels[5].duty_cycle == sv.pulse_us_to_duty(500)
    driver.set_angle("R3", 180)
    assert pca.channels[5].duty_cycle == sv.pulse_us_to_duty(2500)


def test_set_pose_staggers_between_writes():
    pca = FakePca()
    sleeps: list[float] = []

    async def fake_sleep(s):
        sleeps.append(s)

    driver = ServoDriver(pca, stagger_ms=20, sleep=fake_sleep)
    asyncio.run(driver.set_pose({"R1": 90, "R2": 90, "L1": 90}))
    # two gaps for three writes, 20ms each
    assert sleeps == [0.02, 0.02]


def test_wrong_pulse_range_count_rejected():
    with pytest.raises(ValueError):
        ServoDriver(FakePca(), pulse_ranges=[(500, 2500), (500, 2500)])


def test_relax_zeroes_all_channels():
    pca = FakePca()
    driver = ServoDriver(pca, stagger_ms=0)
    for name in sv.SERVO_CHANNELS:
        driver.set_angle(name, 90)
    driver.relax()
    assert all(pca.channels[c].duty_cycle == 0 for c in range(8))
    assert driver.last_angle("R1") is None
