# Central Motion Controller, Balance Modes & Servo Calibration Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the servo-slider calibration bug, make all robot motion interpolate smoothly, and give the robot Raw/Balanced/Angled motion modes with IMU-driven self-leveling, shared identically by the web UI and the brain.

**Architecture:** `ServoDriver` gains per-channel calibrated pulse ranges (fixes the 0°/180° bug). A new `SmoothServos` wrapper sits between every caller and `ServoDriver`, recording targets and slewing toward them on its own periodic tick instead of snapping instantly. `GaitEngine` — already the single chokepoint both the web app and the brain call through — grows `mode`/`reset()`/`standby()` and, via a new pure `BalanceCorrector`, applies IMU-fed proportional roll/pitch trim on top of the CPG/policy gait output whenever mode is Balanced or Angled. The web layer exposes `mode`/`reset`/`standby` WS messages and relocates/extends the Move panel.

**Tech Stack:** Python 3 (asyncio, aiohttp, numpy), pytest (+pytest-asyncio, injected fakes, no real hardware), vanilla JS (no build step), aiohttp `TestClient`/`ws_connect` for WS integration tests.

## Global Constraints

- All new/changed Python code must be testable off-hardware with injected fakes — no real I2C/PCA9685/MPU6050 in tests (matches every existing driver test in this repo).
- Every existing test in `bridge/tests/` must keep passing after each task; run `python -m pytest bridge/tests` (from repo root, or `pytest` from `bridge/`) at the end of every task.
- Default behavior when a feature isn't explicitly opted into must be unchanged: `mode` defaults to `"raw"`, which must reproduce today's exact gait/servo behavior bit-for-bit.
- No servo angle written to hardware may fall outside `[0, 180]` at any point in this plan.
- Follow existing code style exactly: relative imports (`from ..poses import ...`), dataclasses for config/state, injected `clock`/`sleep` for testability, `from __future__ import annotations` at the top of every module.
- Commit after each task with a plain, present-tense message; do not add a Claude co-author trailer ([[no-coauthor-trailer]] memory).

---

## Task 1: Servo pulse-range calibration

**Files:**
- Modify: `bridge/milo_bridge/drivers/servos.py`
- Test: `bridge/tests/test_servos.py`

**Interfaces:**
- Produces: `angle_to_pulse_us(angle: float, min_us: float = PULSE_MIN_US, max_us: float = PULSE_MAX_US) -> float`; `ServoDriver(pca, pulse_ranges: Iterable[tuple[int,int]] = ((500,2500),)*8, stagger_ms=20, sleep=asyncio.sleep)`; `ServoDriver.pulse_ranges: list[tuple[int,int]]` (replaces `.trims`); `ServoDriver.from_hardware(pulse_ranges=..., stagger_ms=20)`; `DEFAULT_PULSE_RANGE: tuple[int,int]` constant `(500, 2500)`.

- [ ] **Step 1: Write the failing tests**

Open `bridge/tests/test_servos.py`. Replace `test_trim_is_applied_and_clamped` (currently lines 48-55) with:

```python
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
```

Replace `test_wrong_trim_count_rejected` (currently lines 71-73) with:

```python
def test_wrong_pulse_range_count_rejected():
    with pytest.raises(ValueError):
        ServoDriver(FakePca(), pulse_ranges=[(500, 2500), (500, 2500)])
```

Leave every other test in the file untouched (`test_angle_to_pulse_endpoints`, `test_pulse_to_duty_16bit`, `test_channel_map_matches_firmware`, `test_set_angle_by_name_and_channel`, `test_set_pose_staggers_between_writes`, `test_relax_zeroes_all_channels`) — they exercise behavior that isn't changing.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `pytest bridge/tests/test_servos.py -v`
Expected: `test_angle_to_pulse_custom_range`, `test_calibrated_range_hits_true_endpoints`, `test_uncalibrated_channel_still_hits_default_endpoints`, `test_wrong_pulse_range_count_rejected` FAIL (`angle_to_pulse_us() got an unexpected keyword argument 'min_us'` / `ServoDriver.__init__() got an unexpected keyword argument 'pulse_ranges'`); the rest still PASS.

- [ ] **Step 3: Rewrite `servos.py` with per-channel pulse ranges**

Replace the full contents of `bridge/milo_bridge/drivers/servos.py` with:

```python
"""PCA9685 servo driver.

Carries over two hard-won lessons from the Sesame ESP32 firmware:
per-servo calibrated pulse ranges (calibrate without disassembly) and
staggered multi-servo writes (simultaneous starts on 8 MG90s brown out
the rail).

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
    Each channel's own ``(min_us, max_us)`` pulse range means 0deg and 180deg
    always drive that channel's calibrated physical extreme -- there's no
    additive-offset-then-clamp step that can strand the endpoints.
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
        min_us, max_us = self.pulse_ranges[channel]
        duty = pulse_us_to_duty(angle_to_pulse_us(angle, min_us, max_us))
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_servos.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/drivers/servos.py bridge/tests/test_servos.py
git commit -m "fix(bridge): calibrate servo pulse range per channel so 0/180 hit true endpoints"
```

---

## Task 2: Config field rename + wiring

**Files:**
- Modify: `bridge/milo_bridge/config.py`
- Modify: `bridge/milo_bridge/main.py:50`
- Modify: `bridge/milo_bridge/cli.py:26`
- Test: `bridge/tests/test_config.py`

**Interfaces:**
- Consumes: `ServoDriver.from_hardware(pulse_ranges=..., stagger_ms=...)` (Task 1).
- Produces: `BridgeConfig.servo_pulse_ranges: list[tuple[int,int]]` (replaces `servo_trims`), default `[(500, 2500)] * 8`.

- [ ] **Step 1: Write the failing test**

Append to `bridge/tests/test_config.py`:

```python
def test_servo_pulse_ranges_round_trip_through_json(tmp_path):
    path = tmp_path / "config.json"
    cfg = BridgeConfig.load(path)
    assert cfg.servo_pulse_ranges == [(500, 2500)] * 8
    cfg.save(path)
    cfg2 = BridgeConfig.load(path)
    assert cfg2.servo_pulse_ranges == [(500, 2500)] * 8
    assert all(isinstance(r, tuple) for r in cfg2.servo_pulse_ranges)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest bridge/tests/test_config.py -v`
Expected: FAIL with `AttributeError: 'BridgeConfig' object has no attribute 'servo_pulse_ranges'`.

- [ ] **Step 3: Rename the config field**

In `bridge/milo_bridge/config.py`, replace:

```python
    # Servo tuning (per-channel trim degrees, firmware-style subtrim)
    servo_trims: list[int] = field(default_factory=lambda: [0] * 8)
    servo_stagger_ms: int = 20
```

with:

```python
    # Servo tuning (per-channel calibrated pulse range, microseconds)
    servo_pulse_ranges: list[tuple[int, int]] = field(
        default_factory=lambda: [(500, 2500)] * 8
    )
    servo_stagger_ms: int = 20
```

Then replace the `save()` method's body:

