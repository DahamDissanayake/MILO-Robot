"""PCA9685 servo driver.

Carries over two hard-won lessons from the Sesame ESP32 firmware:
per-servo calibrated pulse ranges (calibrate without disassembly) and
staggered multi-servo writes (simultaneous starts on 8 MG90s brown out
the rail). A third safeguard was added on the Pi: every write is clamped a
few degrees short of each mechanical stop (SAFE_ANGLE_MIN/SAFE_ANGLE_MAX)
so a commanded extreme can never stall a servo -- a stalled servo grinds at
full current, sagging the shared rail and twitching every other servo.

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
DEFAULT_PULSE_RANGE = (PULSE_MIN_US, PULSE_MAX_US)

# Never command the last few degrees at either end. A servo driven into its
# mechanical hard-stop can't reach the requested position, so its controller
# grinds the motor at full power against the wall -- a stall that pulls ~5x
# normal current, sags the shared servo rail, and makes every other servo
# twitch. Clamping every hardware write into this band keeps 0deg/180deg
# commands off the wall (they drive the safe near-extreme instead).
SAFE_ANGLE_MIN = 5.0
SAFE_ANGLE_MAX = 175.0

# Channel map inherited from the Sesame firmware (movement-sequences.h).
SERVO_CHANNELS: dict[str, int] = {
    "R1": 0, "R2": 1, "L1": 2, "L2": 3,
    "R4": 4, "R3": 5, "L3": 6, "L4": 7,
}
SERVO_NAMES = tuple(SERVO_CHANNELS)
NUM_SERVOS = len(SERVO_CHANNELS)


def angle_to_pulse_us(angle: float, min_us: float = PULSE_MIN_US, max_us: float = PULSE_MAX_US) -> float:
    angle = min(max(angle, 0.0), 180.0)
    return min_us + (angle / 180.0) * (max_us - min_us)


def pulse_us_to_duty(pulse_us: float, freq_hz: int = PWM_FREQUENCY_HZ) -> int:
    """16-bit duty-cycle value as used by adafruit-circuitpython-pca9685."""
    period_us = 1_000_000 / freq_hz
    return round(pulse_us / period_us * 0xFFFF)


class ServoDriver:
    """8-servo driver with per-channel pulse-range calibration and staggered writes.

    ``pca`` must expose ``channels[i].duty_cycle`` (the Adafruit PCA9685 API).
    Each channel has its own ``(min_us, max_us)`` pulse range so 0-180deg maps
    onto that channel's calibrated span with no additive-offset-then-clamp step
    that can strand the endpoints. Every write is then clamped into the
    ``[SAFE_ANGLE_MIN, SAFE_ANGLE_MAX]`` band before it reaches the hardware, so
    a commanded 0deg/180deg drives the servo to its safe near-extreme rather
    than grinding into the mechanical wall. ``last_angle`` still reports the
    logical commanded angle -- the slew layer above reasons in full 0-180 space.
    """

    def __init__(
        self,
        pca,
        pulse_ranges: Iterable[tuple[int, int]] = (DEFAULT_PULSE_RANGE,) * NUM_SERVOS,
        stagger_ms: int = 20,
        sleep=asyncio.sleep,
    ):
        self._pca = pca
        self.pulse_ranges = list(pulse_ranges)
        if len(self.pulse_ranges) != NUM_SERVOS:
            raise ValueError(f"need {NUM_SERVOS} pulse ranges, got {len(self.pulse_ranges)}")
        self.stagger_ms = stagger_ms
        self._sleep = sleep
        self._last_angles: list[float | None] = [None] * NUM_SERVOS

    @classmethod
    def from_hardware(
        cls,
        pulse_ranges: Iterable[tuple[int, int]] = (DEFAULT_PULSE_RANGE,) * NUM_SERVOS,
        stagger_ms: int = 20,
    ):
        import board  # type: ignore
        import busio  # type: ignore
        from adafruit_pca9685 import PCA9685  # type: ignore

        i2c = busio.I2C(board.SCL, board.SDA)
        pca = PCA9685(i2c, address=PCA9685_ADDRESS)
        pca.frequency = PWM_FREQUENCY_HZ
        return cls(pca, pulse_ranges=pulse_ranges, stagger_ms=stagger_ms)

    def _write(self, channel: int, angle: float) -> None:
        # Clamp the *pulse* into the safe band so a commanded extreme can't
        # stall the servo against its hard-stop; keep last_angle in full
        # 0-180 logical space for the slew layer above.
        min_us, max_us = self.pulse_ranges[channel]
        safe = min(max(angle, SAFE_ANGLE_MIN), SAFE_ANGLE_MAX)
        self._pca.channels[channel].duty_cycle = pulse_us_to_duty(angle_to_pulse_us(safe, min_us, max_us))
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
