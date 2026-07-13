import asyncio

from milo_bridge.webapp.control import ControlBroker
from milo_bridge.webapp.motion import MotionService, list_faces
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
    name = next(iter(__import__("milo_bridge.poses", fromlist=["POSES"]).POSES))
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
    deps.display = None
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
    assert "dance" in names and "dance_1" not in names
    assert "cute" in names
