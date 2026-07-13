import math

from milo_bridge.drivers.imu import (
    ComplementaryFilter,
    ImuState,
    Mpu6050,
    accel_to_roll_pitch,
    _s16,
)


class FakeBus:
    """Serves canned 14-byte MPU6050 register blocks."""

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.writes = []
        self._i = 0

    def write_byte_data(self, addr, reg, value):
        self.writes.append((addr, reg, value))

    def read_i2c_block_data(self, addr, reg, length):
        block = self.blocks[min(self._i, len(self.blocks) - 1)]
        self._i += 1
        return block


def block(ax=0, ay=0, az=16384, gx=0, gy=0, gz=0):
    """Raw register block from signed 16-bit values (default: flat, still)."""
    out = []
    for v in (ax, ay, az, 0, gx, gy, gz):  # 0 = temperature
        v &= 0xFFFF
        out += [v >> 8, v & 0xFF]
    return out


def test_s16_sign_decode():
    assert _s16(0x00, 0x01) == 1
    assert _s16(0xFF, 0xFF) == -1
    assert _s16(0x80, 0x00) == -32768


def test_accel_tilt_flat_and_rolled():
    assert accel_to_roll_pitch(0, 0, 1) == (0, 0)
    roll, pitch = accel_to_roll_pitch(0, 1, 0)  # lying on its side
    assert math.isclose(roll, 90.0)
    assert math.isclose(pitch, 0.0)


def test_wakeup_written_on_init():
    bus = FakeBus([block()])
    Mpu6050(bus)
    assert (0x68, 0x6B, 0) in bus.writes


def test_read_raw_scaling():
    bus = FakeBus([block(az=16384, gx=131)])
    imu = Mpu6050(bus)
    accel, gyro = imu.read_raw()
    assert math.isclose(accel[2], 1.0)   # 1 g
    assert math.isclose(gyro[0], 1.0)    # 1 deg/s


def test_gyro_calibration_removes_bias():
    biased = block(gx=262)  # constant 2 deg/s bias
    bus = FakeBus([biased] * 300)
    imu = Mpu6050(bus)
    imu.calibrate_gyro(samples=100)
    _, gyro = imu.read_raw()
    assert abs(gyro[0]) < 1e-9


def test_complementary_filter_initializes_from_accel_then_blends():
    f = ComplementaryFilter(alpha=0.98)
    roll, pitch = f.update((0, 1, 0), (0, 0, 0), 0.01)
    assert math.isclose(roll, 90.0)  # first sample snaps to accel
    # Gyro says rolling back at -100 deg/s; accel still reads 90.
    roll2, _ = f.update((0, 1, 0), (-100, 0, 0), 0.01)
    assert roll2 < roll
    assert math.isclose(roll2, 0.98 * (90 - 1.0) + 0.02 * 90.0)


def test_update_returns_state():
    times = iter([0.0, 0.01, 0.02])
    bus = FakeBus([block()] * 5)
    imu = Mpu6050(bus, clock=lambda: next(times))
    state = imu.update()
    assert isinstance(state, ImuState)
    assert state.gyro == (0.0, 0.0, 0.0)
    assert math.isclose(state.roll, 0.0)
    assert state.accel == (0.0, 0.0, 1.0)
    assert math.isclose(state.yaw, 0.0)


def test_yaw_accumulates_from_gyro_z():
    times = iter([0.0, 0.5])
    bus = FakeBus([block(gz=131)] * 5)  # 131 raw -> 1.0 deg/s (GYRO_LSB_PER_DPS)
    imu = Mpu6050(bus, clock=lambda: next(times))
    imu.update()          # first call: dt defaults to 0.01 -> yaw = 0.01
    state = imu.update()  # second call: dt = 0.5 - 0.0 = 0.5 -> yaw = 0.01 + 0.5 = 0.51
    assert math.isclose(state.yaw, 0.51)


def test_zero_tares_current_orientation_to_flat():
    bus = FakeBus([block(ay=16384, az=0)] * 5)  # lying on its side: roll snaps to 90
    imu = Mpu6050(bus)
    state1 = imu.update()
    assert math.isclose(state1.roll, 90.0)
    imu.zero()
    state2 = imu.update()  # same physical orientation, now reported as flat
    assert math.isclose(state2.roll, 0.0, abs_tol=1e-9)


def test_zero_resets_yaw():
    times = iter([0.0, 0.5, 1.0])
    bus = FakeBus([block(gz=131)] * 5)  # spinning at 1.0 deg/s
    imu = Mpu6050(bus, clock=lambda: next(times))
    imu.update()  # dt=0.01 -> yaw=0.01
    imu.update()  # dt=0.5  -> yaw=0.51
    imu.zero()    # yaw -> 0
    state = imu.update()  # dt=0.5 -> yaw=0.5 (only this update's contribution)
    assert math.isclose(state.yaw, 0.5)
    assert math.isclose(state.roll, 0.0)
    assert math.isclose(state.pitch, 0.0)
