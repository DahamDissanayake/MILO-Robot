import asyncio
import json

import pytest

from milo_bridge.mcp.deps import McpDeps
from milo_bridge.mcp.server import build_mcp_server


class FakeGait:
    def __init__(self):
        self.velocity = None
        self.mode = "balanced"
        self.backend = "cpg"
        self.reset_called = False
        self.standby_called = False

    def set_velocity_command(self, vx, vy, yaw):
        self.velocity = (vx, vy, yaw)

    def set_mode(self, name):
        if name not in ("raw", "balanced", "angled"):
            raise ValueError(f"unknown mode {name!r}")
        self.mode = name

    def reset(self):
        self.reset_called = True

    def standby(self):
        self.standby_called = True


class FakeRunner:
    def __init__(self):
        self.ran: list[tuple[str, int | None]] = []
        self.aborted = False
        self.gate = None  # optional asyncio.Event to hold run() open

    async def run(self, name, cycles=None):
        self.ran.append((name, cycles))
        if self.gate is not None:
            await self.gate.wait()
        return True

    def abort(self):
        self.aborted = True


class FakeBroker:
    def __init__(self, allow=True):
        self._allow = allow
        self.owner = "none"

    def allow_brain_motion(self):
        return self._allow


class FakeServos:
    def __init__(self):
        self.relaxed = False
        self.held = False

    def relax(self):
        self.relaxed = True

    def hold(self):
        self.held = True


def make_deps(allow=True):
    return McpDeps(
        gait=FakeGait(), runner=FakeRunner(), imu=None, broker=FakeBroker(allow),
        servos=FakeServos(), display=None, audio=None,
    )


async def _call(server, tool_name, **kwargs):
    # Parameter deliberately named tool_name, not name -- several tools
    # (run_pose, set_mode, set_face) take a kwarg literally called `name`,
    # which would collide with (and shadow) a same-named parameter here.
    result = await server.call_tool(tool_name, kwargs)
    # run_pose/turn fire-and-forget their work via MovementGuard.start(),
    # which wraps the coroutine in asyncio.ensure_future(). A freshly
    # scheduled Task never runs any of its own code until the event loop
    # gets control back through an await -- and our tool coroutines return
    # immediately without yielding once the Task is created. Give the loop
    # one tick here so the fire-and-forget task's synchronous prefix (e.g.
    # FakeRunner.run() appending to `ran` before it awaits its gate) has
    # actually executed by the time callers inspect side effects. This is
    # in-process test plumbing only; it doesn't change the tools' contract.
    await asyncio.sleep(0)
    # FastMCP's call_tool() (mcp SDK 1.28.1) always runs with
    # convert_result=True. Because our tools are annotated `-> dict` (a bare
    # dict, not a pydantic model/TypedDict), FastMCP infers no structured
    # output schema for them, so convert_result falls back to *unstructured*
    # content: a list of ContentBlock (TextContent) objects whose `.text` is
    # the JSON-serialized dict our tool function actually returned, rather
    # than the dict itself. Unwrap that here so tests can keep asserting
    # against plain dicts -- the tool functions' return contract (they
    # return dicts) is unchanged.
    if isinstance(result, dict):
        return result
    (block,) = result
    return json.loads(block.text)


def test_walk_clamps_and_forwards_velocity():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "walk", vx=5.0, vy=-5.0, yaw_rate=100.0)
        assert result["ok"] is True
        assert deps.gait.velocity == (1.0, -1.0, 2.0)  # clamped to VX_LIM/VY_LIM/YAW_LIM

    asyncio.run(main())


def test_walk_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "walk", vx=0.1, vy=0.0, yaw_rate=0.0)
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.gait.velocity is None

    asyncio.run(main())


def test_run_pose_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "run_pose", name="wave")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.runner.ran == []

    asyncio.run(main())


def test_run_pose_rejects_unknown_name():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "run_pose", name="not-a-pose")
        assert result["ok"] is False and "unknown pose" in result["error"]

    asyncio.run(main())


