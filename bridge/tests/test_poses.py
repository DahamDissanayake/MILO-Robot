import asyncio

import pytest

from milo_bridge import poses
from milo_bridge.poses import POSES, REST_ANGLES, STAND_ANGLES, PoseRunner


class FakeServos:
    def __init__(self):
        self.angles: dict[str, float] = {}
        self.writes: list[dict[str, float]] = []

    async def set_pose(self, updates, stagger=True):
        self.angles.update(updates)
        self.writes.append(dict(updates))


class FakeSmoothServos:
    """Mimics SmoothServos' async interface closely enough to exercise
    PoseRunner's settle-time computation: set_pose only records a target,
    it does not move ``last_angle`` -- matching the real system where the
    slew layer hasn't caught up to a just-issued target yet."""

    def __init__(self, slew_deg_per_s=300.0, start_angles=None):
        self.slew_deg_per_s = slew_deg_per_s
        self._angles: dict[str, float] = dict(start_angles or {})
        self.writes: list[dict[str, float]] = []

    async def set_pose(self, updates, stagger=True):
        self.writes.append(dict(updates))

    def last_angle(self, name):
        return self._angles.get(name)


class FakeDisplay:
    def __init__(self):
        self.faces: list[str] = []
        self.idle = False

    async def set_face(self, name, mode, fps=8.0):
        self.faces.append(name)

    def start_idle(self):
        self.idle = True


async def no_sleep(_s):
    pass


def run_pose(name, **kwargs):
    servos, display = FakeServos(), FakeDisplay()
    runner = PoseRunner(servos, display, sleep=no_sleep)
    completed = asyncio.run(runner.run(name, **kwargs))
    return servos, display, completed


def test_rest_sets_all_90():
    servos, display, completed = run_pose("rest")
    assert completed
    assert servos.angles == {n: 90 for n in ("R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4")}
    assert display.faces == ["rest"]


def test_stand_matches_firmware_angles():
    servos, _, _ = run_pose("stand")
    assert servos.angles == STAND_ANGLES


def test_wave_ends_standing_and_enters_idle():
    servos, display, completed = run_pose("wave")
    assert completed
    assert servos.angles == STAND_ANGLES  # final stand
    assert display.faces[0] == "wave"
    assert display.idle


def test_walk_runs_requested_cycles():
    servos, _, completed = run_pose("walk", cycles=3)
    assert completed
    walk = POSES["walk"]
    # entry steps + cycles + final stand
    assert len(servos.writes) == len(walk.steps) + 3 * len(walk.cycle) + 1


def test_all_poses_use_known_servo_names_and_valid_angles():
    valid = set(STAND_ANGLES)
    for pose in POSES.values():
        for step in pose.steps + pose.cycle:
            assert set(step.updates) <= valid, pose.name
            assert all(0 <= a <= 180 for a in step.updates.values()), pose.name


def test_abort_interrupts_and_recovers_to_stand():
    servos, display = FakeServos(), FakeDisplay()

    async def yielding_sleep(_s):
        await asyncio.sleep(0)  # suspension point so abort can land mid-gait

    runner = PoseRunner(servos, display, sleep=yielding_sleep)

    async def run():
        task = asyncio.create_task(runner.run("walk", cycles=10_000))
        for _ in range(10):  # let a few steps execute
            await asyncio.sleep(0)
        runner.abort()
        return await task

    completed = asyncio.run(run())
    assert not completed
    assert servos.angles["R1"] == STAND_ANGLES["R1"]  # recovered to stand
    assert not display.idle  # aborted runs don't enter idle


def test_gaits_have_cycles_and_oneshots_do_not():
    for name in ("walk", "walk_backward", "turn_left", "turn_right"):
        assert POSES[name].cycle
    for name in ("wave", "dance", "bow", "rest", "stand", "crab", "look_up", "look_down"):
        assert not POSES[name].cycle


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


def test_wake_up_ends_at_stand():
    servos, _, completed = run_pose("wake_up")
    assert completed
    assert servos.angles == STAND_ANGLES