```python
    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["video_size"] = list(self.video_size)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

with:

```python
    def save(self, path: Path | None = None) -> None:
        path = path or DEFAULT_DIR / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["video_size"] = list(self.video_size)
        data["servo_pulse_ranges"] = [list(r) for r in self.servo_pulse_ranges]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
```

And replace `__post_init__`:

```python
    def __post_init__(self) -> None:
        self.video_size = tuple(self.video_size)  # JSON round-trips tuples as lists
```

with:

```python
    def __post_init__(self) -> None:
        self.video_size = tuple(self.video_size)  # JSON round-trips tuples as lists
        self.servo_pulse_ranges = [tuple(r) for r in self.servo_pulse_ranges]
```

- [ ] **Step 4: Update the two construction call sites**

In `bridge/milo_bridge/main.py`, replace line 50:

```python
    servos = ServoDriver.from_hardware(trims=cfg.servo_trims, stagger_ms=cfg.servo_stagger_ms)
```

with:

```python
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
```

In `bridge/milo_bridge/cli.py`, replace line 26:

```python
    servos = ServoDriver.from_hardware(trims=cfg.servo_trims, stagger_ms=cfg.servo_stagger_ms)
```

with:

```python
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/test_config.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS (confirms nothing else referenced `servo_trims`).

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/config.py bridge/milo_bridge/main.py bridge/milo_bridge/cli.py bridge/tests/test_config.py
git commit -m "refactor(bridge): rename servo_trims config to servo_pulse_ranges"
```

---

## Task 3: `SmoothServos` interpolation layer

**Files:**
- Create: `bridge/milo_bridge/drivers/smooth_servos.py`
- Test: `bridge/tests/test_smooth_servos.py`
- Modify: `bridge/milo_bridge/main.py`

**Interfaces:**
- Consumes: `ServoDriver` interface (`set_angle`, `last_angle`, `relax`) from Task 1.
- Produces: `SmoothServos(servos, slew_deg_per_s=300.0, stagger_ms=20, sleep=asyncio.sleep, clock=time.monotonic)` with `set_angle(servo, angle) -> None`, `async set_pose(angles, stagger=True) -> None`, `last_angle(servo) -> float | None`, `relax() -> None`, `tick() -> None`, `async run() -> None`, `start() -> None`, `stop() -> None`. Every later task that touches servos (`PoseRunner`, `GaitEngine`, `SleepController`, `WebDeps.servos`) receives a `SmoothServos` instance instead of a raw `ServoDriver`.

- [ ] **Step 1: Write the failing tests**

Create `bridge/tests/test_smooth_servos.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_smooth_servos.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.drivers.smooth_servos'`.

- [ ] **Step 3: Create `smooth_servos.py`**

Create `bridge/milo_bridge/drivers/smooth_servos.py`:

```python
"""Smooth interpolation layer over ServoDriver.

Every existing caller (poses, gait, manual servo commands) currently
snaps a servo straight to its commanded angle -- fine for a single value,
jarring as motion. SmoothServos exposes the same interface as ServoDriver
(set_angle/set_pose/last_angle/relax) so it's a drop-in replacement, but
set_angle/set_pose only record a *target*; a separate periodic tick()
steps every channel toward its target at a bounded slew rate and performs
the actual hardware write. This lets both GaitEngine (which already calls
set_angle every ~20ms itself) and PoseRunner (one-shot calls that rely on
a subsequent `wait_ms` sleep to give the move time to land) share one
mechanism without either blocking the other -- a version that
blocked-and-ramped inside the call itself would stall GaitEngine's own
50Hz loop.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping

DEFAULT_SLEW_DEG_PER_S = 300.0
TICK_HZ = 50


class SmoothServos:
    def __init__(
        self,
        servos,
        slew_deg_per_s: float = DEFAULT_SLEW_DEG_PER_S,
        stagger_ms: int = 20,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ):
        self._servos = servos
        self._slew = slew_deg_per_s
        self._stagger_s = stagger_ms / 1000
        self._sleep = sleep
        self._clock = clock
        self._targets: dict[str, float] = {}
        self._last_t: float | None = None
        self._task: asyncio.Task | None = None

    def set_angle(self, servo: str, angle: float) -> None:
        self._targets[servo] = angle

    async def set_pose(self, angles: Mapping[str, float], stagger: bool = True) -> None:
        """Record a target per channel, staggering *when* each target starts
        moving (not the write itself, which now happens on tick()) so all
        servos don't begin slewing in the same instant."""
        for i, (name, angle) in enumerate(angles.items()):
            self.set_angle(name, angle)
            if stagger and self._stagger_s and i < len(angles) - 1:
                await self._sleep(self._stagger_s)

    def last_angle(self, servo):
        return self._servos.last_angle(servo)

    def relax(self) -> None:
        self._targets.clear()
        self._servos.relax()

    def tick(self) -> None:
        now = self._clock()
        dt = (now - self._last_t) if self._last_t is not None else 1.0 / TICK_HZ
        self._last_t = now
        max_step = self._slew * dt
        for name, target in self._targets.items():
            current = self._servos.last_angle(name)
            if current is None:
                self._servos.set_angle(name, target)
                continue
            delta = target - current
            if abs(delta) <= max_step:
                if delta != 0:
                    self._servos.set_angle(name, target)
            else:
                self._servos.set_angle(name, current + max_step * (1 if delta > 0 else -1))

    async def run(self) -> None:
        interval = 1.0 / TICK_HZ
        while True:
            started = self._clock()
            self.tick()
            elapsed = self._clock() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.ensure_future(self.run())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_smooth_servos.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Wire `SmoothServos` into `main.py`**

In `bridge/milo_bridge/main.py`, add the import alongside the other driver imports:

```python
from .drivers.servos import ServoDriver
```

becomes:

```python
from .drivers.servos import ServoDriver
from .drivers.smooth_servos import SmoothServos
```

Replace:

```python
    # Required hardware.
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
    display = FaceDisplay.from_hardware(ASSETS_DIR)
    runner = PoseRunner(servos, display)
```

with:

```python
    # Required hardware.
    servos = ServoDriver.from_hardware(pulse_ranges=cfg.servo_pulse_ranges, stagger_ms=cfg.servo_stagger_ms)
    motion_servos = SmoothServos(servos, stagger_ms=cfg.servo_stagger_ms)
    motion_servos.start()
    display = FaceDisplay.from_hardware(ASSETS_DIR)
    runner = PoseRunner(motion_servos, display)
```

Replace:

```python
    gait = GaitEngine(servos, imu=imu, policy_path=POLICY_PATH)
```

with:

```python
    gait = GaitEngine(motion_servos, imu=imu, policy_path=POLICY_PATH)
```

Replace:

```python
    sleep_controller = SleepController(
        runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=servos
    )
```

with:

```python
    sleep_controller = SleepController(
        runner, display, loud_rms_threshold=cfg.loud_rms_threshold, servos=motion_servos
    )
```

Replace:

```python
    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
