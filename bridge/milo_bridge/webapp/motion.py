"""Motion commands from web clients: control-checked, clamped, stale-safed."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from ..drivers.servos import SERVO_CHANNELS
from ..gait.engine import MODES
from ..poses import POSES

log = logging.getLogger(__name__)

STALE_S = 0.5
RESTART_DELAY_S = 0.3  # gives the WS ack a moment to flush before the process exits
ASSETS_FACES = Path(__file__).resolve().parents[2] / "assets" / "faces"
HOLD_CYCLES = 10_000  # effectively "until aborted" -- matches this codebase's own test idiom

VX_LIM, VY_LIM, YAW_LIM = 1.0, 1.0, 2.0
DEG_MIN, DEG_MAX = 0, 180


def list_faces() -> list[str]:
    names = set()
    for p in sorted(ASSETS_FACES.glob("*.png")):
        names.add(re.sub(r"_\d+$", "", p.stem))
    return sorted(names)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _log_pose_result(task):
    exc = task.exception() if not task.cancelled() else None
    if exc:
        log.error("pose failed: %s", exc)


class MotionService:
    def __init__(self, deps):
        self._deps = deps
        self._last_cmd = 0.0
        self._moving = False
        self._task: asyncio.Task | None = None
        self._pose_task: asyncio.Task | None = None

    # -- control gate ------------------------------------------------------
    def _denied(self, client_id: str) -> dict | None:
        broker = self._deps.broker
        if broker is None or not broker.is_web_controller(client_id):
            return {"error": "not-controlling"}
        return None

    # -- commands ----------------------------------------------------------
    async def gait(self, client_id: str, vx: float, vy: float, yaw: float) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.gait.set_velocity_command(
                _clamp(vx, -VX_LIM, VX_LIM), _clamp(vy, -VY_LIM, VY_LIM),
                _clamp(yaw, -YAW_LIM, YAW_LIM))
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        self._last_cmd = time.monotonic()
        self._moving = (vx, vy, yaw) != (0.0, 0.0, 0.0)
        return {"ok": True}

    async def pose(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if name not in POSES:
            return {"error": f"unknown pose {name!r}"}
        if self._pose_task is not None and not self._pose_task.done():
            return {"error": "pose-running"}
        self._pose_task = asyncio.ensure_future(self._deps.runner.run(name))
        self._pose_task.add_done_callback(_log_pose_result)
        return {"ok": True}

    async def face(self, client_id: str, name: str) -> dict:
        if err := self._denied(client_id):
            return err
        if not self._deps.hardware_status.get("display", True):
            return {"error": "display unavailable"}
        try:
            await self._deps.display.set_face(name)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def servo(self, client_id: str, servo: str, deg: float) -> dict:
        if err := self._denied(client_id):
            return err
        if servo not in SERVO_CHANNELS:
            return {"error": f"unknown servo {servo!r}"}
        try:
            self._deps.servos.set_angle(servo, _clamp(deg, DEG_MIN, DEG_MAX))
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def servo_batch(self, client_id: str, angles: dict[str, float]) -> dict:
        if err := self._denied(client_id):
            return err
        bad = [name for name in angles if name not in SERVO_CHANNELS]
        if bad:
            return {"error": f"unknown servo(s) {bad!r}"}
        try:
            clamped = {name: _clamp(deg, DEG_MIN, DEG_MAX) for name, deg in angles.items()}
            await self._deps.servos.set_pose(clamped, stagger=True)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

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

    async def restart(self, client_id: str) -> dict:
        """Cleanly exit so systemd's Restart=always brings the service back
        with every I2C driver freshly re-probed -- the recovery path for a
        peripheral that was unplugged and replugged."""
        if err := self._denied(client_id):
            return err
        log.warning("restart requested by %s — exiting for systemd to restart with fresh hardware", client_id)
        asyncio.get_running_loop().call_later(RESTART_DELAY_S, os._exit, 0)
        return {"ok": True}

    async def relax(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.servos.relax()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def hold(self, client_id: str) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.servos.hold()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    async def manual(self, client_id: str, on: bool) -> dict:
        if err := self._denied(client_id):
            return err
        try:
            self._deps.gait.set_manual(on)
            if on:
                self._deps.runner.abort()
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "on": on}

    async def turn(self, client_id: str, direction: str) -> dict:
        if err := self._denied(client_id):
            return err
        if direction not in ("left", "right"):
            return {"error": f"unknown turn direction {direction!r}"}
        if self._pose_task is not None and not self._pose_task.done():
            return {"error": "pose-running"}
        self._pose_task = asyncio.ensure_future(self._deps.runner.run(f"turn_{direction}", cycles=HOLD_CYCLES))
        self._pose_task.add_done_callback(_log_pose_result)
        return {"ok": True}

    async def stop(self) -> dict:
        """Emergency stop: anyone, anytime."""
        # STOP must attempt every action and never raise.
        try:
            self._deps.gait.set_velocity_command(0.0, 0.0, 0.0)
        except Exception as exc:
            log.exception("stop: gait.set_velocity_command failed")
        self._moving = False
        try:
            self._deps.runner.abort()
        except Exception as exc:
            log.exception("stop: runner.abort failed")
        return {"ok": True}

    # -- staleness watchdog --------------------------------------------------
    def _watchdog_tick(self) -> None:
        if self._moving and time.monotonic() - self._last_cmd > STALE_S:
            log.info("gait command stale — zeroing velocity")
            try:
                self._deps.gait.set_velocity_command(0.0, 0.0, 0.0)
            except Exception as exc:
                log.exception("_watchdog_tick: zeroing failed")
            self._moving = False

    async def _watchdog(self) -> None:
        while True:
            self._watchdog_tick()
            await asyncio.sleep(0.1)

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.ensure_future(self._watchdog())

    def stop_watchdog(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