def test_look_up_holds_its_tilt_instead_of_returning_to_stand():
    servos, _, completed = run_pose("look_up")
    assert completed
    assert servos.angles["R2"] == 0
    assert servos.angles["L2"] == 180
    assert servos.angles["L4"] == 90
    assert servos.angles["R4"] == 90
    assert servos.angles["R1"] == STAND_ANGLES["R1"]  # front hips untouched by look_up


def test_look_down_holds_its_tilt_instead_of_returning_to_stand():
    servos, _, completed = run_pose("look_down")
    assert completed
    assert servos.angles["L1"] == 0
    assert servos.angles["R1"] == 180
    assert servos.angles["L3"] == 90
    assert servos.angles["R3"] == 90
    assert servos.angles["R2"] == STAND_ANGLES["R2"]  # front knees untouched by look_down


def test_look_up_and_down_are_distinct():
    up, down = POSES["look_up"], POSES["look_down"]
    assert up.steps != down.steps


# --- settle-time (slew-aware wait) -------------------------------------------

def test_rest_pose_waits_for_the_slew_even_though_wait_ms_is_zero():
    # Regression for the boot-reboot bug: "rest"'s only step has wait_ms=0
    # (fine on the old firmware's instant-snap servos), but on SmoothServos
    # a 90deg move away from REST_ANGLES's 90 needs real time to land --
    # PoseRunner must wait for it instead of returning immediately.
    sleeps: list[float] = []

    async def spy_sleep(s):
        sleeps.append(s)

    servos = FakeSmoothServos(slew_deg_per_s=300.0, start_angles=dict.fromkeys(STAND_ANGLES, 0.0))
    runner = PoseRunner(servos, FakeDisplay(), sleep=spy_sleep)
    completed = asyncio.run(runner.run("rest"))
    assert completed
    assert sleeps == [pytest.approx(90 / 300.0)]


def test_settle_time_extends_a_wait_ms_too_short_for_the_commanded_move():
    sleeps: list[float] = []

    async def spy_sleep(s):
        sleeps.append(s)

    # cute's Step({"L2": 160, "R2": 20, "R4": 180, "L4": 0}, 0) commands
    # R4 0->180 (a 180deg move) with wait_ms=0 -- ported from firmware
    # where writes were instant. Starting at STAND_ANGLES (R4=0) means the
    # entry stand-step is a no-op, isolating this step's settle time.
    servos = FakeSmoothServos(slew_deg_per_s=300.0, start_angles=dict(STAND_ANGLES))
    runner = PoseRunner(servos, FakeDisplay(), sleep=spy_sleep)
    asyncio.run(runner.run("cute"))
    # The R4 0->180 move (delta 180) must wait at least 180/300s = 0.6s,
    # not the pose-authored 0ms.
    assert any(s >= 180 / 300.0 - 1e-9 for s in sleeps)


def test_settle_time_does_not_shorten_an_explicit_longer_hold():
    sleeps: list[float] = []

    async def spy_sleep(s):
        sleeps.append(s)

    # dance's entry step matches its own starting angles exactly (delta 0
    # -- settle time is 0) but is authored with a 300ms hold; that
    # intentional pause must survive untouched, not get zeroed out.
    servos = FakeSmoothServos(
        slew_deg_per_s=300.0,
        start_angles={"R1": 90, "R2": 90, "L1": 90, "L2": 90, "R4": 160, "R3": 160, "L3": 10, "L4": 10},
    )
    runner = PoseRunner(servos, FakeDisplay(), sleep=spy_sleep)
    asyncio.run(runner.run("dance", cycles=1))
    assert sleeps[0] == pytest.approx(0.3)


def test_settle_time_is_a_noop_for_servos_without_slew_info():
    # Plain FakeServos (used by every other test in this file) has neither
    # last_angle nor slew_deg_per_s -- PoseRunner must fall back to the
    # authored wait_ms unchanged rather than erroring.
    servos, display, completed = run_pose("rest")
    assert completed
    assert servos.angles == REST_ANGLES
