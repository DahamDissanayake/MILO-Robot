import asyncio

from milo_bridge import poses
from milo_bridge.poses import POSES, STAND_ANGLES, PoseRunner


class FakeServos:
    def __init__(self):
        self.angles: dict[str, float] = {}
        self.writes: list[dict[str, float]] = []

    async def set_pose(self, updates, stagger=True):
        self.angles.update(updates)
        self.writes.append(dict(updates))


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
    for name in ("wave", "dance", "bow", "rest", "stand"):
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
