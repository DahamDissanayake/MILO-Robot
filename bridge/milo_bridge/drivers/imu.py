"""MPU6050 IMU driver: 100 Hz reads -> complementary filter -> roll/pitch + angular velocity.

The I2C bus object is injected (smbus2-compatible: ``read_i2c_block_data``,
``write_byte_data``), so the register decode and filter math test off-hardware.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

MPU6050_ADDRESS = 0x68
REG_PWR_MGMT_1 = 0x6B
REG_ACCEL_XOUT_H = 0x3B

ACCEL_LSB_PER_G = 16384.0     # ±2 g full scale
GYRO_LSB_PER_DPS = 131.0      # ±250 °/s full scale


def _s16(hi: int, lo: int) -> int:
    value = (hi << 8) | lo
    return value - 0x10000 if value >= 0x8000 else value


@dataclass(frozen=True)
class ImuState:
    roll: float          # degrees
    pitch: float         # degrees
    yaw: float            # degrees, cumulative since calibration (relative — no magnetometer)
    gyro: tuple[float, float, float]  # deg/s (x, y, z)
    accel: tuple[float, float, float]  # g (x, y, z)


def accel_to_roll_pitch(ax: float, ay: float, az: float) -> tuple[float, float]:
    roll = math.degrees(math.atan2(ay, az))
    pitch = math.degrees(math.atan2(-ax, math.hypot(ay, az)))
    return roll, pitch


class ComplementaryFilter:
    """Fuses gyro integration (fast, drifty) with accel tilt (slow, absolute)."""

    def __init__(self, alpha: float = 0.98):
        self.alpha = alpha
        self.roll = 0.0
        self.pitch = 0.0
        self._initialized = False

    def update(
        self,
        accel: tuple[float, float, float],
        gyro: tuple[float, float, float],
        dt: float,
    ) -> tuple[float, float]:
        acc_roll, acc_pitch = accel_to_roll_pitch(*accel)
        if not self._initialized:
            self.roll, self.pitch = acc_roll, acc_pitch
            self._initialized = True
            return self.roll, self.pitch
        self.roll = self.alpha * (self.roll + gyro[0] * dt) + (1 - self.alpha) * acc_roll
        self.pitch = self.alpha * (self.pitch + gyro[1] * dt) + (1 - self.alpha) * acc_pitch
        return self.roll, self.pitch


class Mpu6050:
    def __init__(self, bus, address: int = MPU6050_ADDRESS, clock=time.monotonic):
        self._bus = bus
        self._address = address
        self._clock = clock
        self._filter = ComplementaryFilter()
        self._gyro_bias = (0.0, 0.0, 0.0)
        self._yaw = 0.0
        self._roll_offset = 0.0
        self._pitch_offset = 0.0
        self._last_t: float | None = None
        self._bus.write_byte_data(self._address, REG_PWR_MGMT_1, 0)  # wake from sleep

    @classmethod
    def from_hardware(cls, bus_number: int = 1) -> "Mpu6050":
        from smbus2 import SMBus  # type: ignore

        return cls(SMBus(bus_number))

    def read_raw(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """Returns (accel g, gyro deg/s), bias-corrected."""
        d = self._bus.read_i2c_block_data(self._address, REG_ACCEL_XOUT_H, 14)
        accel = (
            _s16(d[0], d[1]) / ACCEL_LSB_PER_G,
            _s16(d[2], d[3]) / ACCEL_LSB_PER_G,
            _s16(d[4], d[5]) / ACCEL_LSB_PER_G,
        )
        # d[6:8] is temperature — unused.
        gyro = (
            _s16(d[8], d[9]) / GYRO_LSB_PER_DPS - self._gyro_bias[0],
            _s16(d[10], d[11]) / GYRO_LSB_PER_DPS - self._gyro_bias[1],
            _s16(d[12], d[13]) / GYRO_LSB_PER_DPS - self._gyro_bias[2],
        )
        return accel, gyro

    def calibrate_gyro(self, samples: int = 200) -> None:
        """Average gyro at rest to find bias. Robot must be still (~2 s at 100 Hz)."""
        self._gyro_bias = (0.0, 0.0, 0.0)
        self._yaw = 0.0
        self._roll_offset = 0.0
        self._pitch_offset = 0.0
        total = [0.0, 0.0, 0.0]
        for _ in range(samples):
            _, gyro = self.read_raw()
            for i in range(3):
                total[i] += gyro[i]
        self._gyro_bias = tuple(t / samples for t in total)  # type: ignore[assignment]

    def zero(self) -> None:
        """Tare: treat the current orientation as the new flat/zero reference."""
        self._roll_offset = self._filter.roll
        self._pitch_offset = self._filter.pitch
        self._yaw = 0.0

    def update(self) -> ImuState:
        now = self._clock()
        dt = (now - self._last_t) if self._last_t is not None else 0.01
        self._last_t = now
        accel, gyro = self.read_raw()
        roll, pitch = self._filter.update(accel, gyro, dt)
        self._yaw += gyro[2] * dt
        return ImuState(
            roll=roll - self._roll_offset, pitch=pitch - self._pitch_offset,
            yaw=self._yaw, gyro=gyro, accel=accel,
        )
