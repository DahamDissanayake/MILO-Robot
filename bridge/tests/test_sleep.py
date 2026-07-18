import asyncio

from milo_bridge.sleep import SleepController


class FakeRunner:
    def __init__(self):
        self.ran: list[str] = []
        self.aborted = 0

    def abort(self):
        self.aborted += 1

    async def run(self, name, cycles=4):
        self.ran.append(name)
        return True


class FakeDisplay:
    def __init__(self):
        self.faces: list[str] = []
        self.idle = False

    async def set_face(self, name, mode, fps=8.0):
        self.faces.append(name)

    def start_idle(self):
        self.idle = True

    def stop_idle(self):
        self.idle = False


class FakeServos:
    def __init__(self):
        self.relaxed = 0

    def relax(self):
        self.relaxed += 1


class FakeGait:
    def __init__(self):
        self.suspended_calls: list[bool] = []

    def set_suspended(self, on):
        self.suspended_calls.append(on)


def test_sleep_then_wake_sequence():
    runner, display, servos = FakeRunner(), FakeDisplay(), FakeServos()
    ctl = SleepController(runner, display, servos=servos)

    async def run():
        await ctl.ensure_asleep()
        assert ctl.asleep
        assert runner.ran == ["rest"]
        assert display.faces[-1] == "sleepy"
        assert servos.relaxed == 1
        await ctl.ensure_asleep()  # idempotent
        assert runner.ran == ["rest"]

        await ctl.ensure_awake()
        assert not ctl.asleep
        assert runner.ran == ["rest", "stand"]
        assert display.faces[-1] == "excited"
        assert display.idle
        await ctl.ensure_awake()  # idempotent
        assert runner.ran == ["rest", "stand"]

    asyncio.run(run())


def test_ensure_asleep_suspends_gait_before_relaxing_and_wake_resumes_it():
    # Regression: hold-level self-leveling was re-engaging (and re-driving)
    # servos the instant relax() went limp, defeating asleep power saving.
    # set_suspended(True) must be called before relax(), and lifted again
    # on wake.
    runner, display, servos, gait = FakeRunner(), FakeDisplay(), FakeServos(), FakeGait()
    ctl = SleepController(runner, display, servos=servos, gait=gait)

    async def run():
        await ctl.ensure_asleep()
        assert gait.suspended_calls == [True]
        await ctl.ensure_awake()
        assert gait.suspended_calls == [True, False]

    asyncio.run(run())


def test_loud_sound_perks_up_only_while_asleep():
    runner, display = FakeRunner(), FakeDisplay()
    perks: list[int] = []
    ctl = SleepController(
        runner, display, loud_rms_threshold=1000, on_perk=lambda: perks.append(1)
    )

    async def run():
        # Awake: loud sounds are ignored.
        ctl.handle_audio_level(5000)
        await asyncio.sleep(0)
        assert perks == []

        await ctl.ensure_asleep()
        ctl.handle_audio_level(500)  # quiet: ignored
        await asyncio.sleep(0)
        assert perks == []

        ctl.handle_audio_level(5000)  # loud: perk
        await asyncio.sleep(0.01)
        assert perks == [1]
        assert "surprised" in display.faces
        ctl._cancel_perk()

    asyncio.run(run())
