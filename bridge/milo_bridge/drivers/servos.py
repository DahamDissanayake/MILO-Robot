"""PCA9685 servo driver.

Carries over two hard-won lessons from the Sesame ESP32 firmware:
per-servo trim offsets (calibrate without disassembly) and staggered
multi-servo writes (simultaneous starts on 8 MG90s brown out the rail).

The PCA9685 object is injected so all angle/duty math tests run off-hardware;
``ServoDriver.from_hardware()`` builds the real I2C device on the Pi.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping

PCA9685_ADDRESS = 0x40
PWM_FREQUENCY_HZ = 50
PULSE_MIN_US = 500
PULSE_MAX_US = 2500

# Channel map inherited from the Sesame firmware (movement-sequences.h).
SERVO_CHANNELS: dict[str, int] = {
    "R1": 0, "R2": 1, "L1": 2, "L2": 3,
    "R4": 4, "R3": 5, "L3": 6, "L4": 7,
}
SERVO_NAMES = tuple(SERVO_CHANNELS)
NUM_SERVOS = len(SERVO_CHANNELS)


def angle_to_pulse_us(angle: float) -> float:
    angle = min(max(angle, 0.0), 180.0)
    return PULSE_MIN_US + (angle / 180.0) * (PULSE_MAX_US - PULSE_MIN_US)


def pulse_us_to_duty(pulse_us: float, freq_hz: int = PWM_FREQUENCY_HZ) -> int:
    """16-bit duty-cycle value as used by adafruit-circuitpython-pca9685."""
    period_us = 1_000_000 / freq_hz
    return round(pulse_us / period_us * 0xFFFF)


class ServoDriver:
    """8-servo driver with trims and staggered writes.

    ``pca`` must expose ``channels[i].duty_cycle`` (the Adafruit PCA9685 API).
    """

    def __init__(
        self,
        pca,
        trims: Iterable[int] = (0,) * NUM_SERVOS,
        stagger_ms: int = 20,
        sleep=asyncio.sleep,
    ):
        self._pca = pca
        self.trims = list(trims)
        if len(self.trims) != NUM_SERVOS:
            raise ValueError(f"need {NUM_SERVOS} trim values, got {len(self.trims)}")
        self.stagger_ms = stagger_ms
        self._sleep = sleep
        self._last_angles: list[float | None] = [None] * NUM_SERVOS

    @classmethod
    def from_hardware(cls, trims: Iterable[int] = (0,) * NUM_SERVOS, stagger_ms: int = 20):
        import board  # type: ignore
        import busio  # type: ignore
        from adafruit_pca9685 import PCA9685  # type: ignore

        i2c = busio.I2C(board.SCL, board.SDA)
        pca = PCA9685(i2c, address=PCA9685_ADDRESS)
        pca.frequency = PWM_FREQUENCY_HZ
        return cls(pca, trims=trims, stagger_ms=stagger_ms)

    def _write(self, channel: int, angle: float) -> None:
        trimmed = min(max(angle + self.trims[channel], 0.0), 180.0)
        duty = pulse_us_to_duty(angle_to_pulse_us(trimmed))
        self._pca.channels[channel].duty_cycle = duty
        self._last_angles[channel] = angle

    def set_angle(self, servo: int | str, angle: float) -> None:
        channel = SERVO_CHANNELS[servo] if isinstance(servo, str) else servo
        self._write(channel, angle)

    async def set_pose(self, angles: Mapping[str, float], stagger: bool = True) -> None:
        """Write several servos, pausing ``stagger_ms`` between each write."""
        for i, (name, angle) in enumerate(angles.items()):
            self.set_angle(name, angle)
            if stagger and self.stagger_ms and i < len(angles) - 1:
                await self._sleep(self.stagger_ms / 1000)

    def last_angle(self, servo: int | str) -> float | None:
        channel = SERVO_CHANNELS[servo] if isinstance(servo, str) else servo
        return self._last_angles[channel]

    def relax(self) -> None:
        """Stop driving all channels (servos go limp; saves power while asleep)."""
        for channel in range(NUM_SERVOS):
            self._pca.channels[channel].duty_cycle = 0
            self._last_angles[channel] = None
