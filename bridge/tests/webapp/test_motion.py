import asyncio

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.motion import MotionService, list_faces
from milo_bridge.poses import POSES
from .fakes import make_deps


def _controlled_deps():
    deps = make_deps(broker=ControlBroker())
    deps.broker.acquire_web("c1")
    return deps


async def test_gait_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.gait("nobody", 0.5, 0.0, 0.0)
    assert res == {"error": "not-controlling"}
    assert deps.gait.vel == (0.0, 0.0, 0.0)


async def test_gait_sets_velocity_and_clamps():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.gait("c1", 2.0, -2.0, 9.0) == {"ok": True}
    vx, vy, yaw = deps.gait.vel
    assert -1.0 <= vx <= 1.0 and -1.0 <= vy <= 1.0 and -2.0 <= yaw <= 2.0


async def test_gait_staleness_zeroes():
    deps = _controlled_deps()
    svc = MotionService(deps)
    await svc.gait("c1", 1.0, 0.0, 0.0)
    svc._last_cmd -= 1.0            # simulate 1 s silence
    svc._watchdog_tick()
    assert deps.gait.vel == (0.0, 0.0, 0.0)


async def test_pose_valid_and_invalid():
    deps = _controlled_deps()
    svc = MotionService(deps)
    name = next(iter(POSES))
    assert await svc.pose("c1", name) == {"ok": True}
    await asyncio.sleep(0)
    assert deps.runner.ran == [name]
    assert "error" in await svc.pose("c1", "no-such-pose")


async def test_servo_clamps_and_validates():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.servo("c1", "R1", 200) == {"ok": True}
    assert deps.servos.angles["R1"] == 180
    assert "error" in await svc.servo("c1", "R9", 90)


async def test_face_requires_display():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.face("c1", "cute") == {"ok": True}
    assert deps.display.faces == ["cute"]
    deps.hardware_status = {**deps.hardware_status, "display": False}
    assert "error" in await svc.face("c1", "cute")


async def test_stop_always_allowed():
    deps = make_deps(broker=ControlBroker())   # nobody controls
    svc = MotionService(deps)
    deps.gait.vel = (1.0, 0.0, 0.0)
    assert await svc.stop() == {"ok": True}
    assert deps.gait.vel == (0.0, 0.0, 0.0)
    assert deps.runner.aborted is True


def test_list_faces_groups_frames():
    names = list_faces()
    assert "idle_blink" in names and "idle_blink_1" not in names
    assert "happy" in names


async def test_servo_batch_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.servo_batch("nobody", {"R1": 90})
    assert res == {"error": "not-controlling"}
    assert deps.servos.angles == {}


async def test_servo_batch_writes_all_channels_in_one_call():
    deps = _controlled_deps()
    svc = MotionService(deps)
    angles = {"R1": 90, "R2": 90, "L1": 45, "L4": 120}
    assert await svc.servo_batch("c1", angles) == {"ok": True}
    assert deps.servos.angles == angles


async def test_servo_batch_clamps_every_angle():
    deps = _controlled_deps()
    svc = MotionService(deps)
    await svc.servo_batch("c1", {"R1": 400, "R2": -20})
    assert deps.servos.angles == {"R1": 180, "R2": 0}


async def test_servo_batch_rejects_whole_batch_on_unknown_channel():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.servo_batch("c1", {"R1": 90, "R9": 90})
    assert "error" in res
    assert deps.servos.angles == {}  # no partial write


async def test_handlers_never_raise_on_driver_error():
    """Handlers catch driver errors and return error dicts instead of raising."""

    # Test gait with failing gait driver
    class FakeGaitFailing:
        def set_velocity_command(self, vx, vy, yaw):
            raise RuntimeError("gait driver failed")

    deps = _controlled_deps()
    deps.gait = FakeGaitFailing()
    svc = MotionService(deps)
    result = await svc.gait("c1", 1.0, 0.0, 0.0)
    assert "error" in result
    assert "RuntimeError" in result["error"]

    # Test servo with failing servos driver
    class FakeServosFailing:
        def set_angle(self, servo, deg):
            raise RuntimeError("servo driver failed")

    deps = _controlled_deps()
    deps.servos = FakeServosFailing()
    svc = MotionService(deps)
    result = await svc.servo("c1", "R1", 90)
    assert "error" in result
    assert "RuntimeError" in result["error"]

    # Test stop with both drivers failing
    class FakeGaitFailingStop:
        def set_velocity_command(self, vx, vy, yaw):
            raise RuntimeError("gait failed")

    class FakeRunnerFailing:
        def abort(self):
            raise RuntimeError("runner failed")

    deps = _controlled_deps()
    deps.gait = FakeGaitFailingStop()
    deps.runner = FakeRunnerFailing()
    svc = MotionService(deps)
    result = await svc.stop()
    # Stop must always return {"ok": True}
    assert result == {"ok": True}