```

with:

```python
    web_deps = WebDeps(
        config=cfg, runner=runner, display=display, servos=motion_servos,
        camera=camera, audio=audio, imu=imu, gait=gait,
```

Replace:

```python
    manager = SessionManager(
        cfg,
        servos=servos,
        display=display,
        runner=runner,
        audio=audio,
        graph_api=graph_api,
        gait=gait,
        media_hub=hub,
        broker=broker,
        sleep_controller=sleep_controller,
    )
```

with:

```python
    manager = SessionManager(
        cfg,
        servos=motion_servos,
        display=display,
        runner=runner,
        audio=audio,
        graph_api=graph_api,
        gait=gait,
        media_hub=hub,
        broker=broker,
        sleep_controller=sleep_controller,
    )
```

Finally, replace the shutdown block:

```python
    finally:
        gait_task.cancel()
        backup_task.cancel()
        if web_task is not None:
            web_task.cancel()
        graph.close()
```

with:

```python
    finally:
        gait_task.cancel()
        backup_task.cancel()
        motion_servos.stop()
        if web_task is not None:
            web_task.cancel()
        graph.close()
```

- [ ] **Step 6: Run the full suite to confirm nothing broke**

Run: `pytest bridge/tests -v`
Expected: all tests PASS (`main.py` isn't unit-tested directly, but nothing it imports/constructs should have changed observable behavior for any existing test).

- [ ] **Step 7: Commit**

```bash
git add bridge/milo_bridge/drivers/smooth_servos.py bridge/tests/test_smooth_servos.py bridge/milo_bridge/main.py
git commit -m "feat(bridge): add SmoothServos slew-limited interpolation layer"
```

---

## Task 4: `PoseRunner.is_running`

**Files:**
- Modify: `bridge/milo_bridge/poses.py`
- Test: `bridge/tests/test_poses.py`

**Interfaces:**
- Produces: `PoseRunner.is_running: bool` — `True` for the duration of a `run()` call (including its cycles and recovery-stand write), `False` before/after/on abort, set via `try/finally` so it resets even if the task is cancelled.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/test_poses.py`:

```python
def test_is_running_false_before_and_after_a_run():
    servos, display = FakeServos(), FakeDisplay()
    runner = PoseRunner(servos, display, sleep=no_sleep)
    assert runner.is_running is False
    completed = asyncio.run(runner.run("stand"))
    assert completed
    assert runner.is_running is False


def test_is_running_true_while_a_cycle_is_mid_flight():
    servos, display = FakeServos(), FakeDisplay()

    async def yielding_sleep(_s):
        await asyncio.sleep(0)

    runner = PoseRunner(servos, display, sleep=yielding_sleep)

    async def run():
        task = asyncio.create_task(runner.run("walk", cycles=10_000))
        await asyncio.sleep(0)
        assert runner.is_running is True
        runner.abort()
        await task
        assert runner.is_running is False

    asyncio.run(run())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: FAIL with `AttributeError: 'PoseRunner' object has no attribute 'is_running'`.

- [ ] **Step 3: Add `is_running` to `PoseRunner`**

In `bridge/milo_bridge/poses.py`, replace:

```python
class PoseRunner:
    """Executes poses on the servo driver, with the matching face and
    firmware-style interruptibility (abort mid-gait -> stand)."""

    def __init__(self, servos, display=None, sleep=asyncio.sleep):
        self._servos = servos
        self._display = display
        self._sleep = sleep
        self._abort = asyncio.Event()

    def abort(self) -> None:
        self._abort.set()

    async def run(self, name: str, cycles: int = DEFAULT_WALK_CYCLES) -> bool:
        """Run a pose to completion. Returns False if aborted early."""
        pose = POSES[name]
        self._abort.clear()
        if self._display is not None:
            await self._display.set_face(pose.face, pose.face_mode)

        completed = await self._run_steps(pose.steps)
        if completed and pose.cycle:
            for _ in range(cycles):
                if not await self._run_steps(pose.cycle):
                    completed = False
                    break
        if pose.end_stand or pose.cycle or not completed:
            # Recovery stand must run even after an abort (firmware behavior:
            # releasing the button mid-walk always ends in runStandPose).
            await self._servos.set_pose(STAND_ANGLES)
            if self._display is not None and completed:
                self._display.start_idle()
        return completed
```

with:

```python
class PoseRunner:
    """Executes poses on the servo driver, with the matching face and
    firmware-style interruptibility (abort mid-gait -> stand)."""

    def __init__(self, servos, display=None, sleep=asyncio.sleep):
        self._servos = servos
        self._display = display
        self._sleep = sleep
        self._abort = asyncio.Event()
        self.is_running = False

    def abort(self) -> None:
        self._abort.set()

    async def run(self, name: str, cycles: int = DEFAULT_WALK_CYCLES) -> bool:
        """Run a pose to completion. Returns False if aborted early."""
        pose = POSES[name]
        self._abort.clear()
        self.is_running = True
        try:
            if self._display is not None:
                await self._display.set_face(pose.face, pose.face_mode)

            completed = await self._run_steps(pose.steps)
            if completed and pose.cycle:
                for _ in range(cycles):
                    if not await self._run_steps(pose.cycle):
                        completed = False
                        break
            if pose.end_stand or pose.cycle or not completed:
                # Recovery stand must run even after an abort (firmware behavior:
                # releasing the button mid-walk always ends in runStandPose).
                await self._servos.set_pose(STAND_ANGLES)
                if self._display is not None and completed:
                    self._display.start_idle()
            return completed
        finally:
            self.is_running = False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_poses.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/poses.py bridge/tests/test_poses.py
git commit -m "feat(bridge): add PoseRunner.is_running so other writers can defer to it"
```

---

## Task 5: `BalanceCorrector`

**Files:**
- Create: `bridge/milo_bridge/gait/balance.py`
- Test: `bridge/tests/test_balance.py`

**Interfaces:**
- Consumes: `LEGS` mapping from `bridge/milo_bridge/gait/cpg.py` (leg name -> `(hip_servo, knee_servo, phase_offset, mirror)`).
- Produces: `PARAMS: dict[str, BalanceParams]` with keys `"balanced"`, `"angled"` (each a `BalanceParams(roll_kp, pitch_kp, max_correction_deg)`); `correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]` — pure, does not mutate `angles`, returns `angles` unchanged for any `mode` not in `PARAMS` (i.e. `"raw"`).

- [ ] **Step 1: Write the failing tests**

Create `bridge/tests/test_balance.py`:

```python
"""Off-hardware tests for BalanceCorrector: pure roll/pitch -> hip-angle
trim, no hardware dependency."""
from milo_bridge.gait.balance import PARAMS, correct
from milo_bridge.gait.cpg import GAIT_NEUTRAL


def test_raw_mode_returns_angles_unchanged():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=20.0, pitch_deg=10.0, mode="raw")
    assert result == angles


def test_zero_tilt_leaves_angles_unchanged():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=0.0, mode="balanced")
    assert result == angles


def test_roll_correction_opposes_left_and_right_hips():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=15.0, pitch_deg=0.0, mode="balanced")
    left_delta = result["L1"] - angles["L1"]
    right_delta = result["R1"] - angles["R1"]
    assert left_delta != 0
    assert right_delta != 0
    assert (left_delta > 0) != (right_delta > 0)  # opposite directions


