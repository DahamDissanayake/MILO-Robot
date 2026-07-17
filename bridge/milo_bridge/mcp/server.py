"""Movement, face, speech & IMU MCP server -- the bridge's tool surface for
the brain's tool-calling LLM (and, for manual testing, a human's MCP
client). Every gated tool honors the same ControlBroker a web client
already does (see webapp/motion.py); run_pose/turn share one MovementGuard
so overlapping callers serialize instead of racing the same PoseRunner.
"""
from __future__ import annotations

from ..poses import POSES
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

    return server