def test_run_pose_starts_the_runner_and_returns_immediately():
    async def main():
        deps = make_deps()
        deps.runner.gate = asyncio.Event()  # keep the pose "running" so we can assert fire-and-forget
        server = build_mcp_server(deps)
        result = await _call(server, "run_pose", name="wave")
        assert result == {"ok": True}
        assert deps.runner.ran == [("wave", None)]
        assert deps.movement_guard.busy() is True
        deps.runner.gate.set()

    asyncio.run(main())


def test_run_pose_rejects_a_second_call_while_one_is_in_flight():
    async def main():
        deps = make_deps()
        deps.runner.gate = asyncio.Event()
        server = build_mcp_server(deps)
        await _call(server, "run_pose", name="wave")
        second = await _call(server, "run_pose", name="dance")
        assert second == {"ok": False, "error": "movement-in-progress"}
        deps.runner.gate.set()

    asyncio.run(main())


def test_turn_starts_the_continuous_turn_pose():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "turn", direction="left")
        assert result == {"ok": True}
        name, cycles = deps.runner.ran[0]
        assert name == "turn_left" and cycles == 10_000

    asyncio.run(main())


def test_turn_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "turn", direction="left")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.runner.ran == []

    asyncio.run(main())


def test_turn_rejects_bad_direction():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "turn", direction="sideways")
        assert result["ok"] is False

    asyncio.run(main())


def test_set_mode_validates_and_applies():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        ok = await _call(server, "set_mode", name="raw")
        assert ok == {"ok": True, "mode": "raw"}
        assert deps.gait.mode == "raw"
        bad = await _call(server, "set_mode", name="sideways")
        assert bad["ok"] is False

    asyncio.run(main())


def test_reset_and_standby_call_through_when_allowed():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        await _call(server, "reset")
        await _call(server, "standby")
        assert deps.gait.reset_called and deps.gait.standby_called

    asyncio.run(main())


def test_set_mode_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "set_mode", name="raw")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.gait.mode == "balanced"

    asyncio.run(main())


def test_reset_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "reset")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.gait.reset_called is False

    asyncio.run(main())


def test_standby_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "standby")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.gait.standby_called is False

    asyncio.run(main())


def test_relax_and_hold_call_through():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        await _call(server, "relax")
        await _call(server, "hold")
        assert deps.servos.relaxed and deps.servos.held

    asyncio.run(main())


def test_relax_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "relax")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.servos.relaxed is False

    asyncio.run(main())


def test_hold_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        server = build_mcp_server(deps)
        result = await _call(server, "hold")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.servos.held is False

    asyncio.run(main())


def test_stop_is_never_gated_and_aborts():
    async def main():
        deps = make_deps(allow=False)  # web controls -- stop must still work
        server = build_mcp_server(deps)
        result = await _call(server, "stop")
        assert result == {"ok": True}
        assert deps.gait.velocity == (0.0, 0.0, 0.0)
        assert deps.runner.aborted is True

    asyncio.run(main())


from dataclasses import dataclass


@dataclass
class FakeImuState:
    roll: float
    pitch: float
    yaw: float
    gyro: tuple
    accel: tuple


class FakeImu:
    def __init__(self, state):
        self._state = state

    def update(self):
        return self._state


class FakeDisplay:
    current_face = "idle"


def test_get_imu_state_reports_the_live_snapshot_and_is_never_gated():
    async def main():
        deps = make_deps(allow=False)  # web controls -- read must still work
        deps.imu = FakeImu(FakeImuState(roll=1.5, pitch=-2.0, yaw=10.0, gyro=(0, 0, 0), accel=(0, 0, 1)))
        server = build_mcp_server(deps)
        result = await _call(server, "get_imu_state")
        assert result == {
            "ok": True, "roll": 1.5, "pitch": -2.0, "yaw": 10.0,
            "gyro": [0, 0, 0], "accel": [0, 0, 1],
        }

    asyncio.run(main())