def test_pitch_correction_opposes_front_and_rear_hips():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=0.0, pitch_deg=15.0, mode="balanced")
    front_delta = result["L1"] - angles["L1"]  # FL
    rear_delta = result["L3"] - angles["L3"]  # RL
    assert front_delta != 0
    assert rear_delta != 0
    assert (front_delta > 0) != (rear_delta > 0)


def test_correction_clamped_to_mode_max():
    angles = dict(GAIT_NEUTRAL)
    huge = correct(angles, roll_deg=500.0, pitch_deg=0.0, mode="balanced")
    max_c = PARAMS["balanced"].max_correction_deg
    for hip in ("L1", "R1", "L3", "R3"):
        assert abs(huge[hip] - angles[hip]) <= max_c + 1e-6


def test_angled_mode_allows_larger_pitch_correction_than_balanced():
    angles = dict(GAIT_NEUTRAL)
    balanced = correct(angles, roll_deg=0.0, pitch_deg=90.0, mode="balanced")
    angled = correct(angles, roll_deg=0.0, pitch_deg=90.0, mode="angled")
    b_delta = abs(balanced["L1"] - angles["L1"])
    a_delta = abs(angled["L1"] - angles["L1"])
    assert a_delta > b_delta


def test_result_angles_stay_within_servo_range():
    angles = dict(GAIT_NEUTRAL)
    result = correct(angles, roll_deg=999.0, pitch_deg=-999.0, mode="angled")
    assert all(0.0 <= a <= 180.0 for a in result.values())


def test_does_not_mutate_input_dict():
    angles = dict(GAIT_NEUTRAL)
    original = dict(angles)
    correct(angles, roll_deg=10.0, pitch_deg=10.0, mode="balanced")
    assert angles == original
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/test_balance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'milo_bridge.gait.balance'`.

- [ ] **Step 3: Create `balance.py`**

Create `bridge/milo_bridge/gait/balance.py`:

```python
"""IMU-fed proportional balance correction, layered on top of whatever the
CPG/policy gait backend already computed for this tick.

Not full inverse kinematics -- a lightweight trim: roll error nudges left
vs right hip angles in opposite directions, pitch error nudges front vs
rear hip angles in opposite directions, both clamped to a per-mode
maximum. Angled (climb) mode reuses the exact same math with a wider
pitch authority so it can hold the body level against a real incline, not
just a walking wobble.

Which absolute direction actually counters a given tilt is a hardware
question this can't answer off-robot -- it only guarantees left/right and
front/rear hips are corrected in *opposite* directions from each other.
Flip the sign of roll_kp/pitch_kp in PARAMS below if it leans the wrong
way on the real robot.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cpg import LEGS


@dataclass(frozen=True)
class BalanceParams:
    roll_kp: float
    pitch_kp: float
    max_correction_deg: float


PARAMS: dict[str, BalanceParams] = {
    "balanced": BalanceParams(roll_kp=0.3, pitch_kp=0.3, max_correction_deg=12.0),
    "angled": BalanceParams(roll_kp=0.25, pitch_kp=0.25, max_correction_deg=30.0),
}


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def correct(angles: dict[str, float], roll_deg: float, pitch_deg: float, mode: str) -> dict[str, float]:
    """Apply IMU-fed roll/pitch trim to ``angles`` (a full hip+knee angle
    dict as produced by CpgGait.angles_at / OnnxPolicy.step). Returns a new
    dict; ``angles`` is never mutated. ``mode="raw"`` (or any mode without
    tuned params) returns ``angles`` unchanged."""
    if mode not in PARAMS:
        return angles
    params = PARAMS[mode]
    roll_correction = _clamp(params.roll_kp * roll_deg, params.max_correction_deg)
    pitch_correction = _clamp(params.pitch_kp * pitch_deg, params.max_correction_deg)

    corrected = dict(angles)
    for leg, (hip, *_rest) in LEGS.items():
        if hip not in corrected:
            continue
        side = 1.0 if leg[1] == "L" else -1.0  # opposite sign per side
        front = 1.0 if leg[0] == "F" else -1.0  # opposite sign front vs rear
        delta = side * roll_correction + front * pitch_correction
        corrected[hip] = max(0.0, min(180.0, corrected[hip] + delta))
    return corrected
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/test_balance.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/gait/balance.py bridge/tests/test_balance.py
git commit -m "feat(bridge): add BalanceCorrector, a pure IMU roll/pitch hip-angle trim"
```

---

## Task 6: `GaitEngine` becomes the mode/balance/reset/standby coordinator

**Files:**
- Modify: `bridge/milo_bridge/gait/engine.py`
- Modify: `bridge/milo_bridge/main.py`
- Test: `bridge/tests/test_gait.py`

**Interfaces:**
- Consumes: `balance.correct` and `balance.PARAMS` (Task 5); `REST_ANGLES`/`STAND_ANGLES` from `bridge/milo_bridge/poses.py`; an optional `runner` object exposing `.is_running: bool` (Task 4, `PoseRunner`).
- Produces: `MODES: tuple[str, ...]` = `("raw", "balanced", "angled")`; `GaitEngine(servos, imu=None, runner=None, policy_path=None, rate_hz=50, clock=time.monotonic)`; `GaitEngine.mode: str` (property, default `"raw"`); `GaitEngine.set_mode(name: str) -> None` (raises `ValueError` on an unknown name); `GaitEngine.reset() -> None`; `GaitEngine.standby() -> None`. `set_velocity_command` and `tick` keep their existing signatures/return types.

- [ ] **Step 1: Write the failing tests**

Add these imports to the top of `bridge/tests/test_gait.py` (alongside the existing ones):

```python
from milo_bridge.drivers.imu import ImuState
from milo_bridge.poses import REST_ANGLES
```

Append to `bridge/tests/test_gait.py`:

```python
# --- mode / reset / standby --------------------------------------------------

class FakeImu:
    def __init__(self, roll=0.0, pitch=0.0):
        self.roll = roll
        self.pitch = pitch

    def update(self):
        return ImuState(roll=self.roll, pitch=self.pitch, yaw=0.0, gyro=(0.0, 0.0, 0.0), accel=(0.0, 0.0, 1.0))


class FakeRunner:
    def __init__(self):
        self.is_running = False


def test_mode_defaults_to_raw_and_validates():
    engine = GaitEngine(FakeServos())
    assert engine.mode == "raw"
    engine.set_mode("balanced")
    assert engine.mode == "balanced"
    with pytest.raises(ValueError):
        engine.set_mode("sideways")


def test_reset_writes_rest_angles_and_stops_active_gait():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    engine.reset()
    assert servos.angles == REST_ANGLES
    assert engine.tick() is None  # gait command was cleared, not just paused


def test_standby_writes_stand_angles():
    servos = FakeServos()
    engine = GaitEngine(servos, clock=lambda: 0.0)
    engine.standby()
    assert servos.angles == STAND_ANGLES


def test_auto_standby_on_stop_in_balanced_mode_only():
    servos_balanced = FakeServos()
    engine_balanced = GaitEngine(servos_balanced, clock=lambda: 0.0)
    engine_balanced.set_mode("balanced")
    engine_balanced.set_velocity_command(0.1, 0.0, 0.0)
    servos_balanced.angles = {}  # isolate the stop's effect from the walk-start write
    engine_balanced.set_velocity_command(0.0, 0.0, 0.0)
    assert servos_balanced.angles == STAND_ANGLES

    servos_raw = FakeServos()
    engine_raw = GaitEngine(servos_raw, clock=lambda: 0.0)
    engine_raw.set_velocity_command(0.1, 0.0, 0.0)
    servos_raw.angles = {}
    engine_raw.set_velocity_command(0.0, 0.0, 0.0)
    assert servos_raw.angles == {}  # raw mode: no auto-standby


# --- deference to a running pose ----------------------------------------------

def test_tick_defers_to_a_running_pose():
    servos = FakeServos()
    runner = FakeRunner()
    engine = GaitEngine(servos, runner=runner, clock=lambda: 0.0)
    engine.set_velocity_command(0.1, 0.0, 0.0)
    runner.is_running = True
    assert engine.tick() is None
    assert servos.writes == 0


# --- balance integration -------------------------------------------------------

def test_balanced_mode_applies_imu_correction_while_walking():
    now = {"t": 0.0}
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: now["t"])
    engine.set_mode("balanced")
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 0.15
    raw_cpg = CpgGait().angles_at(0.15, 0.1, 0.0, 0.0)
    written = engine.tick()
    assert written["L1"] != pytest.approx(raw_cpg["L1"])  # balance nudged it


def test_raw_mode_ignores_imu_even_with_tilt():
    now = {"t": 0.0}
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: now["t"])
    engine.set_velocity_command(0.1, 0.0, 0.0)
    now["t"] = 0.15
    written = engine.tick()
    expected = CpgGait().angles_at(0.15, 0.1, 0.0, 0.0)
    assert written == expected


def test_balanced_mode_self_levels_at_standstill():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    engine.set_mode("balanced")
    written = engine.tick()
    assert written is not None
    assert written["L1"] != pytest.approx(STAND_ANGLES["L1"])


def test_raw_mode_idle_does_not_self_level():
    servos = FakeServos()
    imu = FakeImu(roll=20.0, pitch=0.0)
    engine = GaitEngine(servos, imu=imu, clock=lambda: 0.0)
    assert engine.tick() is None
    assert servos.writes == 0
```

- [ ] **Step 2: Run tests to verify the new ones fail and the old ones still pass**

Run: `pytest bridge/tests/test_gait.py -v`
Expected: the new tests FAIL (`AttributeError: 'GaitEngine' object has no attribute 'mode'` etc.); `test_engine_idle_until_commanded`, `test_engine_walks_on_command_and_stops`, `test_engine_missing_policy_file_falls_back` (and all CPG/policy tests) still PASS.

- [ ] **Step 3: Rewrite `gait/engine.py`**

Replace the full contents of `bridge/milo_bridge/gait/engine.py` with:

```python
"""The 50 Hz gait control loop.

