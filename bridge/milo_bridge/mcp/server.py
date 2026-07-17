"""Movement, face, speech & IMU MCP server -- the bridge's tool surface for
the brain's tool-calling LLM (and, for manual testing, a human's MCP
client). Every gated tool honors the same ControlBroker a web client
already does (see webapp/motion.py); run_pose/turn share one MovementGuard
so overlapping callers serialize instead of racing the same PoseRunner.
"""
from __future__ import annotations

from ..poses import POSES
from ..webapp.api.speak import synth_pcm, tts_available
from .deps import McpDeps

TURN_HOLD_CYCLES = 10_000  # matches webapp/motion.py's "continuous until aborted" idiom
VX_LIM, VY_LIM, YAW_LIM = 1.0, 1.0, 2.0


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def build_mcp_server(deps: McpDeps):
    from mcp.server.fastmcp import FastMCP

    server = FastMCP("milo-movement")

    @server.tool()
    async def walk(vx: float, vy: float, yaw_rate: float) -> dict:
        """Continuous velocity walk: vx/vy in m/s, yaw_rate in deg/s. (0,0,0) stops walking."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.gait.set_velocity_command(
            _clamp(vx, -VX_LIM, VX_LIM), _clamp(vy, -VY_LIM, VY_LIM), _clamp(yaw_rate, -YAW_LIM, YAW_LIM)
        )
        return {"ok": True}

    @server.tool()
    async def run_pose(name: str, cycles: int | None = None) -> dict:
        """Run a scripted pose/gait by name (wave, dance, bow, point, pushup,
        swim, cute, freaky, worm, shake, shrug, dead, wake_up, crab, look_up,
        look_down, rest, stand, walk, walk_backward, turn_left, turn_right)."""
        if name not in POSES:
            return {"ok": False, "error": f"unknown pose {name!r}"}
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        if deps.movement_guard.busy():
            return {"ok": False, "error": "movement-in-progress"}
        kwargs = {} if cycles is None else {"cycles": cycles}
        deps.movement_guard.start(deps.runner.run(name, **kwargs))
        return {"ok": True}

    @server.tool()
    async def turn(direction: str) -> dict:
        """Turn continuously left or right until stop() is called."""
        if direction not in ("left", "right"):
            return {"ok": False, "error": f"unknown direction {direction!r}"}
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        if deps.movement_guard.busy():
            return {"ok": False, "error": "movement-in-progress"}
        deps.movement_guard.start(deps.runner.run(f"turn_{direction}", cycles=TURN_HOLD_CYCLES))
        return {"ok": True}

    @server.tool()
    async def set_mode(name: str) -> dict:
        """Set the gait mode: raw, balanced, or angled."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        try:
            deps.gait.set_mode(name)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "mode": name}

    @server.tool()
    async def reset() -> dict:
        """Smoothly return every servo to the 90-degree rest angles."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.gait.reset()
        return {"ok": True}

    @server.tool()
    async def standby() -> dict:
        """Smoothly return every servo to the stand pose."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.gait.standby()
        return {"ok": True}

    @server.tool()
    async def relax() -> dict:
        """Stop driving all servos (they go limp)."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.servos.relax()
        return {"ok": True}

    @server.tool()
    async def hold() -> dict:
        """Re-engage every servo at the angle it was commanded to right before the last relax()."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        deps.servos.hold()
        return {"ok": True}

    @server.tool()
    async def stop() -> dict:
        """Emergency stop: always allowed, regardless of who holds control."""
        deps.gait.set_velocity_command(0.0, 0.0, 0.0)
        deps.runner.abort()
        return {"ok": True}

    @server.tool()
    async def get_imu_state() -> dict:
        """Live IMU snapshot: roll/pitch/yaw in degrees, gyro in deg/s, accel in g. Never gated."""
        if deps.imu is None:
            return {"ok": False, "error": "imu unavailable"}
        state = deps.imu.update()
        return {
            "ok": True, "roll": state.roll, "pitch": state.pitch, "yaw": state.yaw,
            "gyro": list(state.gyro), "accel": list(state.accel),
        }

    @server.tool()
    async def get_status() -> dict:
        """Gait mode/backend, who holds control, whether a movement is in
        flight, and the current face. Never gated."""
        return {
            "ok": True,
            "mode": deps.gait.mode,
            "backend": deps.gait.backend,
            "owner": deps.broker.owner,
            "moving": deps.movement_guard.busy(),
            "current_face": deps.display.current_face if deps.display is not None else None,
        }

    @server.tool()
    async def set_face(name: str) -> dict:
        """Show a preset face expression: happy, sad, angry, surprised,
        sleepy, love, excited, confused, thinking, or idle."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        await deps.display.set_face(name)
        return {"ok": True, "face": deps.display.current_face}

    @server.tool()
    async def speak(text: str) -> dict:
        """Say something out loud right now, independent of the normal
        spoken conversational reply -- for something unprompted."""
        if not deps.broker.allow_brain_motion():
            return {"ok": False, "error": "web-control-active"}
        if deps.audio is None:
            return {"ok": False, "error": "audio unavailable"}
        if not tts_available():
            return {"ok": False, "error": "tts-unavailable"}
        clean = text[:500].strip()
        if not clean:
            return {"ok": False, "error": "empty text"}
        pcm = await synth_pcm(clean)
        if pcm is None:
            return {"ok": False, "error": "tts-failed"}
        deps.audio.play_pcm(pcm)
        return {"ok": True}

    return server