def test_get_imu_state_reports_unavailable_when_no_imu():
    async def main():
        deps = make_deps()
        server = build_mcp_server(deps)
        result = await _call(server, "get_imu_state")
        assert result == {"ok": False, "error": "imu unavailable"}

    asyncio.run(main())


def test_get_status_reports_mode_backend_owner_and_current_face():
    async def main():
        deps = make_deps(allow=False)
        deps.broker.owner = "web"
        deps.display = FakeDisplay()
        server = build_mcp_server(deps)
        result = await _call(server, "get_status")
        assert result == {
            "ok": True, "mode": "balanced", "backend": "cpg", "owner": "web",
            "moving": False, "current_face": "idle",
        }

    asyncio.run(main())


class FakeDisplayWithSet:
    def __init__(self):
        self.current_face = None
        self.requested: list[str] = []
        self.modes: list = []

    async def set_face(self, name, mode=None):
        self.requested.append(name)
        self.modes.append(mode)
        self.current_face = name


def test_set_face_calls_through_and_reports_the_actual_face():
    async def main():
        deps = make_deps()
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        result = await _call(server, "set_face", name="happy")
        assert result == {"ok": True, "face": "happy"}
        assert deps.display.requested == ["happy"]

    asyncio.run(main())


def test_set_face_accepts_talk_prefixed_names_for_the_reflex_caller():
    async def main():
        deps = make_deps()
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        result = await _call(server, "set_face", name="talk_happy")
        assert result == {"ok": True, "face": "talk_happy"}

    asyncio.run(main())


def test_set_face_loops_talk_prefixed_faces_and_plays_others_once():
    async def main():
        from milo_bridge.drivers.display import AnimMode

        deps = make_deps()
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        await _call(server, "set_face", name="talk_happy")
        await _call(server, "set_face", name="happy")
        assert deps.display.modes == [AnimMode.LOOP, AnimMode.ONCE]

    asyncio.run(main())


def test_set_face_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        deps.display = FakeDisplayWithSet()
        server = build_mcp_server(deps)
        result = await _call(server, "set_face", name="happy")
        assert result == {"ok": False, "error": "web-control-active"}
        assert deps.display.requested == []

    asyncio.run(main())


class FakeAudio:
    def __init__(self):
        self.played: list[bytes] = []

    def play_pcm(self, pcm):
        self.played.append(pcm)


def test_speak_synthesizes_and_plays(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()

        async def fake_synth(text, timeout_s=10.0):
            return b"pcmbytes"

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", fake_synth)

        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hello there")
        assert result == {"ok": True}
        assert deps.audio.played == [b"pcmbytes"]

    asyncio.run(main())


def test_speak_truncates_to_500_chars(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()
        seen = {}

        async def fake_synth(text, timeout_s=10.0):
            seen["text"] = text
            return b"x"

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", fake_synth)

        server = build_mcp_server(deps)
        await _call(server, "speak", text="a" * 600)
        assert len(seen["text"]) == 500

    asyncio.run(main())


def test_speak_reports_tts_failed_when_synth_raises(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()

        async def raising_synth(text, timeout_s=10.0):
            raise RuntimeError("espeak-ng vanished")

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", raising_synth)
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "tts-failed"}
        assert deps.audio.played == []

    asyncio.run(main())


def test_speak_denied_while_web_controls():
    async def main():
        deps = make_deps(allow=False)
        deps.audio = FakeAudio()
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "web-control-active"}

    asyncio.run(main())


def test_speak_reports_tts_unavailable(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()
        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: False)
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "tts-unavailable"}

    asyncio.run(main())


def test_speak_reports_synthesis_failure(monkeypatch):
    async def main():
        deps = make_deps()
        deps.audio = FakeAudio()

        async def fake_synth(text, timeout_s=10.0):
            return None

        monkeypatch.setattr("milo_bridge.mcp.server.tts_available", lambda: True)
        monkeypatch.setattr("milo_bridge.mcp.server.synth_pcm", fake_synth)
        server = build_mcp_server(deps)
        result = await _call(server, "speak", text="hi")
        assert result == {"ok": False, "error": "tts-failed"}

    asyncio.run(main())