One interface for all callers -- ``set_velocity_command(vx, vy, yaw_rate)``
-- with two backends: the ONNX RL policy (primary) and the CPG trot
(fallback). Zero command -> hold stand and stop writing servos (lets
scripted poses run), except in balanced/angled mode, which keeps
self-leveling at a standstill.

This is also the robot's mode/reset/standby coordinator: both the web app
and the brain call the same GaitEngine instance, so ``set_mode``/``reset``/
``standby`` apply identically no matter who's driving.
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import numpy as np

from ..poses import REST_ANGLES, STAND_ANGLES
from . import balance
from .cpg import CpgGait
from .policy import SERVO_ORDER, OnnxPolicy

log = logging.getLogger(__name__)

RATE_HZ = 50
MODES = ("raw", *balance.PARAMS)
_BALANCE_MODES = tuple(balance.PARAMS)


class GaitEngine:
    def __init__(
        self,
        servos,
        imu=None,
        runner=None,
        policy_path: Path | str | None = None,
        rate_hz: int = RATE_HZ,
        clock=time.monotonic,
    ):
        self._servos = servos
        self._imu = imu
        self._runner = runner
        self._cpg = CpgGait()
        self._policy: OnnxPolicy | None = None
        if policy_path is not None and Path(policy_path).exists():
            try:
                self._policy = OnnxPolicy(policy_path)
                log.info("gait policy loaded from %s", policy_path)
            except Exception as exc:
                log.warning("policy load failed (%s); CPG fallback active", exc)
        self._rate_hz = rate_hz
        self._clock = clock
        self._command = (0.0, 0.0, 0.0)
        self._active = False
        self._mode = "raw"
        self._t0 = clock()

    @property
    def backend(self) -> str:
        return "policy" if self._policy is not None else "cpg"

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, name: str) -> None:
        if name not in MODES:
            raise ValueError(f"unknown mode {name!r}")
        self._mode = name

    def set_velocity_command(self, vx: float, vy: float, yaw_rate: float) -> None:
        """vx/vy in m/s, yaw_rate in deg/s. (0,0,0) stops walking."""
        was_active = self._active
        self._command = (vx, vy, yaw_rate)
        self._active = any(abs(c) > 1e-6 for c in self._command)
        if self._active and not was_active:
            self._t0 = self._clock()  # restart the CPG cycle cleanly
        elif was_active and not self._active and self._mode in _BALANCE_MODES:
            self.standby()

    def reset(self) -> None:
        """Smoothly return every servo to the 90-degree rest angles."""
        self._set_discrete_target(REST_ANGLES)

    def standby(self) -> None:
        """Smoothly return every servo to the stand pose."""
        self._set_discrete_target(STAND_ANGLES)

    def _set_discrete_target(self, angles: dict[str, float]) -> None:
        self._active = False
        for name, angle in angles.items():
            self._servos.set_angle(name, angle)

    def tick(self) -> dict[str, float] | None:
        """One control step; returns the angles written (None while idle)."""
        if self._runner is not None and self._runner.is_running:
            return None  # a scripted pose owns the servos right now
        if not self._active:
            return self._hold_level() if self._mode in _BALANCE_MODES else None
        vx, vy, yaw = self._command
        need_imu = self._policy is not None or self._mode in _BALANCE_MODES
        state = self._imu.update() if (self._imu is not None and need_imu) else None
        if self._policy is not None:
            joints = np.array(
                [self._servos.last_angle(n) or STAND_ANGLES[n] for n in SERVO_ORDER],
                dtype=np.float32,
            )
            angles = self._policy.step(
                joints,
                state.roll if state else 0.0,
                state.pitch if state else 0.0,
                state.gyro if state else (0.0, 0.0, 0.0),
                (vx, vy, yaw),
            )
        else:
            angles = self._cpg.angles_at(self._clock() - self._t0, vx, vy, yaw)
        if self._mode in _BALANCE_MODES and state is not None:
            angles = balance.correct(angles, state.roll, state.pitch, self._mode)
        for name, angle in angles.items():
            self._servos.set_angle(name, angle)
        return angles

    def _hold_level(self) -> dict[str, float] | None:
        if self._imu is None:
            return None
        state = self._imu.update()
        angles = balance.correct(dict(STAND_ANGLES), state.roll, state.pitch, self._mode)
        for name, angle in angles.items():
            self._servos.set_angle(name, angle)
        return angles

    async def run(self) -> None:
        """Drive ticks at rate_hz forever (owns the loop's timing)."""
        interval = 1.0 / self._rate_hz
        while True:
            started = self._clock()
            self.tick()
            elapsed = self._clock() - started
            await asyncio.sleep(max(0.0, interval - elapsed))
