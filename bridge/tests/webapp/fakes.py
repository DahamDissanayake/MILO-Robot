"""Fake drivers for webapp tests — mirror only the methods the webapp uses."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from milo_bridge.config import BridgeConfig
from milo_bridge.crashlog import CrashLog
from milo_bridge.drivers.imu import ImuState
from milo_bridge.graph.api import GraphApi
from milo_bridge.graph.store import GraphStore
from milo_bridge.webapp.auth import hash_password
from milo_bridge.webapp.deps import WebDeps

TEST_USERNAME = "tester"
TEST_PASSWORD = "test-pw-12345"


class FakeGait:
    backend = "cpg"

    def __init__(self):
        self.vel = (0.0, 0.0, 0.0)
        self.mode = "raw"
        self.reset_called = False
        self.standby_called = False
        self.manual_on = False

    def set_velocity_command(self, vx, vy, yaw_rate):
        self.vel = (vx, vy, yaw_rate)

    def set_mode(self, name):
        self.mode = name

    def reset(self):
        self.reset_called = True

    def standby(self):
        self.standby_called = True

    def set_manual(self, on):
        self.manual_on = on


class FakeServos:
    def __init__(self):
        self.angles = {}
        self.relaxed = False
        self.held = False

    def set_angle(self, servo, angle):
        self.angles[servo] = angle

    async def set_pose(self, angles, stagger=True):
        self.angles.update(angles)

    def relax(self):
        self.relaxed = True

    def hold(self):
        self.held = True


class FakeRunner:
    def __init__(self):
        self.ran = []
        self.aborted = False

    async def run(self, name, cycles=2):
        self.ran.append(name)
        return True

    def abort(self):
        self.aborted = True


class FakeDisplay:
    def __init__(self):
        self.faces = []

    async def set_face(self, name, mode=None):
        self.faces.append(name)

    def start_idle(self):
        pass


class FakeAudio:
    def __init__(self, frames=(b"\x00\x02" * 160,)):
        self._frames = list(frames)
        self.played = []

    async def capture_frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)

    def play_pcm(self, pcm):
        self.played.append(pcm)


class FakeCamera:
    def __init__(self, frames=(b"jpeg-a", b"jpeg-b"), resolution="sd"):
        self._frames = list(frames)
        self.resolution = resolution

    async def frames(self):
        for f in self._frames:
            yield f
            await asyncio.sleep(0)

    def set_resolution(self, name):
        if name not in ("sd", "hd"):
            raise ValueError(f"unknown resolution {name!r}")
        self.resolution = name


class FakePeer:
    def __init__(self, id, name):
        self.id = id
        self.name = name


class FakeAdvertiser:
    def __init__(self):
        self.pairing = False
        self.advertised_ip = "192.168.1.15"


class FakePairingController:
    def __init__(self, advertiser):
        self._advertiser = advertiser
        self.current_pin: str | None = None
        self.entered = 0
        self.exited = 0

    async def enter_pairing_mode(self):
        self.entered += 1
        self.current_pin = "1234"
        self._advertiser.pairing = True
        return self.current_pin

    async def exit_pairing_mode(self):
        self.exited += 1
        self.current_pin = None
        self._advertiser.pairing = False


class FakeRobotServer:
    def __init__(self, paired=None):
        self.connected_brains: dict[str, FakePeer] = {}
        self.active_brain_id: str | None = None
        self.advertiser = FakeAdvertiser()
        self.pairing = FakePairingController(self.advertiser)
        self.port = 8765
        self._paired = paired or []

    def paired_brains(self):
        return self._paired

    def connected_brains_info(self):
        return [
            {"id": peer.id, "name": peer.name, "active": peer.id == self.active_brain_id}
            for peer in self.connected_brains.values()
        ]

    def set_active_brain(self, peer_id):
        if peer_id not in self.connected_brains:
            return False
        self.active_brain_id = peer_id
        return True

    def connect(self, peer: "FakePeer") -> None:
        """Test helper: simulate a brain connecting (mirrors what
        RobotServer._on_connection does on a successful handshake)."""
        self.connected_brains[peer.id] = peer
        if self.active_brain_id is None:
            self.active_brain_id = peer.id


class FakeImu:
    """Mirrors the real Mpu6050 driver's interface exactly: an `update()`
    method (not `read()` — a prior mismatch here masked a production bug
    where telemetry.py called a method the real driver doesn't have) that
    returns a real `ImuState` (a dataclass, not a plain dict)."""

    def __init__(self):
        self.zeroed = False

    def update(self) -> ImuState:
        return ImuState(pitch=1.0, roll=-2.0, yaw=15.0, gyro=(0.1, 0.2, 0.5), accel=(0.01, -0.02, 0.98))

    def zero(self) -> None:
        self.zeroed = True


def make_deps(**overrides) -> WebDeps:
    store = GraphStore(":memory:")
    deps = WebDeps(
        config=BridgeConfig(
            robot_id="milo-test", robot_name="milo",
            web_username=TEST_USERNAME, web_password_hash=hash_password(TEST_PASSWORD),
        ),
        runner=FakeRunner(),
        display=FakeDisplay(),
        servos=FakeServos(),
        camera=FakeCamera(),
        audio=FakeAudio(),
        imu=FakeImu(),
        gait=FakeGait(),
        graph_api=GraphApi(store),
        graph_store=store,
        broker=None,
        media_hub=None,
        log_buffer=None,
        crash_log=CrashLog(Path(tempfile.mkdtemp()) / "crashes.log"),
        hardware_status={"servos": True, "display": True, "imu": True, "camera": True, "audio": True},
        get_link_state=lambda: "disconnected",
    )
    for k, v in overrides.items():
        setattr(deps, k, v)
    return deps