async def test_servo_batch_never_raises_on_bad_angle_value():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.servo_batch("c1", {"R1": "not-a-number"})
    assert "error" in res


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


async def test_restart_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.restart("nobody") == {"error": "not-controlling"}


async def test_restart_schedules_exit_when_controlling(monkeypatch):
    deps = _controlled_deps()
    svc = MotionService(deps)
    monkeypatch.setattr("milo_bridge.webapp.motion.RESTART_DELAY_S", 0.01)
    calls = []
    monkeypatch.setattr("milo_bridge.webapp.motion.os._exit", lambda code: calls.append(code))
    result = await svc.restart("c1")
    assert result == {"ok": True}
    await asyncio.sleep(0.05)
    assert calls == [0]


async def test_relax_requires_control_and_calls_servos():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.relax("nobody") == {"error": "not-controlling"}
    assert deps.servos.relaxed is False

    deps2 = _controlled_deps()
    svc2 = MotionService(deps2)
    assert await svc2.relax("c1") == {"ok": True}
    assert deps2.servos.relaxed is True


async def test_hold_requires_control_and_calls_servos():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    assert await svc.hold("nobody") == {"error": "not-controlling"}
    assert deps.servos.held is False

    deps2 = _controlled_deps()
    svc2 = MotionService(deps2)
    assert await svc2.hold("c1") == {"ok": True}
    assert deps2.servos.held is True


async def test_relax_and_hold_never_raise_on_driver_error():
    class FailingServos:
        def relax(self):
            raise RuntimeError("relax failed")

        def hold(self):
            raise RuntimeError("hold failed")

    deps = _controlled_deps()
    deps.servos = FailingServos()
    svc = MotionService(deps)
    assert "error" in await svc.relax("c1")
    assert "error" in await svc.hold("c1")


async def test_turn_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.turn("nobody", "left")
    assert res == {"error": "not-controlling"}
    assert deps.runner.ran == []


async def test_turn_runs_the_matching_pose_with_a_large_cycle_count():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.turn("c1", "left") == {"ok": True}
    await asyncio.sleep(0)
    assert deps.runner.ran == ["turn_left"]


async def test_turn_rejects_unknown_direction():
    deps = _controlled_deps()
    svc = MotionService(deps)
    res = await svc.turn("c1", "sideways")
    assert "error" in res
    assert deps.runner.ran == []


async def test_turn_shares_the_single_flight_guard_with_pose():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.turn("c1", "left") == {"ok": True}
    res = await svc.turn("c1", "right")
    assert res == {"error": "pose-running"}




async def test_manual_requires_control():
    deps = make_deps(broker=ControlBroker())
    svc = MotionService(deps)
    res = await svc.manual("nobody", True)
    assert res == {"error": "not-controlling"}
    assert deps.gait.manual_on is False


async def test_manual_on_sets_gait_and_aborts_runner():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.manual("c1", True) == {"ok": True, "on": True}
    assert deps.gait.manual_on is True
    assert deps.runner.aborted is True


async def test_manual_off_sets_gait_without_aborting():
    deps = _controlled_deps()
    svc = MotionService(deps)
    assert await svc.manual("c1", False) == {"ok": True, "on": False}
    assert deps.gait.manual_on is False
    assert deps.runner.aborted is False


async def test_manual_never_raises_on_driver_error():
    class FailingGait:
        def set_manual(self, on):
            raise RuntimeError("manual failed")

    deps = _controlled_deps()
    deps.gait = FailingGait()
    svc = MotionService(deps)
    assert "error" in await svc.manual("c1", True)