```

- [ ] **Step 4: Wire `runner` into the `GaitEngine` construction in `main.py`**

In `bridge/milo_bridge/main.py`, replace:

```python
    gait = GaitEngine(motion_servos, imu=imu, policy_path=POLICY_PATH)
```

with:

```python
    gait = GaitEngine(motion_servos, imu=imu, runner=runner, policy_path=POLICY_PATH)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/test_gait.py -v`
Expected: all tests PASS (old and new).

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/gait/engine.py bridge/milo_bridge/main.py bridge/tests/test_gait.py
git commit -m "feat(bridge): GaitEngine grows mode/reset/standby and IMU balance correction"
```

---

## Task 7: `MotionService.mode()` / `reset()` / `standby()`

**Files:**
- Modify: `bridge/milo_bridge/webapp/motion.py`
- Modify: `bridge/tests/webapp/fakes.py`
- Test: `bridge/tests/webapp/test_motion.py`

**Interfaces:**
- Consumes: `MODES` from `bridge/milo_bridge/gait/engine.py` (Task 6); `deps.gait.set_mode(name)`, `deps.gait.reset()`, `deps.gait.standby()`.
- Produces: `MotionService.mode(client_id: str, name: str) -> dict` (returns `{"ok": True, "mode": name}` or `{"error": ...}`); `MotionService.reset(client_id: str) -> dict`; `MotionService.standby(client_id: str) -> dict` (both return `{"ok": True}` or `{"error": ...}`) -- all three gated by the same `_denied()` control check as the existing motion commands, and never raise.

- [ ] **Step 1: Extend the test fakes**

In `bridge/tests/webapp/fakes.py`, replace:

```python
class FakeGait:
    backend = "cpg"

    def __init__(self):
        self.vel = (0.0, 0.0, 0.0)

    def set_velocity_command(self, vx, vy, yaw_rate):
        self.vel = (vx, vy, yaw_rate)
```

with:

```python
class FakeGait:
    backend = "cpg"

    def __init__(self):
        self.vel = (0.0, 0.0, 0.0)
        self.mode = "raw"
        self.reset_called = False
        self.standby_called = False

    def set_velocity_command(self, vx, vy, yaw_rate):
        self.vel = (vx, vy, yaw_rate)

    def set_mode(self, name):
        self.mode = name

    def reset(self):
        self.reset_called = True

    def standby(self):
        self.standby_called = True
```

- [ ] **Step 2: Write the failing tests**

Append to `bridge/tests/webapp/test_motion.py`:

```python
async def test_mode_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.mode("nobody", "balanced")
    assert res == {"error": "not-controlling"}
    assert deps.gait.mode == "raw"


async def test_mode_sets_valid_mode():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.mode("c1", "balanced") == {"ok": True, "mode": "balanced"}
    assert deps.gait.mode == "balanced"


async def test_mode_rejects_unknown_name():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.mode("c1", "sideways")
    assert "error" in res
    assert deps.gait.mode == "raw"


async def test_reset_requires_control_and_calls_gait():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.reset("nobody") == {"error": "not-controlling"}
    assert deps.gait.reset_called is False

    deps2 = _controlled_deps()
    svc2 = MotionService(deps2)
    assert await svc2.reset("c1") == {"ok": True}
    assert deps2.gait.reset_called is True


async def test_standby_requires_control_and_calls_gait():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.standby("nobody") == {"error": "not-controlling"}
    assert deps.gait.standby_called is False

    deps2 = _controlled_deps()
    svc2 = MotionService(deps2)
    assert await svc2.standby("c1") == {"ok": True}
    assert deps2.gait.standby_called is True


async def test_mode_reset_standby_never_raise_on_driver_error():
    class FailingGait:
        mode = "raw"

        def set_mode(self, name):
            raise RuntimeError("mode failed")

        def reset(self):
            raise RuntimeError("reset failed")

        def standby(self):
            raise RuntimeError("standby failed")

    deps = _controlled_deps()
    deps.gait = FailingGait()
    svc = MotionService(deps)
    assert "error" in await svc.mode("c1", "balanced")
    assert "error" in await svc.reset("c1")
    assert "error" in await svc.standby("c1")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest bridge/tests/webapp/test_motion.py -v`
Expected: the new tests FAIL with `AttributeError: 'MotionService' object has no attribute 'mode'` (etc.); existing tests still PASS.

- [ ] **Step 4: Add the three methods to `MotionService`**

In `bridge/milo_bridge/webapp/motion.py`, add the import:

```python
from ..drivers.servos import SERVO_CHANNELS
from ..poses import POSES
```

becomes:

```python
from ..drivers.servos import SERVO_CHANNELS
from ..gait.engine import MODES
from ..poses import POSES
```

Then insert the three new methods between `servo_batch` and `stop`. Replace:

```python
    async def stop(self) -> dict:
```

with:

```python
    async def mode(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if name not in MODES:
            return {"error": f"unknown mode {name!r}"}
        try:
            self._deps.gait.set_mode(name)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "mode": name}

    async def reset(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.gait.reset()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def standby(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.gait.standby()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def stop(self) -> dict:
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest bridge/tests/webapp/test_motion.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add bridge/milo_bridge/webapp/motion.py bridge/tests/webapp/fakes.py bridge/tests/webapp/test_motion.py
git commit -m "feat(bridge): MotionService.mode/reset/standby"
```

---

## Task 8: `ws.py` mode/reset/standby dispatch + broadcast

**Files:**
- Modify: `bridge/milo_bridge/webapp/ws.py`
- Test: `bridge/tests/webapp/test_ws.py`

**Interfaces:**
- Consumes: `MotionService.mode/reset/standby` (Task 7).
- Produces: WS message `{"t": "mode", "name": "raw"|"balanced"|"angled"}` in -> broadcast `{"t": "mode", "name": ...}` to every connected client out (mirrors `_broadcast_owner`'s pattern); WS messages `{"t": "reset"}` / `{"t": "standby"}` in -> `{"t": "ack", "for": "reset"|"standby"}` to the sender (mirrors the `servo`/`pose` pattern), or `{"t": "err", ...}` on failure.

- [ ] **Step 1: Write the failing tests**

Append to `bridge/tests/webapp/test_ws.py`:

```python
async def test_mode_broadcasts_to_all_clients():
    deps = make_deps(broker=ControlBroker())
    client, ws1 = await _ws(deps)
    try:
        ws2 = await client.ws_connect("/ws")
        await ws1.send_json({"t": "control", "take": True})
        await _recv_json_until(ws1, "control")
        await ws1.send_json({"t": "mode", "name": "balanced"})
        data1 = await _recv_json_until(ws1, "mode")
        data2 = await _recv_json_until(ws2, "mode")
        assert data1 == {"t": "mode", "name": "balanced"}
        assert data2 == {"t": "mode", "name": "balanced"}
        assert deps.gait.mode == "balanced"
    finally:
        await client.close()


async def test_mode_denied_without_control():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "mode", "name": "balanced"})
        data = await _recv_json_until(ws, "err")
        assert data["error"] == "not-controlling"
    finally:
        await client.close()


async def test_reset_and_standby_dispatch():
    deps = make_deps(broker=ControlBroker())
    client, ws = await _ws(deps)
    try:
        await ws.send_json({"t": "control", "take": True})
        await _recv_json_until(ws, "control")
        await ws.send_json({"t": "reset"})
        await _recv_json_until(ws, "ack")
        assert deps.gait.reset_called is True
        await ws.send_json({"t": "standby"})
        await _recv_json_until(ws, "ack")
        assert deps.gait.standby_called is True
    finally:
        await client.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest bridge/tests/webapp/test_ws.py -v`
Expected: the new tests FAIL (`{"t": "err", "for": "mode", "error": "unknown-type"}` instead of the expected messages); existing tests still PASS.

- [ ] **Step 3: Add the `mode`/`reset`/`standby` dispatch**

In `bridge/milo_bridge/webapp/ws.py`, replace:

```python
    if t == "stop":
        await motion.stop()
        await ws.send_json({"t": "ack", "for": "stop"})
        return
    if t == "audio":
        ws_state = app["ws_state"][ws]
        ws_state["audio_on"] = bool(data.get("on"))
        return
    handlers = {
        "gait": lambda: motion.gait(client_id, data.get("vx", 0), data.get("vy", 0), data.get("yaw", 0)),
        "pose": lambda: motion.pose(client_id, data.get("name", "")),
        "face": lambda: motion.face(client_id, data.get("name", "")),
        "servo": lambda: motion.servo(client_id, data.get("servo", ""), data.get("deg", 90)),
        "servo_batch": lambda: motion.servo_batch(client_id, data.get("angles", {})),
    }
```

with:

```python
    if t == "stop":
        await motion.stop()
        await ws.send_json({"t": "ack", "for": "stop"})
        return
    if t == "mode":
        res = await motion.mode(client_id, data.get("name", ""))
        if "error" in res:
            await ws.send_json({"t": "err", "for": "mode", "error": res["error"]})
        else:
            _broadcast_mode(app, res["mode"])
        return
    if t == "audio":
        ws_state = app["ws_state"][ws]
        ws_state["audio_on"] = bool(data.get("on"))
        return
    handlers = {
        "gait": lambda: motion.gait(client_id, data.get("vx", 0), data.get("vy", 0), data.get("yaw", 0)),
        "pose": lambda: motion.pose(client_id, data.get("name", "")),
        "face": lambda: motion.face(client_id, data.get("name", "")),
        "servo": lambda: motion.servo(client_id, data.get("servo", ""), data.get("deg", 90)),
        "servo_batch": lambda: motion.servo_batch(client_id, data.get("angles", {})),
        "reset": lambda: motion.reset(client_id),
        "standby": lambda: motion.standby(client_id),
    }
```

Then add `_broadcast_mode`, right after `_broadcast_owner`. Replace:

```python
def _broadcast_owner(app: web.Application) -> None:
    deps = app["deps"]
    owner = deps.broker.owner if deps.broker else "none"
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            you = bool(deps.broker and deps.broker.is_web_controller(state["id"]))
            asyncio.ensure_future(_send_safe(ws, {"t": "control", "owner": owner, "you": you}))
```

with:

```python
def _broadcast_owner(app: web.Application) -> None:
    deps = app["deps"]
    owner = deps.broker.owner if deps.broker else "none"
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            you = bool(deps.broker and deps.broker.is_web_controller(state["id"]))
            asyncio.ensure_future(_send_safe(ws, {"t": "control", "owner": owner, "you": you}))


def _broadcast_mode(app: web.Application, name: str) -> None:
    for ws, state in list(app["ws_state"].items()):
        if not ws.closed:
            asyncio.ensure_future(_send_safe(ws, {"t": "mode", "name": name}))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest bridge/tests/webapp/test_ws.py -v`
Expected: all tests PASS.

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add bridge/milo_bridge/webapp/ws.py bridge/tests/webapp/test_ws.py
git commit -m "feat(bridge): dispatch mode/reset/standby over the websocket, broadcast mode changes"
```

---

## Task 9: Tools — Reset + Standby buttons

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/servos.js`

**Interfaces:**
- Consumes: WS messages `{"t": "reset"}` / `{"t": "standby"}` (Task 8).
- Produces: no new exports; UI-only change to the existing `servos` panel.

- [ ] **Step 1: Replace the single "Center All" button with Reset + Standby**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/panels/servos.js` with:

```js
const SERVOS = ["R1", "R2", "R3", "R4", "L1", "L2", "L3", "L4"];

export default {
  id: "servos", title: "Servo Test", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = SERVOS.map((s) => `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:4px">
        <span style="width:26px;font-weight:600">${s}</span>
        <input type="range" min="0" max="180" value="90" data-servo="${s}" style="flex:1">
        <span data-val="${s}" style="width:34px;text-align:right">90°</span>
      </div>`).join("") +
      `<div style="display:flex;gap:8px;margin-top:8px">
        <button class="btn" id="reset" style="flex:1">Reset (90°)</button>
        <button class="btn" id="standby" style="flex:1">Standby</button>
      </div>`;
    el.querySelectorAll("input[type=range]").forEach((sl) => {
      sl.oninput = () => {
        el.querySelector(`[data-val="${sl.dataset.servo}"]`).textContent = `${sl.value}°`;
        bus.send({ t: "servo", servo: sl.dataset.servo, deg: Number(sl.value) });
      };
    });
    el.querySelector("#reset").onclick = () => {
      SERVOS.forEach((s) => {
        el.querySelector(`[data-servo="${s}"]`).value = 90;
        el.querySelector(`[data-val="${s}"]`).textContent = "90°";
      });
      bus.send({ t: "reset" });
    };
    el.querySelector("#standby").onclick = () => bus.send({ t: "standby" });
  },
};
```

- [ ] **Step 2: Run the static-integrity and webapp test suites**

Run: `pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all tests PASS (this file references no filenames, so it's unaffected — this just confirms nothing else broke).

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

Note: this is a UI-only change with no automated coverage in this repo. It needs a manual check on the real dashboard (drag a slider to 0/180 after the Task 1 fix and confirm the physical servo lands there; click Reset and Standby and confirm the robot settles smoothly) — that verification can't happen in this session.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/servos.js
git commit -m "feat(webapp): replace Center All with Reset + Standby buttons"
```

---

## Task 10: Move panel — mode selector + status line

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/js/panels/move.js`

**Interfaces:**
- Consumes: WS message `{"t": "mode", "name": ...}` in (Task 8), sends `{"t": "mode", "name": ...}` out.
- Produces: no new exports; UI-only change to the existing `move` panel.

- [ ] **Step 1: Add the mode selector and status line**

Replace the full contents of `bridge/milo_bridge/webapp/static/js/panels/move.js` with:

```js
const SEND_MS = 100;
const MODES = ["raw", "balanced", "angled"];
const MODE_LABEL = { raw: "Raw", balanced: "Balanced", angled: "Angled" };

export default {
  id: "move", title: "Move", needsControl: true,
  mount(el, { bus }) {
    el.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:14px;align-items:center">
        <div style="display:flex;gap:6px;width:100%;max-width:220px" id="mode-row">
          ${MODES.map((m) => `<button class="btn" data-mode="${m}" style="flex:1">${MODE_LABEL[m]}</button>`).join("")}
        </div>
        <div class="muted" id="mode-status">Mode: Raw</div>
        <div id="pad" style="width:100%;max-width:220px;aspect-ratio:1;border:1px solid var(--line);
             border-radius:8px;position:relative;touch-action:none">
          <div id="knob" style="position:absolute;width:26px;height:26px;border-radius:50%;
               background:var(--ink);left:calc(50% - 13px);top:calc(50% - 13px)"></div>
        </div>
        <div style="display:flex;flex-direction:column;gap:10px;width:100%;max-width:220px">
          <label>Speed <input id="speed" type="range" min="10" max="100" value="60"></label>
          <div class="muted">or WASD / arrows, Q/E to turn</div>
          <button class="btn danger" id="mstop">STOP</button>
        </div>
      </div>`;
    const pad = el.querySelector("#pad"), knob = el.querySelector("#knob");
    const speed = el.querySelector("#speed");
    const modeStatus = el.querySelector("#mode-status");
    let vec = { vx: 0, vy: 0, yaw: 0 }, timer = null;

    function setModeButtons(name) {
      el.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("active", b.dataset.mode === name));
      modeStatus.textContent = name === "raw" ? "Mode: Raw" : `Mode: ${MODE_LABEL[name]} — enabled`;
    }
    setModeButtons("raw");
    const offMode = bus.on("mode", (m) => setModeButtons(m.name));
    el.querySelectorAll("[data-mode]").forEach((b) => {
      b.onclick = () => bus.send({ t: "mode", name: b.dataset.mode });
    });

    function sending(active) {
      if (active && !timer) timer = setInterval(() => bus.send({ t: "gait", ...scaled() }), SEND_MS);
      if (!active && timer) { clearInterval(timer); timer = null; bus.send({ t: "gait", vx: 0, vy: 0, yaw: 0 }); }
    }
    const scaled = () => {
      const k = speed.value / 100;
      return { vx: vec.vx * k, vy: vec.vy * k, yaw: vec.yaw * 2 * k };
    };

    pad.addEventListener("pointerdown", (e) => {
      pad.setPointerCapture(e.pointerId);
      const rect = pad.getBoundingClientRect();
      const move = (ev) => {
        const x = Math.max(-1, Math.min(1, ((ev.clientX - rect.left) / rect.width) * 2 - 1));
        const y = Math.max(-1, Math.min(1, ((ev.clientY - rect.top) / rect.height) * 2 - 1));
        knob.style.left = `calc(${(x + 1) * 50}% - 13px)`;
        knob.style.top = `calc(${(y + 1) * 50}% - 13px)`;
        vec = { vx: -y, vy: x, yaw: 0 };
        sending(true);
      };
      const up = () => {
        pad.removeEventListener("pointermove", move);
        knob.style.left = "calc(50% - 13px)"; knob.style.top = "calc(50% - 13px)";
        vec = { vx: 0, vy: 0, yaw: 0 }; sending(false);
      };
      pad.addEventListener("pointermove", move);
      pad.addEventListener("pointerup", up, { once: true });
      move(e);
    });

    const keys = { w: [1,0,0], s: [-1,0,0], a: [0,-1,0], d: [0,1,0], q: [0,0,-1], e: [0,0,1],
      ArrowUp: [1,0,0], ArrowDown: [-1,0,0], ArrowLeft: [0,0,-1], ArrowRight: [0,0,1] };
    const down = new Set();
    const sync = () => {
      let vx = 0, vy = 0, yaw = 0;
      down.forEach((k) => { const [a,b,c] = keys[k]; vx += a; vy += b; yaw += c; });
      vec = { vx: Math.sign(vx), vy: Math.sign(vy), yaw: Math.sign(yaw) };
      sending(down.size > 0);
    };
    const kd = (e) => { if (keys[e.key] && !e.repeat && e.target.tagName !== "INPUT") { down.add(e.key); sync(); } };
    const ku = (e) => { if (keys[e.key]) { down.delete(e.key); sync(); } };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);

    el.querySelector("#mstop").onclick = () => bus.send({ t: "stop" });
    return () => {
      sending(false);
      offMode();
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
    };
  },
};
```

- [ ] **Step 2: Run the webapp test suite**

Run: `pytest bridge/tests -v`
Expected: all tests PASS.

Note: this is a UI-only change with no automated coverage in this repo. Manual check needed: click each mode button, confirm the status line updates and every open tab reflects the same mode (per Task 8's broadcast) — can't happen in this session.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/js/panels/move.js
git commit -m "feat(webapp): add Raw/Balanced/Angled mode selector to the Move panel"
```

---

## Task 11: Cockpit layout swap

**Files:**
- Modify: `bridge/milo_bridge/webapp/static/css/console.css`

**Interfaces:**
- None (pure CSS layout change; `#cockpit-move`/`#cockpit-camera`/`#cockpit-side` element IDs and their JS wiring in `layout.js`/`registry.js` are unchanged).

- [ ] **Step 1: Swap the grid columns and areas**

In `bridge/milo_bridge/webapp/static/css/console.css`, replace:

```css
#cockpit {
  display: grid;
  grid-template-columns: 280px 1fr 320px;
  grid-template-areas: "move camera side";
  gap: 16px;
  padding: 16px;
  align-items: start;
}
```

with:

```css
#cockpit {
  display: grid;
  grid-template-columns: 320px 1fr 280px;
  grid-template-areas: "side camera move";
  gap: 16px;
  padding: 16px;
  align-items: start;
}
```

Leave the mobile breakpoint (`@media (max-width: 900px)`, currently `grid-template-areas: "camera" "move" "side"`) unchanged — camera-first still reads best on a narrow screen.

- [ ] **Step 2: Run the static-integrity test**

Run: `pytest bridge/tests/webapp/test_static_integrity.py -v`
Expected: all tests PASS.

Note: this is a pure CSS change with no automated coverage in this repo. Manual check needed on the real dashboard to confirm Communication/Sensors now render on the left and Move on the right — can't happen in this session.

- [ ] **Step 3: Commit**

```bash
git add bridge/milo_bridge/webapp/static/css/console.css
git commit -m "feat(webapp): move the Move panel to the right, Communication/Sensors to the left"
```

---

## Final verification

- [ ] Run the complete suite once more from the repo root: `pytest bridge/tests -v`. Expected: all tests PASS, zero skips beyond the pre-existing `onnx`/`onnxruntime` `importorskip` in `test_gait.py`.
- [ ] On the real robot: verify the Tools > Servo Test sliders now drive each servo to its true physical 0°/180° with no jump; verify poses/gait/reset/standby motion is visibly smoother than before; verify Balanced mode visibly counters a manual tilt and Angled mode holds level on an incline, tuning `BalanceParams` in `gait/balance.py` (`roll_kp`, `pitch_kp`, `max_correction_deg`) if the correction is too weak, too strong, or backwards in sign.
